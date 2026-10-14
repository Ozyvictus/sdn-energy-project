"""Network-wide QoS monitor for a Mininet topology.

The controller publishes a JSON cycle snapshot to /tmp/sdn_energy_cycle.json.
The snapshot has alternating phases: cycle 1 = energy, cycle 2 = qos, etc.
This monitor runs the full host-pair ping sweep only on QoS phases. The same
5-second scheduler is therefore shared, without launching active probes in
an energy phase. Eight hosts yield 28 unique unordered host pairs.

For throughput, one host pair is tested per QoS phase using iperf3. This is a
rotating end-to-end sample, not simultaneous throughput for all 28 pairs. It
is optional and enabled by default. Link throughput remains available from
OpenFlow port counters independently of this active test.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
QOS_REQUEST_PATH = "/tmp/sdn_energy_qos_request.json"
FILE_POLL_SECONDS = 0.10
PING_COUNT = 1
PING_WAIT_SECONDS = 1
MAX_CONCURRENT_PINGS = 28
QOS_SLOT_BUDGET_SECONDS = 4.0
ENABLE_ROTATING_IPERF = True
IPERF_DURATION_SECONDS = 1
IPERF_PORT = 5201
IPERF_CONNECT_TIMEOUT_MS = 700

PING_FIELDS = [
    "session_id", "cycle", "timestamp_utc", "src_host", "dst_host",
    "src_ip", "dst_ip", "latency_ms", "jitter_ms", "packet_loss_percent", "reachable",
    "packets_sent", "packets_received", "measurement_valid", "ping_output",
    "throughput_mbps", "throughput_test_status",
]
SUMMARY_FIELDS = [
    "session_id", "cycle", "timestamp_utc", "pairs_expected", "pairs_measured",
    "pairs_reachable", "average_rtt_ms", "p95_rtt_ms", "average_jitter_ms", "average_packet_loss_percent",
    "maximum_packet_loss_percent", "throughput_pair", "throughput_mbps",
    "sweep_duration_seconds", "active_probes_within_slot",
]


_PACKET_LOSS_RE = re.compile(r"(?P<loss>\d+(?:\.\d+)?)%\s*packet loss", re.IGNORECASE)
_PACKETS_RE = re.compile(
    r"(?P<sent>\d+)\s+packets transmitted,\s*(?P<received>\d+)\s+(?:packets\s+)?received",
    re.IGNORECASE,
)
_RTT_RE = re.compile(
    r"(?:rtt|round-trip)\s+min/avg/max/(?:mdev|stddev)\s*=\s*"
    r"[\d.]+/([\d.]+)/[\d.]+/([\d.]+)\s*ms",
    re.IGNORECASE,
)


def parse_ping_output(output: str) -> dict:
    """Parse standard Ubuntu iputils-ping summary output."""
    loss_match = _PACKET_LOSS_RE.search(output)
    packet_match = _PACKETS_RE.search(output)
    rtt_match = _RTT_RE.search(output)
    sent = int(packet_match.group("sent")) if packet_match else None
    received = int(packet_match.group("received")) if packet_match else None
    loss = float(loss_match.group("loss")) if loss_match else None
    latency = float(rtt_match.group(1)) if rtt_match else None
    jitter = float(rtt_match.group(2)) if rtt_match else None

    # Loss can still be validly measured at 100% even when Linux omits an RTT line.
    valid = loss is not None and sent is not None and received is not None
    return {
        "latency_ms": latency,
        "jitter_ms": jitter,
        "packet_loss_percent": loss,
        "packets_sent": sent,
        "packets_received": received,
        "reachable": bool(received and received > 0),
        "measurement_valid": valid,
        "ping_output": output.strip()[-1500:],
    }


def _ip(host: Any) -> str:
    value = host.IP()
    if not value:
        raise ValueError(f"Host {getattr(host, 'name', '<unknown>')} has no IP address")
    return str(value)


def measure_latency_loss(
    src_host: Any,
    dst_ip: str,
    *,
    count: int = PING_COUNT,
    timeout_seconds: int = PING_WAIT_SECONDS,
) -> dict:
    """Run ping via Host.popen (not shared interactive host.cmd shell)."""
    cmd = [
        "ping", "-n", "-c", str(int(count)), "-W", str(int(timeout_seconds)), str(dst_ip)
    ]
    proc = None
    output = ""
    try:
        proc = src_host.popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        output, _ = proc.communicate(timeout=max(2.0, count * timeout_seconds + 1.0))
    except subprocess.TimeoutExpired:
        if proc is not None:
            proc.kill()
            output, _ = proc.communicate()
        output = (output or "") + "\nPing process timed out."
    except Exception as exc:
        output = f"Unable to run ping: {type(exc).__name__}: {exc}"
    result = parse_ping_output(output or "")
    result["success"] = bool(result["measurement_valid"])
    return result


def _measure_throughput(src_host: Any, dst_host: Any, *, duration_seconds: int = IPERF_DURATION_SECONDS) -> dict:
    """Run one controlled TCP iperf3 test; always clean up our server process."""
    server = None
    client = None
    try:
        dst_ip = _ip(dst_host)
        server = dst_host.popen(
            ["iperf3", "-s", "-1", "-p", str(IPERF_PORT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # Give the server a brief opportunity to bind. Any startup race is returned
        # as an explicit failed measurement rather than silently reusing a stale server.
        time.sleep(0.25)
        client = src_host.popen(
            [
                "iperf3", "-c", dst_ip, "-t", str(int(duration_seconds)), "-J",
                "-p", str(IPERF_PORT), "--connect-timeout", str(IPERF_CONNECT_TIMEOUT_MS),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        output, _ = client.communicate(timeout=duration_seconds + 1.5)
        start = output.find("{")
        if start < 0:
            return {"throughput_mbps": None, "status": "error", "error": output.strip()[-1000:]}
        payload = json.loads(output[start:])
        if payload.get("error"):
            return {"throughput_mbps": None, "status": "error", "error": str(payload["error"])}
        end = payload.get("end", {})
        summary = end.get("sum_received") or end.get("sum") or end.get("sum_sent")
        if not summary or summary.get("bits_per_second") is None:
            return {"throughput_mbps": None, "status": "error", "error": "No throughput summary in iperf3 JSON"}
        return {
            "throughput_mbps": float(summary["bits_per_second"]) / 1_000_000.0,
            "status": "ok",
            "error": None,
        }
    except subprocess.TimeoutExpired:
        if client is not None:
            client.kill()
            try:
                client.communicate(timeout=1)
            except Exception:
                pass
        return {"throughput_mbps": None, "status": "timeout", "error": "iperf3 client timed out"}
    except FileNotFoundError as exc:
        return {"throughput_mbps": None, "status": "missing_dependency", "error": str(exc)}
    except Exception as exc:
        return {"throughput_mbps": None, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if client is not None and client.poll() is None:
            client.kill()
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.communicate(timeout=1)
            except Exception:
                server.kill()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_rows(path: Path, fields: Sequence[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    if not rows:
        return
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def _percentile(values: List[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


class QoSMonitor:
    """Poll controller snapshots and measure all unique host pairs on QoS phases."""

    def __init__(
        self,
        net: Any,
        *,
        snapshot_path: str = QOS_REQUEST_PATH,
        data_dir: Optional[str] = None,
        enable_rotating_iperf: bool = ENABLE_ROTATING_IPERF,
        iperf_duration_seconds: int = IPERF_DURATION_SECONDS,
    ) -> None:
        self.net = net
        self.snapshot_path = Path(snapshot_path)
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.pair_csv = self.data_dir / "qos_pair_measurements.csv"
        self.summary_csv = self.data_dir / "qos_cycle_summary.csv"
        self.enable_rotating_iperf = bool(enable_rotating_iperf)
        self.iperf_duration_seconds = int(iperf_duration_seconds)
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.last_marker: Optional[Tuple[str, int]] = None
        self.qos_sequence = 0
        self.logger_prefix = "[QoS Monitor]"

    def start(self) -> "QoSMonitor":
        if self.thread and self.thread.is_alive():
            return self
        self.thread = threading.Thread(target=self._loop, name="qos-monitor", daemon=True)
        self.thread.start()
        print(f"{self.logger_prefix} started; watching QoS requests at {self.snapshot_path}")
        return self

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        print(f"{self.logger_prefix} stopped")

    def _read_snapshot(self) -> Optional[dict]:
        try:
            with self.snapshot_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict) or "cycle" not in data or "session_id" not in data:
                return None
            return data
        except (OSError, json.JSONDecodeError):
            return None

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            snapshot = self._read_snapshot()
            if snapshot:
                try:
                    marker = (str(snapshot["session_id"]), int(snapshot["cycle"]))
                except (KeyError, ValueError, TypeError):
                    marker = None
                if marker and marker != self.last_marker:
                    self.last_marker = marker
                    if snapshot.get("phase") == "qos":
                        age = time.time() - float(snapshot.get("timestamp_epoch", time.time()))
                        # Requests are published at QoS-slot start. Ignore stale requests
                        # if this worker was busy or started after that slot had passed.
                        if 0.0 <= age <= 2.0:
                            self.qos_sequence += 1
                            try:
                                self.run_cycle(snapshot, self.qos_sequence)
                            except Exception as exc:
                                print(f"{self.logger_prefix} cycle failed: {type(exc).__name__}: {exc}")
            self.stop_event.wait(FILE_POLL_SECONDS)

    def _hosts(self) -> List[Any]:
        hosts = list(getattr(self.net, "hosts", []))
        hosts.sort(key=lambda h: str(getattr(h, "name", "")))
        return hosts

    def run_cycle(self, snapshot: dict, qos_sequence: Optional[int] = None) -> dict:
        hosts = self._hosts()
        if len(hosts) < 2:
            raise RuntimeError(f"QoS monitor needs at least two Mininet hosts; got {len(hosts)}")
        pairs = [(hosts[i], hosts[j]) for i in range(len(hosts)) for j in range(i + 1, len(hosts))]
        cycle = int(snapshot["cycle"])
        session_id = str(snapshot["session_id"])
        start_time = time.monotonic()
        print(f"{self.logger_prefix} cycle={cycle}: measuring {len(pairs)} unique host pairs")

        results: List[dict] = []
        workers = max(1, min(MAX_CONCURRENT_PINGS, len(pairs)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(self._measure_pair, src, dst): (src, dst)
                for src, dst in pairs
            }
            for future in as_completed(future_map):
                src, dst = future_map[future]
                try:
                    row = future.result()
                except Exception as exc:
                    row = {
                        "src_host": src.name, "dst_host": dst.name,
                        "src_ip": _ip(src) if _safe_ip(src) else "",
                        "dst_ip": _ip(dst) if _safe_ip(dst) else "",
                        "latency_ms": None, "jitter_ms": None, "packet_loss_percent": None,
                        "reachable": False, "packets_sent": None, "packets_received": None,
                        "measurement_valid": False,
                        "ping_output": f"{type(exc).__name__}: {exc}",
                        "throughput_mbps": None, "throughput_test_status": "not_tested",
                    }
                results.append(row)

        # One rotating, active throughput test per QoS phase. It starts only after
        # all pairwise ping probes have completed so it does not contaminate those RTT samples.
        throughput_pair_name = ""
        throughput_mbps = None
        elapsed_before_iperf = time.monotonic() - start_time
        active_probes_within_slot = elapsed_before_iperf <= QOS_SLOT_BUDGET_SECONDS
        # Reserve time for a one-second test, server startup and cleanup so the
        # active probe train is unlikely to spill into the next energy phase.
        enough_time_for_iperf = (
            QOS_SLOT_BUDGET_SECONDS - elapsed_before_iperf
            >= IPERF_DURATION_SECONDS + 1.0
        )
        if self.enable_rotating_iperf and pairs and active_probes_within_slot and enough_time_for_iperf:
            pair_index = ((qos_sequence or self.qos_sequence) - 1) % len(pairs)
            throughput_src, throughput_dst = pairs[pair_index]
            throughput_pair_name = f"{throughput_src.name}->{throughput_dst.name}"
            throughput_result = _measure_throughput(
                throughput_src, throughput_dst, duration_seconds=self.iperf_duration_seconds
            )
            throughput_mbps = throughput_result.get("throughput_mbps")
            for row in results:
                if row["src_host"] == throughput_src.name and row["dst_host"] == throughput_dst.name:
                    row["throughput_mbps"] = throughput_mbps
                    row["throughput_test_status"] = throughput_result.get("status", "error")
                    break
            print(
                f"{self.logger_prefix} throughput {throughput_pair_name}: "
                f"{throughput_mbps if throughput_mbps is not None else 'unavailable'} Mbps "
                f"({throughput_result.get('status')})"
            )
        else:
            skip_reason = (
                "slot_budget_exceeded"
                if self.enable_rotating_iperf and pairs and not enough_time_for_iperf
                else "disabled"
            )
            for row in results:
                row["throughput_mbps"] = None
                row["throughput_test_status"] = skip_reason

        # Stable output ordering regardless of future completion order.
        results.sort(key=lambda r: (r["src_host"], r["dst_host"]))
        now = _utc_now()
        for row in results:
            row.update({
                "session_id": session_id,
                "cycle": cycle,
                "timestamp_utc": now,
            })
        _append_rows(self.pair_csv, PING_FIELDS, results)

        valid_loss = [float(r["packet_loss_percent"]) for r in results if r.get("measurement_valid") and r.get("packet_loss_percent") is not None]
        valid_latency = [float(r["latency_ms"]) for r in results if r.get("latency_ms") is not None]
        valid_jitter = [float(r["jitter_ms"]) for r in results if r.get("jitter_ms") is not None]
        reachable = sum(bool(r.get("reachable")) for r in results)
        summary = {
            "session_id": session_id,
            "cycle": cycle,
            "timestamp_utc": now,
            "pairs_expected": len(pairs),
            "pairs_measured": sum(bool(r.get("measurement_valid")) for r in results),
            "pairs_reachable": reachable,
            "average_rtt_ms": (sum(valid_latency) / len(valid_latency)) if valid_latency else None,
            "p95_rtt_ms": _percentile(valid_latency, 95),
            "average_jitter_ms": (sum(valid_jitter) / len(valid_jitter)) if valid_jitter else None,
            "average_packet_loss_percent": (sum(valid_loss) / len(valid_loss)) if valid_loss else None,
            "maximum_packet_loss_percent": max(valid_loss) if valid_loss else None,
            "throughput_pair": throughput_pair_name,
            "throughput_mbps": throughput_mbps,
            "sweep_duration_seconds": round(time.monotonic() - start_time, 3),
            "active_probes_within_slot": (time.monotonic() - start_time) <= QOS_SLOT_BUDGET_SECONDS,
        }
        _append_rows(self.summary_csv, SUMMARY_FIELDS, [summary])
        print(
            f"{self.logger_prefix} cycle={cycle}: {reachable}/{len(pairs)} pairs reachable; "
            f"avg RTT={summary['average_rtt_ms']} ms; "
            f"avg loss={summary['average_packet_loss_percent']}%; "
            f"sweep={summary['sweep_duration_seconds']} s"
        )
        return {"pairs": results, "summary": summary}

    def _measure_pair(self, src: Any, dst: Any) -> dict:
        src_ip = _ip(src)
        dst_ip = _ip(dst)
        result = measure_latency_loss(src, dst_ip)
        return {
            "src_host": src.name,
            "dst_host": dst.name,
            "src_ip": src_ip,
            "dst_ip": dst_ip,
            "latency_ms": result.get("latency_ms"),
            "jitter_ms": result.get("jitter_ms"),
            "packet_loss_percent": result.get("packet_loss_percent"),
            "reachable": bool(result.get("reachable")),
            "packets_sent": result.get("packets_sent"),
            "packets_received": result.get("packets_received"),
            "measurement_valid": bool(result.get("measurement_valid")),
            "ping_output": result.get("ping_output", ""),
            "throughput_mbps": None,
            "throughput_test_status": "not_tested",
        }


def _safe_ip(host: Any) -> bool:
    try:
        return bool(_ip(host))
    except Exception:
        return False


def start_qos_monitor(net: Any, **kwargs: Any) -> QoSMonitor:
    """Start the monitor from the Mininet CLI: ``py qos_monitor = start_qos_monitor(net)``."""
    return QoSMonitor(net, **kwargs).start()
