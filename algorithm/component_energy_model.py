"""State-based network component power and energy model.

This module deliberately does not use byte counters, packet counters, or
active-port statistics to infer power. It estimates power only from the
configured operational state of switches and inter-switch links.

Default state power values are taken from the simulation parameters in:
S. Behera, N. Panda, and S. S. Patra, "Dynamic Flow Rule Placement for
Real-Time Energy Optimization in SDN," Computers, vol. 15, no. 6, 349, 2026,
doi:10.3390/computers15060349, Table 4 (switch active/sleep: 300/20 W;
link active/sleep: 50/1 W).

These are literature-derived simulation parameters, not power measurements
of Mininet or Open vSwitch. Treat the results as a modeled comparison, not
physical watt-meter readings. The source paper's values describe its own
simulation scenario and may not represent the actual equipment used in a
particular deployment.
"""

from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"

# Simulation parameters from Behera et al. (2026), Table 4.
DEFAULT_SWITCH_ACTIVE_W = 300.0
DEFAULT_SWITCH_SLEEP_W = 20.0
DEFAULT_LINK_ACTIVE_W = 50.0
DEFAULT_LINK_SLEEP_W = 1.0

ACTIVE = "active"
SLEEP = "sleep"
VALID_STATES = {ACTIVE, SLEEP}

LinkLike = Union[str, Sequence[str], Tuple[str, str]]


def _switch_name(value: object) -> str:
    name = str(value).strip().lower()
    if name.isdigit():
        name = f"s{name}"
    if not re.fullmatch(r"s\d+", name):
        raise ValueError(f"Invalid switch name: {value!r}; expected e.g. 's1'")
    return name


def canonical_link(link: LinkLike, node_b: Optional[str] = None) -> str:
    """Return a stable inter-switch link name such as ``s1-s3``."""
    if node_b is not None:
        nodes = (str(link), str(node_b))
    elif isinstance(link, str):
        parts = link.lower().split("-")
        if len(parts) != 2:
            raise ValueError(f"Invalid link {link!r}; expected 's1-s3'")
        nodes = (parts[0], parts[1])
    else:
        if len(link) != 2:
            raise ValueError(f"Invalid link {link!r}; expected two endpoints")
        nodes = (str(link[0]), str(link[1]))

    a, b = sorted((_switch_name(nodes[0]), _switch_name(nodes[1])))
    if a == b:
        raise ValueError("A link must connect two different switches")
    return f"{a}-{b}"


def _configured_switches_and_links() -> Tuple[set[str], set[str]]:
    """Infer the initially expected inter-switch topology from config.py."""
    switches: set[str] = set()
    links: set[str] = set()
    try:
        from config import LINK_CAPACITY_MBPS  # type: ignore
    except Exception:
        LINK_CAPACITY_MBPS = {}

    for endpoints in getattr(LINK_CAPACITY_MBPS, "keys", lambda: [])():
        if not isinstance(endpoints, (tuple, list)) or len(endpoints) != 2:
            continue
        a, b = (str(endpoints[0]).lower(), str(endpoints[1]).lower())
        if re.fullmatch(r"s\d+", a) and re.fullmatch(r"s\d+", b) and a != b:
            switches.update((a, b))
            links.add(canonical_link((a, b)))

    # Optional override, useful if the topology later changes.
    try:
        from config import NETWORK_SWITCH_NAMES  # type: ignore
        switches.update(_switch_name(s) for s in NETWORK_SWITCH_NAMES)
    except Exception:
        pass
    return switches, links


