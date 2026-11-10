"""Structural tests for the four-pod offered-load traffic profile."""

import unittest

from algorithm.traffic_demo import FLOW_LAYOUT, PROFILES, _target_attainment_percent
from topology.fattree_spec import build_fat_tree_spec


class TrafficDemoTopologyTests(unittest.TestCase):
    def test_flows_cover_all_four_pods_and_all_eight_edge_switches(self):
        spec = build_fat_tree_spec(4)
        edge_to_pod = {
            edge: pod
            for pod, edges in spec.pod_edge.items()
            for edge in edges
        }

        source_hosts = [source for source, _destination in FLOW_LAYOUT]
        destination_hosts = [destination for _source, destination in FLOW_LAYOUT]
        used_hosts = source_hosts + destination_hosts

        self.assertEqual(len(FLOW_LAYOUT), 4)
        self.assertEqual(len(set(used_hosts)), 8)
        self.assertTrue(set(used_hosts).issubset(set(spec.hosts)))

        source_pods = {
            edge_to_pod[spec.host_to_edge[host]]
            for host in source_hosts
        }
        destination_pods = {
            edge_to_pod[spec.host_to_edge[host]]
            for host in destination_hosts
        }
        used_edges = {spec.host_to_edge[host] for host in used_hosts}

        self.assertEqual(source_pods, {0, 1, 2, 3})
        self.assertEqual(destination_pods, {0, 1, 2, 3})
        self.assertEqual(used_edges, set(spec.edge_switches))

        for source, destination in FLOW_LAYOUT:
            self.assertNotEqual(
                edge_to_pod[spec.host_to_edge[source]],
                edge_to_pod[spec.host_to_edge[destination]],
            )

    def test_offered_rate_per_flow_stays_below_host_link_capacity(self):
        spec = build_fat_tree_spec(4)
        host_link_capacity = min(
            spec.link_capacities_mbps[(edge, host)]
            for edge, host in spec.host_links
        )
        self.assertLess(max(rate for _name, rate in PROFILES), host_link_capacity)

    def test_target_attainment_distinguishes_requested_from_achieved_rate(self):
        self.assertEqual(_target_attainment_percent(10.0, 20.0), 50.0)
        self.assertEqual(_target_attainment_percent(20.0, 20.0), 100.0)
        self.assertIsNone(_target_attainment_percent(None, 20.0))
        self.assertIsNone(_target_attainment_percent(10.0, 0.0))


if __name__ == "__main__":
    unittest.main()
