"""Tests for deterministic physical switch-port inventory generation."""

import unittest

from topology.port_map import build_switch_port_map, mininet_interface_port


class FakeNode:
    def __init__(self, name):
        self.name = name


class FakeIntf:
    def __init__(self, node, port):
        self.node = FakeNode(node)
        self.port = port


class InterfacePortLookupTests(unittest.TestCase):
    def test_reads_port_from_node_mapping_without_intf_port_attribute(self):
        class Node:
            name = "s1"

            def __init__(self):
                self.ports = {}

        class TCIntfLike:
            def __init__(self, node):
                self.node = node
                self.name = "s1-eth3"

        node = Node()
        intf = TCIntfLike(node)
        node.ports[intf] = 3

        self.assertFalse(hasattr(intf, "port"))
        self.assertEqual(mininet_interface_port(intf), 3)

    def test_uses_direct_port_only_as_compatibility_fallback(self):
        class Node:
            name = "s1"

        class ForkIntf:
            node = Node()
            port = 4
            name = "s1-eth4"

        self.assertEqual(mininet_interface_port(ForkIntf()), 4)

    def test_missing_port_mapping_raises_clear_error(self):
        class Node:
            name = "s1"
            ports = {}

        class Intf:
            node = Node()
            name = "s1-eth7"

        with self.assertRaisesRegex(ValueError, "node.ports"):
            mininet_interface_port(Intf())


class PhysicalPortMapTests(unittest.TestCase):
    def test_maps_both_ends_of_switch_links_and_ignores_host_links(self):
        endpoints = [
            ("s1", 1, "s5", 3),
            ("s5", 4, "s13", 1),
            ("h1", 0, "s13", 2),
        ]
        rows = build_switch_port_map(
            endpoints,
            {"s1", "s5", "s13"},
            expected_links=(("s1", "s5"), ("s5", "s13")),
        )

        self.assertEqual(len(rows), 4)
        self.assertEqual(
            {(row["dpid"], row["port_no"], row["peer_dpid"], row["peer_port_no"], row["link"])
             for row in rows},
            {
                (1, 1, 5, 3, "s1-s5"),
                (5, 3, 1, 1, "s1-s5"),
                (5, 4, 13, 1, "s5-s13"),
                (13, 1, 5, 4, "s5-s13"),
            },
        )

    def test_rejects_missing_expected_links(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            build_switch_port_map(
                [("s1", 1, "s5", 1)],
                {"s1", "s5", "s13"},
                expected_links=(("s1", "s5"), ("s5", "s13")),
            )

    def test_rejects_duplicate_port_assignments(self):
        with self.assertRaisesRegex(ValueError, "more than one"):
            build_switch_port_map(
                [
                    ("s1", 1, "s5", 1),
                    ("s1", 1, "s13", 1),
                ],
                {"s1", "s5", "s13"},
            )

    def test_rejects_non_positive_switch_ports(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            build_switch_port_map(
                [("s1", 0, "s5", 1)],
                {"s1", "s5"},
            )

    def test_rejects_invalid_switch_names(self):
        with self.assertRaisesRegex(ValueError, "switch name"):
            build_switch_port_map(
                [("sw1", 1, "s5", 1)],
                {"sw1", "s5"},
            )


if __name__ == "__main__":
    unittest.main()