class ComponentEnergyModel:
    """Estimate per-cycle network power/energy from component states.

    By default every expected switch and configured inter-switch fabric link starts ACTIVE.
    Host-facing access links are intentionally omitted because this project keeps
    those links available to serve hosts rather than treating them as sleep targets.
    The optimizer must explicitly call ``set_switch_state`` or
    ``set_link_state`` to transition a component into SLEEP. Newly discovered
    components are added as ACTIVE, and previously known states are retained
    if topology discovery temporarily stops reporting a component.
    """

    def __init__(
        self,
        *,
        switch_active_w: float = DEFAULT_SWITCH_ACTIVE_W,
        switch_sleep_w: float = DEFAULT_SWITCH_SLEEP_W,
        link_active_w: float = DEFAULT_LINK_ACTIVE_W,
        link_sleep_w: float = DEFAULT_LINK_SLEEP_W,
        data_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        for label, value in (
            ("switch_active_w", switch_active_w),
            ("switch_sleep_w", switch_sleep_w),
            ("link_active_w", link_active_w),
            ("link_sleep_w", link_sleep_w),
        ):
            if not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{label} must be a non-negative number")

        self.switch_active_w = float(switch_active_w)
        self.switch_sleep_w = float(switch_sleep_w)
        self.link_active_w = float(link_active_w)
        self.link_sleep_w = float(link_sleep_w)
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.csv_path = self.data_dir / "component_energy.csv"

        expected_switches, expected_links = _configured_switches_and_links()
        self.switch_states: Dict[str, str] = {s: ACTIVE for s in expected_switches}
        self.link_states: Dict[str, str] = {link: ACTIVE for link in expected_links}
        self.session_cumulative_energy_wh = 0.0

    def sync_topology(
        self,
        switches: Iterable[object],
        links: Iterable[LinkLike],
    ) -> None:
        """Register discovered topology without deriving state from traffic.

        This uses union semantics on purpose: an intentionally sleeping link
        may disappear from topology discovery, but its modeled state should
        remain SLEEP until the optimizer wakes it.
        """
        for switch in switches:
            name = _switch_name(switch)
            self.switch_states.setdefault(name, ACTIVE)
        for link in links:
            name = canonical_link(link)
            self.link_states.setdefault(name, ACTIVE)

    def set_switch_state(self, switch: object, state: str) -> None:
        name = _switch_name(switch)
        normalized = str(state).strip().lower()
        if normalized not in VALID_STATES:
            raise ValueError(f"Switch state must be one of {sorted(VALID_STATES)}")
        self.switch_states[name] = normalized

    def set_link_state(self, link: LinkLike, state: str, node_b: Optional[str] = None) -> None:
        name = canonical_link(link, node_b)
        normalized = str(state).strip().lower()
        if normalized not in VALID_STATES:
            raise ValueError(f"Link state must be one of {sorted(VALID_STATES)}")
        self.link_states[name] = normalized

    def get_component_states(self) -> dict:
        return {
            "switches": dict(sorted(self.switch_states.items())),
            "links": dict(sorted(self.link_states.items())),
        }

    def calculate_power(self) -> dict:
        active_switches = sum(s == ACTIVE for s in self.switch_states.values())
        sleeping_switches = sum(s == SLEEP for s in self.switch_states.values())
        active_links = sum(s == ACTIVE for s in self.link_states.values())
        sleeping_links = sum(s == SLEEP for s in self.link_states.values())

        switch_active_power_w = active_switches * self.switch_active_w
        switch_sleep_power_w = sleeping_switches * self.switch_sleep_w
        link_active_power_w = active_links * self.link_active_w
        link_sleep_power_w = sleeping_links * self.link_sleep_w
        total_power_w = (
            switch_active_power_w
            + switch_sleep_power_w
            + link_active_power_w
            + link_sleep_power_w
        )
        return {
            "switches_total": len(self.switch_states),
            "switches_active": active_switches,
            "switches_sleeping": sleeping_switches,
            "links_total": len(self.link_states),
            "links_active": active_links,
            "links_sleeping": sleeping_links,
            "switch_active_power_w": switch_active_power_w,
            "switch_sleep_power_w": switch_sleep_power_w,
            "link_active_power_w": link_active_power_w,
            "link_sleep_power_w": link_sleep_power_w,
            "total_power_w": total_power_w,
        }

    def record_cycle(
        self,
        *,
        cycle: int,
        duration_seconds: float,
        phase: str,
        session_id: str,
    ) -> dict:
        """Integrate estimated energy over a cycle and append one CSV row."""
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if phase not in {"energy", "qos"}:
            raise ValueError("phase must be 'energy' or 'qos'")

        breakdown = self.calculate_power()
        interval_energy_wh = breakdown["total_power_w"] * duration_seconds / 3600.0
        self.session_cumulative_energy_wh += interval_energy_wh
        result = {
            "session_id": session_id,
            "cycle": int(cycle),
            "phase": phase,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": float(duration_seconds),
            **breakdown,
            "interval_energy_wh": interval_energy_wh,
            "cumulative_energy_wh": self.session_cumulative_energy_wh,
            "component_states_json": json.dumps(self.get_component_states(), sort_keys=True),
            "model": "component_operational_state",
            "is_physical_measurement": False,
        }
        self._append_csv(result)
        return result

    def _append_csv(self, row: Mapping[str, object]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fields = [
            "session_id", "cycle", "phase", "timestamp_utc", "duration_seconds",
            "switches_total", "switches_active", "switches_sleeping",
            "links_total", "links_active", "links_sleeping",
            "switch_active_power_w", "switch_sleep_power_w",
            "link_active_power_w", "link_sleep_power_w", "total_power_w",
            "interval_energy_wh", "cumulative_energy_wh", "component_states_json",
            "model", "is_physical_measurement",
        ]
        write_header = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        with self.csv_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(row)


# Module-level helpers for controller or optimizer integrations that prefer
# a small API. Normally the controller should hold one ComponentEnergyModel.
def estimate_power_from_states(
    switch_states: Mapping[str, str],
    link_states: Mapping[LinkLike, str],
    *,
    switch_active_w: float = DEFAULT_SWITCH_ACTIVE_W,
    switch_sleep_w: float = DEFAULT_SWITCH_SLEEP_W,
    link_active_w: float = DEFAULT_LINK_ACTIVE_W,
    link_sleep_w: float = DEFAULT_LINK_SLEEP_W,
) -> dict:
    model = ComponentEnergyModel(
        switch_active_w=switch_active_w,
        switch_sleep_w=switch_sleep_w,
        link_active_w=link_active_w,
        link_sleep_w=link_sleep_w,
        data_dir=Path(os.devnull).parent / "__energy_model_no_output__",
    )
    model.switch_states = {_switch_name(k): str(v).lower() for k, v in switch_states.items()}
    model.link_states = {canonical_link(k): str(v).lower() for k, v in link_states.items()}
    invalid = (set(model.switch_states.values()) | set(model.link_states.values())) - VALID_STATES
    if invalid:
        raise ValueError(f"Invalid component states: {sorted(invalid)}")
    return model.calculate_power()
