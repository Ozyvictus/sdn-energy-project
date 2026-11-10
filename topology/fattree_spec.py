"""Pure-Python specification for canonical k-ary Fat-Tree topologies.

This module intentionally has no Mininet dependency so topology generation
and structural checks can run in ordinary Python environments.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Dict, Mapping, Tuple

Link = Tuple[str, str]


@dataclass(frozen=True)
class FatTreeSpec:
    """Deterministic node/link inventory for a canonical even-k Fat-Tree."""

    k: int
    core_switches: Tuple[str, ...]
    aggregation_switches: Tuple[str, ...]
    edge_switches: Tuple[str, ...]
    hosts: Tuple[str, ...]
    inter_switch_links: Tuple[Link, ...]
    host_links: Tuple[Link, ...]  # (edge switch, host)
    host_ips: Mapping[str, str]
    switch_roles: Mapping[str, str]
    pod_aggregation: Mapping[int, Tuple[str, ...]]
    pod_edge: Mapping[int, Tuple[str, ...]]
    host_to_edge: Mapping[str, str]
    link_capacities_mbps: Mapping[Link, int]

    @property
    def switches(self) -> Tuple[str, ...]:
        return self.core_switches + self.aggregation_switches + self.edge_switches

    @property
    def all_links(self) -> Tuple[Link, ...]:
        return self.inter_switch_links + self.host_links

    @property
    def counts(self) -> Dict[str, int]:
        return {
            "core_switches": len(self.core_switches),
            "aggregation_switches": len(self.aggregation_switches),
            "edge_switches": len(self.edge_switches),
            "switches": len(self.switches),
            "hosts": len(self.hosts),
            "inter_switch_links": len(self.inter_switch_links),
            "host_links": len(self.host_links),
            "total_links": len(self.all_links),
        }


def build_fat_tree_spec(
    k: int = 4,
    *,
    host_link_bw: int = 100,
    switch_link_bw: int = 1000,
    host_ip_network: str = "10.0.0.0/8",
) -> FatTreeSpec:
    """Build the canonical k-ary Fat-Tree inventory.

    A valid canonical k-ary Fat-Tree uses a positive even integer k.
    Each pod contains k/2 aggregation and k/2 edge switches. Every edge
    switch serves k/2 hosts. Each aggregation switch connects to k/2 edge
    switches in its pod and k/2 core switches (one in each core group).
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 2 or k % 2:
        raise ValueError("Fat-Tree k must be an even integer greater than or equal to 2")
    if not isinstance(host_link_bw, int) or host_link_bw <= 0:
        raise ValueError("host_link_bw must be a positive integer Mbps value")
    if not isinstance(switch_link_bw, int) or switch_link_bw <= 0:
        raise ValueError("switch_link_bw must be a positive integer Mbps value")

    half = k // 2
    core_count = half * half
    aggregation_count = k * half
    edge_count = k * half
    host_count = k * half * half

    core_switches = tuple(f"s{i}" for i in range(1, core_count + 1))
    aggregation_start = core_count + 1
    aggregation_switches = tuple(
        f"s{i}" for i in range(aggregation_start, aggregation_start + aggregation_count)
    )
    edge_start = aggregation_start + aggregation_count
    edge_switches = tuple(
        f"s{i}" for i in range(edge_start, edge_start + edge_count)
    )
    hosts = tuple(f"h{i}" for i in range(1, host_count + 1))

    switch_roles: Dict[str, str] = {}
    switch_roles.update({name: "core" for name in core_switches})
    switch_roles.update({name: "aggregation" for name in aggregation_switches})
    switch_roles.update({name: "edge" for name in edge_switches})

    pod_aggregation: Dict[int, Tuple[str, ...]] = {}
    pod_edge: Dict[int, Tuple[str, ...]] = {}
    inter_switch_links = []
    host_links = []
    host_to_edge: Dict[str, str] = {}
    host_ips: Dict[str, str] = {}
    link_capacities: Dict[Link, int] = {}

    ip_network = ipaddress.ip_network(host_ip_network, strict=False)
    ip_iterator = ip_network.hosts()

    for pod in range(k):
        pod_aggs = tuple(
            aggregation_switches[pod * half + index]
            for index in range(half)
        )
        pod_edges = tuple(
            edge_switches[pod * half + index]
            for index in range(half)
        )
        pod_aggregation[pod] = pod_aggs
        pod_edge[pod] = pod_edges

        # Each aggregation switch connects to one core in every core group.
        # The position within the aggregation block selects the core index.
        for aggregation_index, aggregation in enumerate(pod_aggs):
            for core_group in range(half):
                core = core_switches[core_group * half + aggregation_index]
                link = (aggregation, core)
                inter_switch_links.append(link)
                link_capacities[link] = switch_link_bw

        # Every edge switch connects to every aggregation switch in its pod.
        for edge in pod_edges:
            for aggregation in pod_aggs:
                link = (edge, aggregation)
                inter_switch_links.append(link)
                link_capacities[link] = switch_link_bw

            # Attach exactly k/2 hosts to each edge switch.
            edge_index = pod_edges.index(edge)
            for host_index in range(half):
                host_number = pod * half * half + edge_index * half + host_index + 1
                host = f"h{host_number}"
                ip_address = next(ip_iterator, None)
                if ip_address is None:
                    raise ValueError(
                        f"Host IP network {host_ip_network!r} is too small for {host_count} hosts"
                    )
                hosts_ip = str(ip_address)
                host_ips[host] = f"{hosts_ip}/{ip_network.prefixlen}"
                host_links.append((edge, host))
                host_to_edge[host] = edge
                link_capacities[(edge, host)] = host_link_bw

    return FatTreeSpec(
        k=k,
        core_switches=core_switches,
        aggregation_switches=aggregation_switches,
        edge_switches=edge_switches,
        hosts=hosts,
        inter_switch_links=tuple(inter_switch_links),
        host_links=tuple(host_links),
        host_ips=host_ips,
        switch_roles=switch_roles,
        pod_aggregation=pod_aggregation,
        pod_edge=pod_edge,
        host_to_edge=host_to_edge,
        link_capacities_mbps=link_capacities,
    )
