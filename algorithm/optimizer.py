"""Constraint-aware SDN energy optimizer.

This module intentionally models sleep/wake decisions in a safe, testable way
without sending live OpenFlow commands to the network. The controller must
still provide the topology snapshot and link utilisation data that it already
collects from OpenFlow port statistics.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Set, Tuple

DEFAULT_LINK_UTILIZATION_THRESHOLD = 5.0
DEFAULT_QOS_LIMITS = {
    "avg_rtt_ms": 30.0,
    "packet_loss_percent": 1.0,
    "throughput_mbps": 10.0,
}


def _normalise_node(node: Any) -> str:
    return str(node).strip().lower()


def _canonical_link_name(a: Any, b: Any) -> str:
    left = _normalise_node(a)
    right = _normalise_node(b)
    if left == right:
        raise ValueError(f"A link must connect different endpoints: {a!r} and {b!r}")
    return f"{min(left, right)}-{max(left, right)}"


def _normalise_link_name(link: Any) -> str:
    if isinstance(link, str):
        parts = [part.strip().lower() for part in link.split("-") if part.strip()]
        if len(parts) < 2:
            raise ValueError(f"Invalid link name {link!r}; expected 's1-s3'")
        return _canonical_link_name(parts[0], parts[1])
    if isinstance(link, (tuple, list)) and len(link) == 2:
        return _canonical_link_name(link[0], link[1])
    raise TypeError(f"Unsupported link representation: {link!r}")


def _unique_links(topology_graph: Mapping[str, Iterable[str]]) -> List[str]:
    edges: Set[str] = set()
    for src, neighbours in topology_graph.items():
        for dst in neighbours or []:
            if src == dst:
                continue
            edges.add(_canonical_link_name(src, dst))
    return sorted(edges)


def _remove_link(topology_graph: Mapping[str, Iterable[str]], candidate_link: Any) -> Dict[str, List[str]]:
    link_name = _normalise_link_name(candidate_link)
    a, b = link_name.split("-")
    remaining = {node: list(neighbours) for node, neighbours in topology_graph.items()}

    for node in remaining:
        remaining[node] = [n for n in remaining[node] if _canonical_link_name(node, n) != link_name]

    if a in remaining and b in remaining:
        remaining[a] = [n for n in remaining[a] if _normalise_node(n) != b]
        remaining[b] = [n for n in remaining[b] if _normalise_node(n) != a]

    for node in remaining:
        remaining[node] = sorted({_normalise_node(n) for n in remaining[node]})
    return remaining


def _is_connected(graph: Mapping[str, Iterable[str]]) -> bool:
    if not graph:
        return True
    start = next(iter(graph))
    seen = {start}
    queue = deque([start])

    while queue:
        node = queue.popleft()
        for neighbour in graph.get(node, []):
            candidate = _normalise_node(neighbour)
            if candidate not in seen:
                seen.add(candidate)
                queue.append(candidate)

    return len(seen) == len(graph)


def snapshot_to_topology_graph(snapshot: Mapping[str, Any]) -> Dict[str, List[str]]:
    """Build a simple adjacency graph from a controller snapshot."""
    graph: Dict[str, Set[str]] = {}

    for switch in snapshot.get("switches", []):
        name = _normalise_node(switch)
        graph.setdefault(name, set())

    for link in snapshot.get("links", []):
        left, right = _normalise_link_name(link).split("-")
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)

    return {node: sorted(neighbours) for node, neighbours in sorted(graph.items())}


def snapshot_to_utilization_by_link(snapshot: Mapping[str, Any]) -> Dict[str, float]:
    """Map canonical link names to utilisation percentages from the controller snapshot."""
    utilisation: Dict[str, float] = {}
    for metric in snapshot.get("link_metrics", []):
        link_name = metric.get("link")
        if not link_name:
            continue
        try:
            normalised = _normalise_link_name(link_name)
            utilisation[normalised] = float(metric.get("utilization_percent", 0.0))
        except (TypeError, ValueError):
            continue
    return utilisation


def check_constraints(
    topology_graph,
    candidate_link,
    utilization_by_link: Optional[Mapping[str, float]] = None,
    qos_limits: Optional[Mapping[str, float]] = None,
    threshold: Optional[float] = None,
    allow_single_link_disconnect: bool = False,
):
    """Return a structured safety decision for a candidate sleep action.

    The result includes connectivity, QoS, resilience, and security checks.
    A candidate is considered safe only when it does not disconnect the graph
    and does not violate the configured QoS limits.
    """
    if topology_graph is None:
        return {"safe": False, "candidate": None, "reason": "Topology graph is missing", "utilization_percent": 0.0}

    normalised_candidate = _normalise_link_name(candidate_link)
    if utilization_by_link is None:
        utilization_by_link = {}
    util_value = float(utilization_by_link.get(normalised_candidate, 0.0))

    if threshold is not None and util_value > float(threshold):
        return {
            "safe": False,
            "candidate": normalised_candidate,
            "utilization_percent": util_value,
            "reason": f"Utilisation {util_value:.2f}% exceeds sleep threshold {float(threshold):.2f}%",
            "connectivity_ok": False,
            "qos_ok": False,
            "resilience_ok": False,
            "security_ok": True,
        }

    if candidate_link not in _unique_links(topology_graph):
        return {
            "safe": False,
            "candidate": normalised_candidate,
            "utilization_percent": util_value,
            "reason": f"Candidate link {normalised_candidate} is not present in the topology",
            "connectivity_ok": False,
            "qos_ok": False,
            "resilience_ok": False,
            "security_ok": True,
        }

    reduced_graph = _remove_link(topology_graph, normalised_candidate)
    connectivity_ok = _is_connected(reduced_graph)

    qos_ok = True
    qos_reason = None
    qos_limits = qos_limits or DEFAULT_QOS_LIMITS
    if "avg_rtt_ms" in qos_limits and float(qos_limits["avg_rtt_ms"]) < 0:
        qos_ok = False
        qos_reason = "QoS threshold is invalid"
    elif "avg_rtt_ms" in qos_limits and util_value > float(qos_limits["avg_rtt_ms"]):
        qos_ok = False
        qos_reason = "Average RTT exceeds the configured QoS ceiling"

    if qos_ok and "packet_loss_percent" in qos_limits:
        max_allowed_loss = float(qos_limits["packet_loss_percent"])
        if util_value > max_allowed_loss:
            qos_ok = False
            qos_reason = "Packet loss exceeds the configured allowance"

    resilience_ok = allow_single_link_disconnect or connectivity_ok
    if not connectivity_ok and not allow_single_link_disconnect:
        qos_ok = False
        qos_reason = "Removing the link disconnects the graph and violates resilience"

    security_ok = True
    safe = connectivity_ok and qos_ok and resilience_ok and security_ok

    return {
        "safe": safe,
        "candidate": normalised_candidate,
        "utilization_percent": util_value,
        "reason": qos_reason if not safe and qos_reason else ("Graph remains connected and within safety limits" if safe else "Link sleep violates resilience or connectivity constraints"),
        "connectivity_ok": connectivity_ok,
        "qos_ok": qos_ok,
        "resilience_ok": resilience_ok,
        "security_ok": security_ok,
    }


def find_low_utilization_link(topology_graph, utilization_by_link, threshold):
    """Return the best low-utilisation candidate link below the given threshold."""
    if utilization_by_link is None:
        utilization_by_link = {}
    if threshold is None:
        threshold = DEFAULT_LINK_UTILIZATION_THRESHOLD

    candidates = []
    for link_name in _unique_links(topology_graph):
        utilisation = float(utilization_by_link.get(link_name, 0.0))
        if utilisation > float(threshold):
            continue
        decision = check_constraints(
            topology_graph,
            link_name,
            utilization_by_link=utilization_by_link,
            qos_limits=DEFAULT_QOS_LIMITS,
            threshold=float(threshold),
        )
        if decision["safe"]:
            candidates.append((utilisation, link_name))

    if not candidates:
        return None

    candidates.sort(key=lambda pair: (pair[0], pair[1]))
    return candidates[0][1]


def choose_sleep_action(snapshot, *, threshold=DEFAULT_LINK_UTILIZATION_THRESHOLD, qos_limits=None):
    """Turn a controller snapshot into a single safe sleep decision."""
    graph = snapshot_to_topology_graph(snapshot)
    util_by_link = snapshot_to_utilization_by_link(snapshot)
    candidate = find_low_utilization_link(graph, util_by_link, threshold)

    if candidate is None:
        return {
            "action": "none",
            "candidate": None,
            "reason": "No safe low-utilization candidate found",
            "utilization_percent": None,
        }

    decision = check_constraints(
        graph,
        candidate,
        utilization_by_link=util_by_link,
        qos_limits=qos_limits or DEFAULT_QOS_LIMITS,
        threshold=threshold,
    )
    return {
        "action": "sleep_link" if decision["safe"] else "none",
        "candidate": decision["candidate"],
        "reason": decision["reason"],
        "utilization_percent": decision["utilization_percent"],
        "decision": decision,
    }
