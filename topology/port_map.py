"""Build a physical switch-port inventory from Mininet link objects.

Unlike LLDP discovery, this inventory includes switch links whose ports are
currently blocked by STP. It is derived from Mininet's actual interface port
numbers, not from assumptions about link creation order.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, Set, Tuple

MININET_PORT_MAP_PATH = "/tmp/sdn_energy_port_map.json"


def _switch_dpid(name: str) -> int:
    if not name.startswith("s") or not name[1:].isdigit():
        raise ValueError(f"Invalid deterministic switch name: {name!r}")
    return int(name[1:])


def mininet_interface_port(intf: Any) -> int:
    """Read a Mininet interface's port number from its owning node.

    Mininet stores interface-to-port assignments in intf.node.ports.
    Some interface subclasses, including TCIntf in supported Mininet
    versions, do not expose a public intf.port attribute.
    """
    node = getattr(intf, "node", None)
    node_ports = getattr(node, "ports", None)
    if node_ports is not None:
        try:
            return int(node_ports[intf])
        except (KeyError, TypeError, ValueError):
            pass

    # Compatibility fallback for Mininet forks that expose intf.port.
    direct_port = getattr(intf, "port", None)
    if direct_port is not None:
        try:
            return int(direct_port)
        except (TypeError, ValueError):
            pass

    node_name = getattr(node, "name", "<unknown>")
    intf_name = getattr(intf, "name", "<unknown>")
    raise ValueError(
        f"Cannot resolve Mininet port for interface {intf_name!r} on node "
        f"{node_name!r}; expected node.ports[intf]"
    )


def build_switch_port_map(
    link_endpoints: Iterable[Tuple[str, int, str, int]],
    switch_names: Iterable[str],
    *,
    expected_links: Sequence[Tuple[str, str]] | None = None,
) -> list[dict]:
    """Return one mapping row for each endpoint of every switch-to-switch link.

    Each input tuple is (node_a, port_a, node_b, port_b). Host-facing links
    are ignored. Duplicate switch ports and unexpected/missing physical links
    are rejected so a bad inventory cannot silently feed the monitor.
    """
    switches = set(switch_names)
    mapping: dict[Tuple[int, int], dict] = {}
    discovered_links: Set[str] = set()

    for node_a, port_a_raw, node_b, port_b_raw in link_endpoints:
        if node_a not in switches or node_b not in switches:
            continue

        dpid_a = _switch_dpid(node_a)
        dpid_b = _switch_dpid(node_b)
        port_a = int(port_a_raw)
        port_b = int(port_b_raw)
        if dpid_a == dpid_b:
            raise ValueError(f"Self-link is invalid for switch {node_a}")
        if port_a <= 0 or port_b <= 0:
            raise ValueError(f"Switch ports must be positive: {node_a}:{port_a}, {node_b}:{port_b}")

        left, right = sorted((node_a, node_b), key=_switch_dpid)
        link_name = f"{left}-{right}"
        discovered_links.add(link_name)

        rows = (
            {
                "dpid": dpid_a,
                "port_no": port_a,
                "peer_dpid": dpid_b,
                "peer_port_no": port_b,
                "link": link_name,
            },
            {
                "dpid": dpid_b,
                "port_no": port_b,
                "peer_dpid": dpid_a,
                "peer_port_no": port_a,
                "link": link_name,
            },
        )
        for row in rows:
            key = (row["dpid"], row["port_no"])
            previous = mapping.get(key)
            if previous is not None and previous != row:
                raise ValueError(f"Switch port {key} maps to more than one physical link")
            mapping[key] = row

    if expected_links is not None:
        expected = {
            f"{left}-{right}"
            for a, b in expected_links
            for left, right in [sorted((a, b), key=_switch_dpid)]
        }
        missing = sorted(expected - discovered_links)
        unexpected = sorted(discovered_links - expected)
        if missing or unexpected:
            raise ValueError(
                f"Mininet port inventory mismatch: missing={missing}, unexpected={unexpected}"
            )

    return sorted(mapping.values(), key=lambda row: (row["dpid"], row["port_no"]))


def write_switch_port_map(
    path: str,
    *,
    k: int,
    port_map: Sequence[Mapping[str, Any]],
) -> None:
    """Atomically publish the physical Mininet port inventory for OS-Ken."""
    links = sorted({str(row["link"]) for row in port_map})
    payload = {
        "schema_version": 1,
        "topology_k": int(k),
        "source": "mininet_net_links",
        "physical_inter_switch_link_count": len(links),
        "links": links,
        "port_map": list(port_map),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(temporary, target)
