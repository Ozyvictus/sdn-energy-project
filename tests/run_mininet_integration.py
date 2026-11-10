"""Live Mininet/OVS integration test for the k=4 Fat-Tree.

This test is intended for a Linux runner with Mininet and Open vSwitch installed.
It expects launch.py to be running as the OpenFlow controller on 127.0.0.1:6653.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mininet.log import setLogLevel

from config import FAT_TREE_K, STP_CONVERGENCE_SECONDS
from topology.mininet_topo import build_topology
from algorithm.qos_monitor import QoSMonitor


def ovs_value(*args: str) -> str:
    return subprocess.check_output(
        ["ovs-vsctl", *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def wait_for_all_controllers(switch_count: int, timeout_seconds: int = 45) -> None:
    """Wait until every expected bridge reports an active OpenFlow controller."""
    deadline = time.monotonic() + timeout_seconds
    pending = list(range(1, switch_count + 1))

    while pending and time.monotonic() < deadline:
        pending = []
        for dpid in range(1, switch_count + 1):
            bridge = f"s{dpid}"
            try:
                controller_refs = ovs_value("get", "Bridge", bridge, "controller")
                ref = controller_refs.strip().strip("[]").strip('"')
                if not ref:
                    pending.append(dpid)
                    continue
                connected = ovs_value("get", "Controller", ref, "is_connected")
                if connected.lower() != "true":
                    pending.append(dpid)
            except subprocess.CalledProcessError:
                pending.append(dpid)
        if pending:
            time.sleep(1)

    if pending:
        raise RuntimeError(
            "OpenFlow controller did not connect to all switches; "
            f"DPIDs still disconnected: {pending}"
        )


def check_pair(net, source_name: str, destination_name: str) -> None:
    source = net.get(source_name)
    destination = net.get(destination_name)
    print(f"\n[INTEGRATION] Testing {source_name} <-> {destination_name}")
    loss_percent = net.ping([source, destination], timeout=2)
    if loss_percent != 0:
        raise AssertionError(
            f"{source_name}<->{destination_name} has {loss_percent:.1f}% packet loss"
        )


def wait_for_monitor_snapshot(timeout_seconds: int = 30) -> dict:
    """Wait for a complete OpenFlow stats snapshot of the discovered k=4 fabric."""
    snapshot_path = Path("/tmp/sdn_energy_cycle.json")
    deadline = time.monotonic() + timeout_seconds
    latest = None
    while time.monotonic() < deadline:
        try:
            latest = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if (
                latest.get("topology_switch_count") == 20
                and latest.get("topology_inter_switch_link_count") == 32
                and latest.get("stats_complete") is True
                and float(latest.get("energy_interval_seconds", 0)) > 0
            ):
                return latest
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        time.sleep(0.25)
    raise AssertionError(
        "Controller did not publish a complete k=4 monitoring snapshot; "
        f"last snapshot={latest}"
    )


def check_qos_measurement_batch(net, timeout_seconds: int = 30) -> None:
    """Run one live 16-pair QoS batch and validate the resulting CSV records."""
    with tempfile.TemporaryDirectory(prefix="sdn-qos-ci-") as output_dir:
        monitor = QoSMonitor(
            net,
            data_dir=output_dir,
            enable_rotating_iperf=False,
        ).start()
        summary_path = Path(output_dir) / "qos_cycle_summary.csv"
        pair_path = Path(output_dir) / "qos_pair_measurements.csv"
        summary = None
        pair_rows = []
        deadline = time.monotonic() + timeout_seconds
        try:
            while time.monotonic() < deadline:
                if summary_path.exists() and summary_path.stat().st_size:
                    try:
                        with summary_path.open("r", newline="", encoding="utf-8") as handle:
                            summaries = list(csv.DictReader(handle))
                        with pair_path.open("r", newline="", encoding="utf-8") as handle:
                            pair_rows = list(csv.DictReader(handle))
                        if summaries:
                            summary = summaries[-1]
                            if len(pair_rows) >= int(summary["pairs_scheduled"]):
                                break
                    except (OSError, csv.Error, KeyError, ValueError):
                        pass
                time.sleep(0.2)
        finally:
            monitor.stop()

        if summary is None:
            raise AssertionError("QoS monitor did not complete a batch before timeout")

        if int(summary["pairs_expected"]) != 120:
            raise AssertionError(f"Expected 120 host pairs, got {summary['pairs_expected']}")
        if int(summary["pairs_scheduled"]) != 16:
            raise AssertionError(f"Expected 16 scheduled pairs, got {summary['pairs_scheduled']}")
        if int(summary["pairs_measured"]) != 16:
            raise AssertionError(f"Expected 16 valid pair measurements, got {summary['pairs_measured']}")
        if len(pair_rows) != 16:
            raise AssertionError(f"Expected 16 per-pair CSV rows, got {len(pair_rows)}")
        if any(row.get("ping_count") != "3" for row in pair_rows):
            raise AssertionError("QoS pair CSV does not show three measured ICMP probes per pair")
        if any(row.get("warmup_ping_count") != "1" for row in pair_rows):
            raise AssertionError("QoS pair CSV does not show one separate warm-up probe per pair")
        print(
            "[INTEGRATION] QoS measurement batch PASS: "
            f"{summary['pairs_measured']}/{summary['pairs_expected']} valid overall pairs, "
            f"batch {summary['batch_index']}/{summary['batches_per_sweep']}, "
            f"{summary['ping_batch_duration_seconds']}s"
        )


def main() -> None:
    setLogLevel("info")
    if FAT_TREE_K != 4:
        raise AssertionError(f"CI integration test expects k=4, got k={FAT_TREE_K}")

    net = None
    try:
        net = build_topology(k=4, enable_stp=True)
        wait_for_all_controllers(switch_count=20)

        print(
            f"\n[INTEGRATION] Waiting {STP_CONVERGENCE_SECONDS}s "
            "for STP convergence..."
        )
        time.sleep(STP_CONVERGENCE_SECONDS)

        for dpid in range(1, 21):
            stp_enabled = ovs_value("get", "Bridge", f"s{dpid}", "stp_enable")
            if stp_enabled.lower() != "true":
                raise AssertionError(f"STP is not enabled on s{dpid}: {stp_enabled}")

        # Same edge switch, different edge switches in the same pod,
        # and hosts in separate pods exercise progressively longer paths.
        check_pair(net, "h1", "h2")
        check_pair(net, "h1", "h3")
        check_pair(net, "h1", "h5")
        check_pair(net, "h9", "h16")

        print("\n[INTEGRATION] Testing all 16 hosts with pingAll")
        all_hosts_loss = net.pingAll(timeout=2)
        if all_hosts_loss != 0:
            raise AssertionError(
                f"Full k=4 Fat-Tree pingAll has {all_hosts_loss:.1f}% packet loss"
            )

        snapshot = wait_for_monitor_snapshot()
        print(
            "\n[INTEGRATION] Monitoring snapshot PASS: "
            f"{snapshot['topology_switch_count']} switches, "
            f"{snapshot['topology_inter_switch_link_count']} unique links, "
            f"{snapshot['switch_reply_coverage_percent']}% switch-stat coverage, "
            f"{snapshot['energy_interval_seconds']}s energy interval"
        )
        check_qos_measurement_batch(net)

        print("\n[INTEGRATION] PASS: topology, monitoring, QoS batch, and host reachability validated.")
    finally:
        if net is not None:
            net.stop()


if __name__ == "__main__":
    main()
