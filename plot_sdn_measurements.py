#!/usr/bin/env python3
"""Plot latest common session from the SDN project's CSV measurement files.

Run from the project root after a measurement run:
    python plot_sdn_measurements.py

Expected files under data/:
    active_port_metrics.csv
    component_energy.csv
    qos_cycle_summary.csv
    qos_pair_measurements.csv
"""
from pathlib import Path
import sys
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path.cwd()
DATA = ROOT / "data"
OUT = ROOT / "graphs" / "measurement_demo"
FILES = {
    "active": DATA / "active_port_metrics.csv",
    "energy": DATA / "component_energy.csv",
    "summary": DATA / "qos_cycle_summary.csv",
    "pairs": DATA / "qos_pair_measurements.csv",
}


def main():
    missing = [str(p) for p in FILES.values() if not p.exists()]
    if missing:
        print("Missing required CSV file(s):", *missing, sep="\n  ", file=sys.stderr)
        return 2
    dfs = {name: pd.read_csv(path) for name, path in FILES.items()}
    for name, df in dfs.items():
        if "session_id" not in df.columns:
            print(f"{FILES[name]} has no session_id column", file=sys.stderr)
            return 2
    common = set.intersection(*[
        set(df["session_id"].dropna().astype(str)) for df in dfs.values()
    ])
    if not common:
        print("No common session_id exists in all four CSVs.", file=sys.stderr)
        return 2

    # Use the newest session in the QoS summary if all files contain it.
    qos_sid = str(dfs["summary"]["session_id"].iloc[-1])
    sid = qos_sid if qos_sid in common else sorted(common)[-1]
    dfs = {k: v[v["session_id"].astype(str) == sid].copy() for k, v in dfs.items()}
    active, energy, summary, pairs = dfs["active"], dfs["energy"], dfs["summary"], dfs["pairs"]
    OUT.mkdir(parents=True, exist_ok=True)

    def save(fig, name):
        fig.tight_layout()
        fig.savefig(OUT / name, dpi=160)
        plt.close(fig)
        print(OUT / name)

    if not energy.empty and {"cycle", "total_power_w"}.issubset(energy.columns):
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(energy["cycle"], energy["total_power_w"], marker="o")
        ax.set_title("Modeled Network Power by Cycle")
        ax.set_xlabel("Controller cycle")
        ax.set_ylabel("Modeled power (W)")
        ax.grid(True, alpha=0.3)
        save(fig, "modeled_power.png")

        if "cumulative_energy_wh" in energy:
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.plot(energy["cycle"], energy["cumulative_energy_wh"], marker=".")
            ax.set_title("Cumulative Modeled Energy")
            ax.set_xlabel("Controller cycle")
            ax.set_ylabel("Cumulative energy (Wh)")
            ax.grid(True, alpha=0.3)
            save(fig, "cumulative_energy.png")

    if not energy.empty and {"cycle", "switches_active", "links_active"}.issubset(energy.columns):
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(energy["cycle"], energy["switches_active"], marker=".", label="Active switches")
        ax.plot(energy["cycle"], energy["links_active"], marker=".", label="Active inter-switch links")
        ax.set_title("Modeled Active Component Counts")
        ax.set_xlabel("Controller cycle")
        ax.set_ylabel("Component count")
        ax.legend()
        ax.grid(True, alpha=0.3)
        save(fig, "active_components.png")

    if not active.empty and {"cycle", "scope", "active_ports"}.issubset(active.columns):
        network = active[active["scope"].astype(str).str.lower() == "network"].sort_values("cycle")
        if not network.empty:
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.plot(network["cycle"], network["active_ports"], marker=".")
            ax.set_title("Network-wide Traffic-active Ports")
            ax.set_xlabel("Controller cycle")
            ax.set_ylabel("Active ports")
            ax.grid(True, alpha=0.3)
            save(fig, "network_active_ports.png")

    if not summary.empty and {"cycle", "average_rtt_ms", "p95_rtt_ms"}.issubset(summary.columns):
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(summary["cycle"], summary["average_rtt_ms"], marker="o", label="Average RTT")
        ax.plot(summary["cycle"], summary["p95_rtt_ms"], marker="s", label="P95 RTT")
        ax.set_title("Network-wide QoS Latency")
        ax.set_xlabel("QoS sweep cycle")
        ax.set_ylabel("Round-trip time (ms)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        save(fig, "qos_latency.png")

    if not summary.empty and {"cycle", "average_packet_loss_percent", "maximum_packet_loss_percent"}.issubset(summary.columns):
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(summary["cycle"], summary["average_packet_loss_percent"], marker="o", label="Average loss")
        ax.plot(summary["cycle"], summary["maximum_packet_loss_percent"], marker="s", label="Maximum loss")
        ax.set_title("Network-wide QoS Packet Loss")
        ax.set_xlabel("QoS sweep cycle")
        ax.set_ylabel("Packet loss (%)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        save(fig, "qos_packet_loss.png")

    if not summary.empty and {"cycle", "throughput_mbps", "throughput_pair"}.issubset(summary.columns):
        tput = summary.dropna(subset=["throughput_mbps"])
        if not tput.empty:
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.plot(tput["cycle"], tput["throughput_mbps"], marker="o")
            ax.set_title("Rotating TCP Throughput Samples")
            ax.set_xlabel("QoS sweep cycle")
            ax.set_ylabel("TCP throughput (Mbps)")
            ax.grid(True, alpha=0.3)
            save(fig, "tcp_throughput.png")

    print(f"Session: {sid}")
    print(f"Rows: energy={len(energy)}, active-port={len(active)}, QoS summaries={len(summary)}, pair measurements={len(pairs)}")
    print(f"Graphs saved under: {OUT}")
    print("Note: traffic utilisation is separately updated by controller/algorithm/traffic_utilisation.py.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
