"""Standard-library structural tests for the pure-Python Fat-Tree specification."""

import unittest
from collections import Counter

from topology.fattree_spec import build_fat_tree_spec


def canonical_edges(links):
    return {tuple(sorted(link)) for link in links}


class FatTreeSpecTests(unittest.TestCase):
    def test_k4_has_canonical_component_counts(self):
        spec = build_fat_tree_spec(4)
        self.assertEqual(
            spec.counts,
            {
                "core_switches": 4,
                "aggregation_switches": 8,
                "edge_switches": 8,
                "switches": 20,
                "hosts": 16,
                "inter_switch_links": 32,
                "host_links": 16,
                "total_links": 48,
            },
        )

    def test_k4_switch_degrees_and_host_placement(self):
        spec = build_fat_tree_spec(4)
        adjacency = {switch: set() for switch in spec.switches}

        for left, right in spec.inter_switch_links:
            adjacency[left].add(right)
            adjacency[right].add(left)

        self.assertTrue(all(len(adjacency[core]) == 4 for core in spec.core_switches))
        self.assertTrue(all(len(adjacency[agg]) == 4 for agg in spec.aggregation_switches))
        self.assertTrue(all(len(adjacency[edge]) == 2 for edge in spec.edge_switches))

        host_counts = Counter(edge for edge, _host in spec.host_links)
        self.assertEqual(set(host_counts), set(spec.edge_switches))
        self.assertTrue(all(host_counts[edge] == 2 for edge in spec.edge_switches))

    def test_k4_pod_structure_and_core_groups(self):
        spec = build_fat_tree_spec(4)
        core_group_a = set(spec.core_switches[:2])
        core_group_b = set(spec.core_switches[2:])

        self.assertEqual(len(spec.pod_aggregation), 4)
        self.assertEqual(len(spec.pod_edge), 4)

        for pod in range(4):
            aggregations = spec.pod_aggregation[pod]
            edges = spec.pod_edge[pod]
            self.assertEqual(len(aggregations), 2)
            self.assertEqual(len(edges), 2)

            for edge in edges:
                connected_aggs = {
                    right if left == edge else left
                    for left, right in spec.inter_switch_links
                    if edge in (left, right)
                }
                self.assertEqual(connected_aggs, set(aggregations))

            for aggregation in aggregations:
                connected_cores = {
                    right if left == aggregation else left
                    for left, right in spec.inter_switch_links
                    if aggregation in (left, right)
                    and (right if left == aggregation else left) in spec.core_switches
                }
                self.assertEqual(len(connected_cores & core_group_a), 1)
                self.assertEqual(len(connected_cores & core_group_b), 1)

    def test_k4_has_no_duplicate_links_and_all_hosts_are_assigned_once(self):
        spec = build_fat_tree_spec(4)
        unique_links = canonical_edges(spec.all_links)

        self.assertEqual(len(unique_links), 48)
        self.assertEqual(len(spec.all_links), len(unique_links))
        attached_hosts = [host for _edge, host in spec.host_links]
        self.assertEqual(sorted(attached_hosts), sorted(spec.hosts))
        self.assertEqual(len(set(attached_hosts)), 16)
        self.assertEqual(set(spec.host_to_edge), set(spec.hosts))

    def test_k4_host_addresses_and_link_capacities(self):
        spec = build_fat_tree_spec(4)

        self.assertEqual(len(set(spec.host_ips.values())), 16)
        self.assertEqual(spec.host_ips["h1"], "10.0.0.1/8")
        self.assertEqual(spec.host_ips["h16"], "10.0.0.16/8")

        for link in spec.inter_switch_links:
            self.assertEqual(spec.link_capacities_mbps[link], 1000)
        for link in spec.host_links:
            self.assertEqual(spec.link_capacities_mbps[link], 100)

    def test_even_k_topologies_have_expected_general_counts(self):
        for k in (2, 4, 6, 8):
            with self.subTest(k=k):
                spec = build_fat_tree_spec(k)
                half = k // 2
                self.assertEqual(spec.counts["core_switches"], half**2)
                self.assertEqual(spec.counts["aggregation_switches"], k * half)
                self.assertEqual(spec.counts["edge_switches"], k * half)
                self.assertEqual(spec.counts["hosts"], k * half**2)
                self.assertEqual(spec.counts["inter_switch_links"], k**3 // 2)
                self.assertEqual(spec.counts["host_links"], k**3 // 4)

    def test_invalid_k_is_rejected(self):
        for k in (0, 1, 3, 5, -2, True, 4.0):
            with self.subTest(k=k):
                with self.assertRaises(ValueError):
                    build_fat_tree_spec(k)

    def test_too_small_host_address_pool_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "too small"):
            build_fat_tree_spec(4, host_ip_network="192.0.2.0/28")


if __name__ == "__main__":
    unittest.main()
