"""Live Mininet/OVS integration test for the k=4 Fat-Tree.

This test is intended for a Linux runner with Mininet and Open vSwitch installed.
It expects launch.py to be running as the OpenFlow controller on 127.0.0.1:6653.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mininet.log import setLogLevel

from config import FAT_TREE_K, STP_CONVERGENCE_SECONDS
from topology.mininet_topo import build_topology


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

        print("\n[INTEGRATION] PASS: selected k=4 Fat-Tree host pairs are reachable.")
    finally:
        if net is not None:
            net.stop()


if __name__ == "__main__":
    main()
