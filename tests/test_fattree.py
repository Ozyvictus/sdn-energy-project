"""Structural tests for the pure-Python Fat-Tree specification."""

from collections import Counter

import pytest

from topology.fattree_spec import build_fat_tree_spec


def canonical_edges(links):
    return {tuple(sorted(link)) for link in links}


def test_k4_has_canonical_component_counts():
    spec = build_fat_tree_spec(4)

    assert spec.counts == {
        "core_switches": 4,
        "aggregation_switches": 8,
        "edge_switches": 8,
        "switches": 20,
        "hosts": 16,
        "inter_switch_links": 32,
        "host_links": 16,
        "total_links": 48,
    }


def test_k4_switch_degrees_and_host_placement():
    spec = build_fat_tree_spec(4)
    adjacency = {switch: set() for switch in spec.switches}

    for left, right in spec.inter_switch_links:
        adjacency[left].add(right)
        adjacency[right].add(left)

    assert all(len(adjacency[core]) == 4 for core in spec.core_switches)
    assert all(len(adjacency[agg]) == 4 for agg in spec.aggregation_switches)
    assert all(len(adjacency[edge]) == 2 for edge in spec.edge_switches)

    host_counts = Counter(edge for edge, _host in spec.host_links)
    assert set(host_counts) == set(spec.edge_switches)
    assert all(host_counts[edge] == 2 for edge in spec.edge_switches)


def test_k4_pod_structure_and_core_groups():
    spec = build_fat_tree_spec(4)
    core_group_a = set(spec.core_switches[:2])
    core_group_b = set(spec.core_switches[2:])

    assert len(spec.pod_aggregation) == 4
    assert len(spec.pod_edge) == 4

    for pod in range(4):
        aggregations = spec.pod_aggregation[pod]
        edges = spec.pod_edge[pod]
        assert len(aggregations) == 2
        assert len(edges) == 2

        for edge in edges:
            connected_aggs = {
                right if left == edge else left
                for left, right in spec.inter_switch_links
                if edge in (left, right)
            }
            assert connected_aggs == set(aggregations)

        for aggregation in aggregations:
            connected_cores = {
                right if left == aggregation else left
                for left, right in spec.inter_switch_links
                if aggregation in (left, right)
                and (right if left == aggregation else left) in spec.core_switches
            }
            assert len(connected_cores & core_group_a) == 1
            assert len(connected_cores & core_group_b) == 1


def test_k4_has_no_duplicate_links_and_all_hosts_are_assigned_once():
    spec = build_fat_tree_spec(4)

    assert len(canonical_edges(spec.all_links)) == 48
    assert len(spec.all_links) == len(canonical_edges(spec.all_links))

    attached_hosts = [host for _edge, host in spec.host_links]
    assert sorted(attached_hosts) == sorted(spec.hosts)
    assert len(set(attached_hosts)) == 16
    assert set(spec.host_to_edge) == set(spec.hosts)


def test_k4_host_addresses_and_link_capacities():
    spec = build_fat_tree_spec(4)

    assert len(set(spec.host_ips.values())) == 16
    assert spec.host_ips["h1"] == "10.0.0.1/8"
    assert spec.host_ips["h16"] == "10.0.0.16/8"

    for link in spec.inter_switch_links:
        assert spec.link_capacities_mbps[link] == 1000
    for link in spec.host_links:
        assert spec.link_capacities_mbps[link] == 100


@pytest.mark.parametrize("k", [2, 4, 6, 8])
def test_even_k_topologies_have_expected_general_counts(k):
    spec = build_fat_tree_spec(k)
    half = k // 2

    assert spec.counts["core_switches"] == half**2
    assert spec.counts["aggregation_switches"] == k * half
    assert spec.counts["edge_switches"] == k * half
    assert spec.counts["hosts"] == k * half**2
    assert spec.counts["inter_switch_links"] == k**3 // 2
    assert spec.counts["host_links"] == k**3 // 4


@pytest.mark.parametrize("k", [0, 1, 3, 5, -2, True, 4.0])
def test_invalid_k_is_rejected(k):
    with pytest.raises(ValueError):
        build_fat_tree_spec(k)


def test_too_small_host_address_pool_is_rejected():
    with pytest.raises(ValueError, match="too small"):
        build_fat_tree_spec(4, host_ip_network="192.0.2.0/28")
