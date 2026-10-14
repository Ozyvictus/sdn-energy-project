"""CSV recorder for traffic-active port counts, kept separate from energy."""

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


def record_active_port_metrics(
    *,
    cycle: int,
    phase: str,
    active_ports_by_switch: Mapping[int, int],
    session_id: str,
    data_dir: Optional[str] = None,
) -> None:
    """Append per-switch and network-wide active-port counts for one cycle."""
    output_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "active_port_metrics.csv"
    now = datetime.now(timezone.utc).isoformat()
    total = sum(int(count) for count in active_ports_by_switch.values())
    rows = [
        {
            "session_id": session_id,
            "cycle": int(cycle),
            "phase": phase,
            "timestamp_utc": now,
            "scope": "switch",
            "node": f"s{dpid}",
            "active_ports": int(count),
        }
        for dpid, count in sorted(active_ports_by_switch.items())
    ]
    rows.append({
        "session_id": session_id,
        "cycle": int(cycle),
        "phase": phase,
        "timestamp_utc": now,
        "scope": "network",
        "node": "network",
        "active_ports": total,
    })
    fields = ["session_id", "cycle", "phase", "timestamp_utc", "scope", "node", "active_ports"]
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
