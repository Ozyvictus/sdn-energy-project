"""Unit tests for deterministic QoS pair sampling and ICMP parsing."""

import unittest
from unittest.mock import patch

from algorithm.qos_monitor import (
    PING_COUNT,
    WARMUP_PING_COUNT,
    QoSMonitor,
    parse_ping_output,
    select_pair_batch,
)


class PingParsingTests(unittest.TestCase):
    def test_three_packet_ping_parses_rtt_and_mdev(self):
        output = """3 packets transmitted, 3 received, 0% packet loss, time 2002ms
rtt min/avg/max/mdev = 0.035/0.045/0.056/0.009 ms
"""
        result = parse_ping_output(output)
        self.assertTrue(result["measurement_valid"])
        self.assertTrue(result["reachable"])
        self.assertEqual(result["packets_sent"], 3)
        self.assertEqual(result["packets_received"], 3)
        self.assertEqual(result["packet_loss_percent"], 0.0)
        self.assertEqual(result["latency_ms"], 0.045)
        self.assertEqual(result["jitter_ms"], 0.009)
        self.assertEqual(PING_COUNT, 3)

    def test_total_packet_loss_is_a_valid_measurement_without_rtt(self):
        output = "3 packets transmitted, 0 received, 100% packet loss, time 2030ms"
        result = parse_ping_output(output)
        self.assertTrue(result["measurement_valid"])
        self.assertFalse(result["reachable"])
        self.assertEqual(result["packet_loss_percent"], 100.0)
        self.assertIsNone(result["latency_ms"])
        self.assertIsNone(result["jitter_ms"])


class PairBatchTests(unittest.TestCase):
    def setUp(self):
        self.pairs = [(f"h{i}", f"h{j}") for i in range(1, 17) for j in range(i + 1, 17)]

    def test_k4_pair_matrix_is_covered_once_over_eight_batches(self):
        self.assertEqual(len(self.pairs), 120)
        visited = []
        for sequence in range(1, 9):
            batch_index, batch_count, batch = select_pair_batch(
                self.pairs, sequence, batch_size=16
            )
            self.assertEqual(batch_index, sequence)
            self.assertEqual(batch_count, 8)
            self.assertEqual(len(batch), 16 if sequence < 8 else 8)
            visited.extend(batch)
        self.assertEqual(visited, self.pairs)
        self.assertEqual(len(set(visited)), 120)

    def test_last_batch_can_be_smaller_and_next_sweep_restarts(self):
        pairs = self.pairs[:28]
        first = select_pair_batch(pairs, 1, batch_size=16)
        second = select_pair_batch(pairs, 2, batch_size=16)
        third = select_pair_batch(pairs, 3, batch_size=16)
        self.assertEqual((first[0], first[1], len(first[2])), (1, 2, 16))
        self.assertEqual((second[0], second[1], len(second[2])), (2, 2, 12))
        self.assertEqual(third, first)

    def test_empty_pair_set(self):
        self.assertEqual(select_pair_batch([], 1), (0, 0, []))
\n    def test_pair_measurement_separates_warmup_from_recorded_qos(self):
        class Host:
            def __init__(self, name, ip):
                self.name = name
                self._ip = ip

            def IP(self):
                return self._ip

        source = Host("h1", "10.0.0.1")
        destination = Host("h2", "10.0.0.2")
        warmup_result = {
            "latency_ms": 650.0,
            "reachable": True,
            "measurement_valid": True,
        }
        measured_result = {
            "latency_ms": 0.2,
            "jitter_ms": 0.05,
            "packet_loss_percent": 0.0,
            "reachable": True,
            "packets_sent": 3,
            "packets_received": 3,
            "measurement_valid": True,
            "ping_output": "three measured probes",
        }

        monitor = QoSMonitor(net=type("Net", (), {"hosts": [source, destination]})())
        with patch(
            "algorithm.qos_monitor.measure_latency_loss",
            side_effect=[warmup_result, measured_result],
        ) as measure:
            row = monitor._measure_pair(source, destination)

        self.assertEqual(measure.call_count, 2)
        self.assertEqual(measure.call_args_list[0].kwargs["count"], WARMUP_PING_COUNT)
        self.assertEqual(row["warmup_latency_ms"], 650.0)
        self.assertTrue(row["warmup_reachable"])
        self.assertEqual(row["ping_count"], PING_COUNT)
        self.assertEqual(row["latency_ms"], 0.2)
        self.assertEqual(row["jitter_ms"], 0.05)


    def test_invalid_batch_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            select_pair_batch(self.pairs, 0)
        with self.assertRaises(ValueError):
            select_pair_batch(self.pairs, 1, batch_size=0)

    def test_legacy_csv_is_preserved_and_new_schema_uses_versioned_file(self):
        import csv
        import tempfile
        from pathlib import Path

        from algorithm.qos_monitor import _append_rows

        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / "qos_pair_measurements.csv"
            with legacy.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(["old_column"])

            current = _append_rows(
                legacy,
                ["new_column"],
                [{"new_column": "kept_separate"}],
            )

            self.assertEqual(current.name, "qos_pair_measurements_v2.csv")
            with legacy.open("r", newline="", encoding="utf-8") as handle:
                self.assertEqual(next(csv.reader(handle)), ["old_column"])
            with current.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows, [{"new_column": "kept_separate"}])


if __name__ == "__main__":
    unittest.main()
