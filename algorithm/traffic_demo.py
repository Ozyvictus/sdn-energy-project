#!/usr/bin/env python3
"""
Automated Mininet traffic demo for the SDN energy/QoS project.

Run this module from the Mininet CLI, after the topology is started:
    mininet> px from algorithm.traffic_demo import run_demo
    mininet> px run_demo(net)

The demo runs:
  * a quiet baseline (no generated demo workload),
  * four simultaneous inter-pod UDP flows at 10, 25, and 60 Mbps per flow,
  * a quiet cooldown.

The controller/QoS monitors continue independently. This script records the
exact wall-clock window for each phase, plus iperf3 sender/receiver results.
It does not alter controller code or the application's measurement CSVs.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import random
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "data" / "traffic_demo"

# Directed flow ring spanning all four pods and all eight edge switches.
# Host mapping is defined by topology/fattree_spec.py:
# pod 0: h1-h4, pod 1: h5-h8, pod 2: h9-h12, pod 3: h13-h16.
# Dynamic ports reduce the risk of colliding with old iperf3 test servers.
FLOW_LAYOUT = (
    ("h1", "h5"),    # pod 0 -> pod 1, edge s13 -> s15
    ("h7", "h11"),   # pod 1 -> pod 2, edge s16 -> s18
    ("h9", "h13"),   # pod 2 -> pod 3, edge s17 -> s19
    ("h15", "h3"),   # pod 3 -> pod 0, edge s20 -> s14
)

PROFILES = (
    ("low", 10),
    ("medium", 25),
    ("high", 60),
)

DEFAULT_BASELINE_SECONDS = 10
DEFAULT_PROFILE_SECONDS = 20
DEFAULT_COOLDOWN_SECONDS = 10
SERVER_STARTUP_SECONDS = 1.0
PROCESS_TIMEOUT_GRACE_SECONDS = 12.0


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_csv(path: Path, fieldnames: List[str], row: Dict[str, Any]) -> None:
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()


def _make_user_readable(path: Path) -> None:
    """Make generated files accessible for upload even if Mininet runs as root."""
    try:
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
    except OSError:
        pass

    # When launched via sudo, restore ownership to the invoking desktop user.
    if os.geteuid() == 0:
        try:
            uid = int(os.environ.get("SUDO_UID", "0"))
            gid = int(os.environ.get("SUDO_GID", "0"))
            if uid > 0:
                os.chown(path, uid, gid if gid > 0 else -1)
        except (OSError, ValueError):
            pass


def _prepare_output_tree(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    # Make parent and run directory traversable/readable for the normal user.
    for candidate in (OUTPUT_ROOT, directory):
        if candidate.exists():
            _make_user_readable(candidate)


def _check_host_tools(net, host_names: List[str]) -> Dict[str, Any]:
    hosts = {}
    errors = []
    for name in host_names:
        try:
            host = net.get(name)
        except Exception as exc:
            errors.append(f"Mininet host {name!r} not found: {exc}")
            continue

        ip = host.IP()
        if not ip:
            errors.append(f"Host {name} has no IPv4 address.")
            continue

        tool = host.cmd("command -v iperf3").strip()
        if not tool:
            errors.append(
                f"iperf3 is not installed/available in host {name}. "
                "Install it in the Ubuntu VM with: sudo apt install iperf3"
            )
            continue
        hosts[name] = host

    if errors:
        raise RuntimeError("\n".join(errors))
    return hosts


def _load_iperf_json(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    start = raw.find("{")
    if start < 0:
        raise ValueError(raw.strip()[-1000:] or "iperf3 produced no JSON output")
    decoder = json.JSONDecoder()
    parsed, _ = decoder.raw_decode(raw[start:])
    return parsed


def _as_mbps(section: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(section, dict):
        return None
    value = section.get("bits_per_second")
    try:
        return float(value) / 1_000_000.0 if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_iperf_result(path: Path, returncode: Optional[int]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "sent_mbps": None,
        "received_mbps": None,
        "udp_packet_loss_percent": None,
        "udp_jitter_ms": None,
        "status": "error",
        "error": "",
    }
    try:
        data = _load_iperf_json(path)
    except Exception as exc:
        result["error"] = f"Could not parse iperf3 JSON: {exc}"
        return result

    if data.get("error"):
        result["error"] = str(data["error"])
        return result

    end = data.get("end", {})
    sent = end.get("sum_sent") or end.get("sum") or {}
    received = end.get("sum_received") or end.get("sum") or {}

    result["sent_mbps"] = _as_mbps(sent)
    result["received_mbps"] = _as_mbps(received)

    for key in ("lost_percent", "jitter_ms"):
        if received.get(key) is not None:
            try:
                result["udp_packet_loss_percent" if key == "lost_percent" else "udp_jitter_ms"] = float(received[key])
            except (TypeError, ValueError):
                pass

    if returncode == 0 and result["received_mbps"] is not None:
        result["status"] = "ok"
    else:
        result["status"] = "error"
        if not result["error"]:
            result["error"] = f"iperf3 process return code: {returncode}"
    return result


def _terminate_process(proc, label: str) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2.0)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except Exception:
            pass


def _wait_for_clients(jobs: List[Dict[str, Any]], timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        running = [job for job in jobs if job["process"].poll() is None]
        if not running:
            return
        if time.monotonic() >= deadline:
            for job in running:
                _terminate_process(job["process"], job["label"])
        time.sleep(0.2)


def _write_phase(
    path: Path,
    run_id: str,
    phase: str,
    start_epoch: float,
    end_epoch: float,
    intended_duration: float,
    note: str,
    controller_snapshot_path: str,
) -> None:
    fields = [
        "run_id", "phase", "start_utc", "end_utc",
        "start_epoch", "end_epoch", "elapsed_seconds",
        "intended_duration_seconds", "controller_snapshot_path", "note",
    ]
    _append_csv(path, fields, {
        "run_id": run_id,
        "phase": phase,
        "start_utc": dt.datetime.fromtimestamp(start_epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_utc": dt.datetime.fromtimestamp(end_epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start_epoch": f"{start_epoch:.3f}",
        "end_epoch": f"{end_epoch:.3f}",
        "elapsed_seconds": f"{end_epoch - start_epoch:.3f}",
        "intended_duration_seconds": intended_duration,
        "controller_snapshot_path": controller_snapshot_path,
        "note": note,
    })


def _run_load_profile(
    net,
    hosts: Dict[str, Any],
    profile: str,
    target_mbps: int,
    duration_seconds: int,
    ports: Dict[str, int],
    run_dir: Path,
    summary_path: Path,
    run_id: str,
) -> List[Dict[str, Any]]:
    flows = []
    for src, dst in FLOW_LAYOUT:
        dst_ip = hosts[dst].IP()
        port = ports[dst]
        log_path = run_dir / f"iperf_{profile}_{src}_to_{dst}.json"
        cmd = [
            "iperf3", "-c", dst_ip,
            "-u", "-b", f"{target_mbps}M",
            "-t", str(duration_seconds), "-i", "1",
            "-p", str(port), "-J",
        ]
        flows.append({
            "src": src, "dst": dst, "dst_ip": dst_ip,
            "port": port, "log_path": log_path, "cmd": cmd,
        })

    start_epoch = time.time()
    start_mono = time.monotonic()
    jobs = []

    # All configured clients start concurrently, making each profile a repeatable
    # aggregate offered-load level instead of sequential tests.
    try:
        for flow in flows:
            log_handle = flow["log_path"].open("w", encoding="utf-8")
            try:
                process = hosts[flow["src"]].popen(
                    flow["cmd"],
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                )
            finally:
                log_handle.close()
            jobs.append({
                "process": process,
                "label": f"{flow['src']}->{flow['dst']} ({profile})",
                "flow": flow,
            })

        _wait_for_clients(jobs, duration_seconds + PROCESS_TIMEOUT_GRACE_SECONDS)
    finally:
        # Do not leave a client running if the user presses Ctrl+C or a launch fails.
        for job in jobs:
            _terminate_process(job["process"], job["label"])

    end_epoch = time.time()
    actual_duration = time.monotonic() - start_mono

    rows: List[Dict[str, Any]] = []
    fields = [
        "run_id", "profile", "target_mbps_per_flow", "target_aggregate_mbps",
        "source_host", "destination_host", "destination_ip", "server_port",
        "requested_duration_seconds", "profile_start_utc", "profile_end_utc",
        "profile_elapsed_seconds", "process_returncode",
        "sent_mbps", "received_mbps", "udp_packet_loss_percent",
        "udp_jitter_ms", "status", "error", "iperf_json_path",
    ]

    for job in jobs:
        flow = job["flow"]
        proc = job["process"]
        measurement = _extract_iperf_result(flow["log_path"], proc.returncode)
        row = {
            "run_id": run_id,
            "profile": profile,
            "target_mbps_per_flow": target_mbps,
            "target_aggregate_mbps": target_mbps * len(FLOW_LAYOUT),
            "source_host": flow["src"],
            "destination_host": flow["dst"],
            "destination_ip": flow["dst_ip"],
            "server_port": flow["port"],
            "requested_duration_seconds": duration_seconds,
            "profile_start_utc": dt.datetime.fromtimestamp(start_epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "profile_end_utc": dt.datetime.fromtimestamp(end_epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "profile_elapsed_seconds": f"{actual_duration:.3f}",
            "process_returncode": proc.returncode,
            "sent_mbps": measurement["sent_mbps"],
            "received_mbps": measurement["received_mbps"],
            "udp_packet_loss_percent": measurement["udp_packet_loss_percent"],
            "udp_jitter_ms": measurement["udp_jitter_ms"],
            "status": measurement["status"],
            "error": measurement["error"],
            "iperf_json_path": str(flow["log_path"]),
        }
        _append_csv(summary_path, fields, row)
        rows.append(row)
    return rows


def run_demo(
    net,
    baseline_seconds: int = DEFAULT_BASELINE_SECONDS,
    profile_seconds: int = DEFAULT_PROFILE_SECONDS,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
) -> str:
    """
    Run the complete automated demo from the Mininet CLI.

    Returns the absolute directory containing the results.
    The function blocks the Mininet CLI for about 80 seconds by default;
    OS-Ken and its independent monitoring process continue running.
    """
    if min(baseline_seconds, profile_seconds, cooldown_seconds) < 0:
        raise ValueError("Durations must be non-negative.")
    if profile_seconds < 5:
        raise ValueError("profile_seconds must be at least 5 seconds.")

    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    run_dir = OUTPUT_ROOT / f"run_{run_id}"
    _prepare_output_tree(run_dir)

    phase_path = run_dir / "traffic_demo_phases.csv"
    summary_path = run_dir / "traffic_demo_summary.csv"
    manifest_path = run_dir / "manifest.json"
    controller_snapshot = Path("/tmp/sdn_energy_cycle.json")

    # Use a high, per-run port range to avoid clashing with the controller's
    # rotating iperf3 measurement port and with a prior interrupted demo.
    port_start = 54000 + random.SystemRandom().randrange(0, 900)
    ports = {dst: port_start + index for index, (_, dst) in enumerate(FLOW_LAYOUT)}

    host_names = sorted(set(name for flow in FLOW_LAYOUT for name in flow))
    hosts = _check_host_tools(net, host_names)

    controller_info = {}
    try:
        snapshot = json.loads(controller_snapshot.read_text(encoding="utf-8"))
        snapshot_age = time.time() - float(snapshot.get("timestamp_epoch", 0))
        if snapshot_age < 20:
            controller_info = {
                "session_id": snapshot.get("session_id"),
                "cycle": snapshot.get("cycle"),
                "phase": snapshot.get("phase"),
                "snapshot_age_seconds_at_start": round(snapshot_age, 3),
            }
    except Exception:
        pass

    manifest = {
        "run_id": run_id,
        "created_utc": _utc_now(),
        "description": "Automated Mininet offered-load demonstration; not a physical power measurement.",
        "topology_pairs": [{"source": src, "destination": dst} for src, dst in FLOW_LAYOUT],
        "profiles": [{"name": name, "target_mbps_per_flow": rate,
                      "target_aggregate_mbps": rate * len(FLOW_LAYOUT)}
                     for name, rate in PROFILES],
        "baseline_seconds": baseline_seconds,
        "profile_seconds": profile_seconds,
        "cooldown_seconds": cooldown_seconds,
        "iperf_ports": ports,
        "controller_snapshot_at_start": controller_info,
        "controller_snapshot_path": str(controller_snapshot),
        "phase_file": str(phase_path),
        "summary_file": str(summary_path),
        "note": (
            "Target rates are offered UDP rates, not guaranteed delivered throughput. "
            "The application controller/QoS monitor runs independently. Join phases "
            "to its CSVs using timestamps/session_id. Workload and monitor traffic "
            "are not yet classified separately."
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    servers = []
    server_handles = []
    try:
        print(f"\n[traffic-demo] Run ID: {run_id}", flush=True)
        print(f"[traffic-demo] Results: {run_dir}", flush=True)
        print(
            f"[traffic-demo] Checking and starting {len(FLOW_LAYOUT)} inter-pod iperf3 servers...",
            flush=True,
        )

        for src, dst in FLOW_LAYOUT:
            log_path = run_dir / f"iperf_server_{dst}.log"
            handle = log_path.open("w", encoding="utf-8")
            server_handles.append(handle)
            proc = hosts[dst].popen(
                ["iperf3", "-s", "-p", str(ports[dst])],
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
            servers.append({"process": proc, "destination": dst, "log": log_path})
        for handle in server_handles:
            handle.flush()
            handle.close()
        server_handles.clear()

        time.sleep(SERVER_STARTUP_SECONDS)
        failed_servers = [s for s in servers if s["process"].poll() is not None]
        if failed_servers:
            detail = []
            for server in failed_servers:
                try:
                    detail.append(f"{server['destination']}: {server['log'].read_text(errors='replace')[-600:]}")
                except Exception:
                    pass
            raise RuntimeError(
                "An iperf3 server failed to start. Details:\n" + "\n".join(detail)
            )

        # Baseline: servers listen, but no generated demo clients are sending.
        print(f"[traffic-demo] Baseline: {baseline_seconds}s, no demo workload.", flush=True)
        p0 = time.time()
        time.sleep(baseline_seconds)
        p1 = time.time()
        _write_phase(
            phase_path, run_id, "baseline", p0, p1, baseline_seconds,
            "No demo workload; controller/QoS monitoring traffic may still occur.",
            str(controller_snapshot),
        )

        # Run each load profile; all configured flow clients run concurrently.
        for profile, rate in PROFILES:
            print(
                f"\n[traffic-demo] {profile.upper()}: starting {len(FLOW_LAYOUT)} concurrent UDP flows "
                f"at {rate} Mbps each (target aggregate {rate * len(FLOW_LAYOUT)} Mbps) "
                f"for {profile_seconds}s.",
                flush=True,
            )
            phase_start = time.time()
            rows = _run_load_profile(
                net, hosts, profile, rate, profile_seconds,
                ports, run_dir, summary_path, run_id,
            )
            phase_end = time.time()
            _write_phase(
                phase_path, run_id, profile, phase_start, phase_end, profile_seconds,
                f"Three concurrent UDP flows at {rate} Mbps target per flow.",
                str(controller_snapshot),
            )
            for row in rows:
                print(
                    f"[traffic-demo] {row['source_host']}->{row['destination_host']}: "
                    f"sent={row['sent_mbps']} Mbps, received={row['received_mbps']} Mbps, "
                    f"loss={row['udp_packet_loss_percent']}%, status={row['status']}"
                    + (f", error={row['error']}" if row["error"] else ""),
                    flush=True,
                )
            if any(row["status"] != "ok" for row in rows):
                print(
                    "[traffic-demo] WARNING: at least one flow failed; inspect its JSON log "
                    "in the results directory.",
                    flush=True,
                )

        print(f"\n[traffic-demo] Cooldown: {cooldown_seconds}s, no demo workload.", flush=True)
        c0 = time.time()
        time.sleep(cooldown_seconds)
        c1 = time.time()
        _write_phase(
            phase_path, run_id, "cooldown", c0, c1, cooldown_seconds,
            "No demo workload; controller/QoS monitoring traffic may still occur.",
            str(controller_snapshot),
        )

        manifest["completed_utc"] = _utc_now()
        manifest["status"] = "completed"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print("\n[traffic-demo] Completed.", flush=True)
        print(f"[traffic-demo] Phase windows: {phase_path}", flush=True)
        print(f"[traffic-demo] Throughput results: {summary_path}", flush=True)
        print("[traffic-demo] The OS-Ken/QoS CSV files remain in the project's normal data/ directory.", flush=True)
        return str(run_dir)

    except KeyboardInterrupt:
        manifest["completed_utc"] = _utc_now()
        manifest["status"] = "interrupted"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print("\n[traffic-demo] Interrupted; cleaning up launched clients and servers.", flush=True)
        raise
    except Exception as exc:
        manifest["completed_utc"] = _utc_now()
        manifest["status"] = "error"
        manifest["error"] = str(exc)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"\n[traffic-demo] ERROR: {exc}", flush=True)
        raise
    finally:
        for handle in server_handles:
            try:
                handle.close()
            except Exception:
                pass
        for server in servers:
            _terminate_process(server["process"], f"server-{server['destination']}")

        # Ensure a root-started Mininet run leaves outputs usable by the desktop user.
        for path in [OUTPUT_ROOT, run_dir]:
            if path.exists():
                _make_user_readable(path)
        for path in run_dir.glob("*"):
            if path.is_file():
                _make_user_readable(path)


if __name__ == "__main__":
    print(
        "This module must be imported from the Mininet CLI, where the Mininet "
        "`net` object exists.\n"
        "Run:\n"
        "  mininet> px from algorithm.traffic_demo import run_demo\n"
        "  mininet> px run_demo(net)"
    )
