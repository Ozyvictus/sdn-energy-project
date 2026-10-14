"""Apply the controller edits for the component energy model and alternating QoS phases.

Run from the project root:
    source venv/bin/activate
    python apply_controller_integration.py

The script backs up controller/monitor.py before modifying it. It deliberately
fails rather than guessing if the monitor source no longer matches the version
this integration targets. Inspect the resulting diff before starting OS-Ken.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path.cwd()
MONITOR = ROOT / "controller" / "monitor.py"
BACKUP = ROOT / "controller" / "monitor.py.before_component_energy.bak"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match for {label!r}, found {count}; no changes were written.")
    return text.replace(old, new, 1)


def replace_regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE | re.DOTALL)
    if count != 1:
        raise RuntimeError(f"Expected exactly one regex match for {label!r}, found {count}; no changes were written.")
    return updated


def main() -> None:
    if not MONITOR.exists():
        raise SystemExit(f"Cannot find {MONITOR}. Run this from your project root.")
    original = MONITOR.read_text(encoding="utf-8")
    if "ComponentEnergyModel" in original and "_publish_cycle_snapshot" in original:
        raise SystemExit("controller/monitor.py already appears to be integrated; refusing to patch twice.")

    code = original
    code = replace_once(
        code,
        "import time\n",
        "import time\nimport json\nimport os\nimport uuid\n",
        "standard-library imports",
    )
    code = replace_once(
        code,
        "from algorithm.energy_model import (\n    record_state,\n    generate_all_graphs\n)\n",
        "from algorithm.component_energy_model import ComponentEnergyModel\n"
        "from algorithm.active_port_metrics import record_active_port_metrics\n",
        "energy model imports",
    )
    # Keep the module docstring consistent with the state-based energy model.
    code = re.sub(
        r"Energy model:\s+Modeled Power = Active Ports × 0\.2 W\s+IMPORTANT:\s+This is a SOFTWARE MODEL, not a measurement of physical\s+electrical power\.",
        "Energy model:\n\n    Modeled power is calculated from configured switch/link operational states,\n    independently of traffic-active-port statistics.\n\nIMPORTANT:\n\n    Results are literature-parameterized estimates, not physical watt-meter readings.",
        code,
        count=1,
    )
    code = replace_once(
        code,
        "POLL_INTERVAL_SECONDS = 10",
        "POLL_INTERVAL_SECONDS = 5",
        "poll interval",
    )
    code = replace_once(
        code,
        "CYCLE_TIMEOUT_SECONDS = 3",
        "CYCLE_TIMEOUT_SECONDS = 2",
        "cycle timeout",
    )
    code = replace_once(
        code,
        "TRAFFIC_THRESHOLD_BYTES = 0\n",
        "TRAFFIC_THRESHOLD_BYTES = 0\n\nCYCLE_SNAPSHOT_PATH = \"/tmp/sdn_energy_cycle.json\"\nQOS_REQUEST_PATH = \"/tmp/sdn_energy_qos_request.json\"\n",
        "cycle snapshot path",
    )
    code = replace_once(
        code,
        "        self.cycle_number = 0\n        self.current_link_rates = {}\n",
        "        self.cycle_number = 0\n"
        "        self.current_link_rates = {}\n"
        "        self.session_id = uuid.uuid4().hex\n"
        "        self.component_energy_model = ComponentEnergyModel()\n",
        "component model initialization",
    )
    code = replace_once(
        code,
        "            self.cycle_number += 1\n",
        "            cycle_loop_started = time.monotonic()\n\n"
        "            self.cycle_number += 1\n",
        "cycle timer",
    )
    code = replace_once(
        code,
        '                "cycle": self.cycle_number,\n',
        '                "cycle": self.cycle_number,\n'
        '                "phase": "energy" if self.cycle_number % 2 == 1 else "qos",\n',
        "alternating cycle phase",
    )

    energy_section = r'''        # Record traffic-active port counts separately. These are traffic
        # observations, not the input to the component power model.
        record_active_port_metrics(
            cycle=cycle["cycle"],
            phase=cycle["phase"],
            active_ports_by_switch=cycle["active_ports"],
            session_id=self.session_id,
        )

        topology_switches = get_switch(self, None)
        topology_links = get_link(self, None)
        switch_names = [f"s{sw.dp.id}" for sw in topology_switches]
        link_names = [
            (f"s{link.src.dpid}", f"s{link.dst.dpid}")
            for link in topology_links
        ]
        self.component_energy_model.sync_topology(switch_names, link_names)
        energy_result = self.component_energy_model.record_cycle(
            cycle=cycle["cycle"],
            duration_seconds=POLL_INTERVAL_SECONDS,
            phase=cycle["phase"],
            session_id=self.session_id,
        )
        cycle["energy_result"] = energy_result

        self.logger.info(
            "COMPONENT-STATE ENERGY | phase=%s | switches=%d active/%d sleeping | "
            "links=%d active/%d sleeping | modeled_power=%.2f W | "
            "interval_energy=%.6f Wh | cumulative_energy=%.6f Wh",
            cycle["phase"],
            energy_result["switches_active"],
            energy_result["switches_sleeping"],
            energy_result["links_active"],
            energy_result["links_sleeping"],
            energy_result["total_power_w"],
            energy_result["interval_energy_wh"],
            energy_result["cumulative_energy_wh"],
        )
        self.logger.info(
            "Traffic-active ports remain a separate metric: %d",
            network_active_ports,
        )

'''
    code = replace_regex_once(
        code,
        r"        result = record_state\(.*?(?=        # ={8,}\n        # TRAFFIC UTILISATION GRAPH)",
        energy_section,
        "legacy active-port energy calculation block",
    )

    # The former energy_model graphs encoded active-port count as power. Remove
    # that graph call to avoid continuing to produce scientifically misleading charts.
    code = replace_regex_once(
        code,
        r"        # ={8,}\n        # ENERGY GRAPHS\n.*?(?=        # ={8,}\n        # CLEAR CURRENT CYCLE)",
        "        # Component energy is recorded in data/component_energy.csv.\n\n",
        "legacy energy graphs block",
    )

    snapshot_method = r'''    def _publish_cycle_snapshot(self, cycle):
        """Atomically publish the completed 5-second cycle for the Mininet QoS worker."""
        topology_switches = get_switch(self, None)
        topology_links = get_link(self, None)
        link_metrics = []
        for link_name, rates in sorted(self.current_link_rates.items()):
            if not rates:
                continue
            src, dst = link_name.split("-", 1)
            capacity_mbps = get_link_capacity(src, dst)
            if capacity_mbps is None:
                continue
            rate_mbps = max(rates)
            link_metrics.append({
                "link": link_name,
                "traffic_mbps": rate_mbps,
                "capacity_mbps": capacity_mbps,
                "utilization_percent": (rate_mbps / capacity_mbps) * 100.0,
            })

        snapshot = {
            "session_id": self.session_id,
            "cycle": int(cycle["cycle"]),
            "phase": cycle["phase"],
            "timestamp_epoch": time.time(),
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "interval_seconds": POLL_INTERVAL_SECONDS,
            "switches": sorted({f"s{sw.dp.id}" for sw in topology_switches}),
            "links": sorted({
                self._link_name(self._canonical_link(link.src.dpid, link.dst.dpid))
                for link in topology_links
            }),
            "network_active_ports": sum(cycle["active_ports"].values()),
            "link_metrics": link_metrics,
            "energy": cycle.get("energy_result", {}),
        }
        temporary_path = f"{CYCLE_SNAPSHOT_PATH}.{os.getpid()}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle)
        os.replace(temporary_path, CYCLE_SNAPSHOT_PATH)
        self.logger.info(
            "Published cycle snapshot: cycle=%d phase=%s path=%s",
            cycle["cycle"], cycle["phase"], CYCLE_SNAPSHOT_PATH,
        )

'''
    code = replace_once(
        code,
        "    def _finalize_cycle(self):\n",
        snapshot_method + "    def _finalize_cycle(self):\n",
        "snapshot publisher method insertion",
    )
    # Trigger active probes at the start of the QoS slot, rather than after the
    # OpenFlow statistics collection for that slot has already ended.
    code = replace_once(
        code,
        "            # Reset link-rate collection for this cycle.\n",
        "            if self.current_cycle[\"phase\"] == \"qos\":\n"
        "                self._publish_qos_request(self.current_cycle)\n\n"
        "            # Reset link-rate collection for this cycle.\n",
        "QoS request at phase start",
    )

    qos_request_method = """    def _publish_qos_request(self, cycle):
        \"\"\"Signal Mininet to begin active probes during this QoS phase.\"\"\"
        request = {
            \"session_id\": self.session_id,
            \"cycle\": int(cycle[\"cycle\"]),
            \"phase\": cycle[\"phase\"],
            \"timestamp_epoch\": time.time(),
            \"timestamp_utc\": time.strftime(\"%Y-%m-%dT%H:%M:%SZ\", time.gmtime()),
            \"interval_seconds\": POLL_INTERVAL_SECONDS,
        }
        temporary_path = f\"{QOS_REQUEST_PATH}.{os.getpid()}.tmp\"
        with open(temporary_path, \"w\", encoding=\"utf-8\") as handle:
            json.dump(request, handle)
        os.replace(temporary_path, QOS_REQUEST_PATH)
        self.logger.info(
            \"Published QoS phase request: cycle=%d path=%s\",
            cycle[\"cycle\"], QOS_REQUEST_PATH,
        )

"""
    code = replace_once(
        code,
        "    def _publish_cycle_snapshot(self, cycle):\n",
        qos_request_method + "    def _publish_cycle_snapshot(self, cycle):\n",
        "QoS request publisher method insertion",
    )

    # The snapshot is inserted at the end of _finalize_cycle, after graph generation.
    finalize_marker = "    def _finalize_cycle(self):\n"
    if finalize_marker not in code:
        raise RuntimeError("Could not locate _finalize_cycle for snapshot placement; no changes were written.")
    prefix, finalize_body = code.split(finalize_marker, 1)
    finalize_body = replace_once(
        finalize_body,
        "        self.current_cycle = None",
        "        self._publish_cycle_snapshot(cycle)\n\n        self.current_cycle = None",
        "snapshot publication at cycle end",
    )
    code = prefix + finalize_marker + finalize_body
    code = replace_regex_once(
        code,
        r"(?m)^\s*# Wait before beginning the next cycle\.\n.*?hub\.sleep\(\n\s*POLL_INTERVAL_SECONDS\n\s*\)",
        "            # Keep cycle start-to-start cadence at five seconds.\n"
        "            remaining = POLL_INTERVAL_SECONDS - (time.monotonic() - cycle_loop_started)\n"
        "            if remaining > 0:\n"
        "                hub.sleep(remaining)",
        "fixed 5-second cycle cadence",
    )

    # Validate likely indentation-sensitive essentials before writing.
    for required in (
        "from algorithm.component_energy_model import ComponentEnergyModel",
        "self._publish_cycle_snapshot(cycle)",
        '"phase": "energy" if self.cycle_number % 2 == 1 else "qos"',
        "record_active_port_metrics(",
        "POLL_INTERVAL_SECONDS = 5",
        "QOS_REQUEST_PATH = \"/tmp/sdn_energy_qos_request.json\"",
        "self._publish_qos_request(self.current_cycle)",
    ):
        if required not in code:
            raise RuntimeError(f"Post-patch validation failed: {required!r} missing; no changes were written.")

    shutil.copy2(MONITOR, BACKUP)
    MONITOR.write_text(code, encoding="utf-8")
    print(f"Updated {MONITOR}")
    print(f"Backup: {BACKUP}")
    print("Next: run python -m py_compile controller/monitor.py algorithm/component_energy_model.py algorithm/active_port_metrics.py algorithm/qos_monitor.py")
    print("Inspect the diff before launching the controller.")


if __name__ == "__main__":
    main()
