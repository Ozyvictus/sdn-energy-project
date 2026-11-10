# Monitoring and measurement pipeline

This document describes the baseline measurement path for the k=4 Fat-Tree. It does not enable link sleeping or other live optimizer actuation.

## What runs where

- `launch.py` starts the OS-Ken controller, L2 forwarding app, topology discovery, and `controller.monitor.TopologyMonitor`.
- `controller/monitor.py` polls OpenFlow port counters, calculates inter-switch link rates/utilization, tracks traffic-active ports, and records component-state energy estimates.
- `topology/port_map.py` validates and writes the complete physical switch-port inventory built from Mininet's actual interface port numbers. This inventory includes STP-blocked redundant links.
- `algorithm/qos_monitor.py` runs from the Mininet CLI as a separate worker. It probes rotating host-pair batches during QoS cycles.
- `algorithm/traffic_demo.py` generates four UDP flows in a ring across the four Fat-Tree pods and records the offered-load experiment.
- `algorithm/component_energy_model.py` estimates power from configured component states. It does not measure physical Mininet or OVS power.

The controller alternates five-second cycles: energy, QoS, energy, QoS, and so on. The energy model uses a nominal five-second interval for the first sample, then the actual monotonic start-to-start interval between successive monitoring cycles. If a cycle takes longer than expected, its elapsed interval is not silently rounded down to five seconds.

## Start a measurement session

Run the commands from the repository root. Activate the same Python environment in both terminals.

**Terminal 1 — controller**

```bash
sudo -E "$(command -v python)" -u launch.py
```

**Terminal 2 — Mininet**

```bash
sudo -E "$(command -v python)" -u -m topology.mininet_topo
```

Wait for the topology launcher to finish its configured STP convergence delay and open the Mininet CLI. Keep STP enabled while the project uses the current flooding-based L2 forwarding app.

**Mininet CLI — start the QoS worker**

```text
mininet> px from algorithm.qos_monitor import QoSMonitor
mininet> px qos_monitor = QoSMonitor(net).start()
```

The worker watches `/tmp/sdn_energy_qos_request.json`; it does not start automatically with Mininet. Stop it before exiting the CLI:

```text
mininet> px qos_monitor.stop()
```

**Mininet CLI — run the offered-load experiment**

Run this while the QoS worker is active:

```text
mininet> px from algorithm.traffic_demo import run_demo
mininet> px traffic_run_dir = run_demo(net)
```

The default demo records a quiet baseline, low/medium/high offered UDP loads, and a cooldown. Each profile starts four concurrent inter-pod flows spanning all four pods and all eight edge switches. Target rates are offered rates, not guaranteed delivered throughput.

## QoS sampling design

A 16-host topology has 120 unique unordered host pairs. The QoS worker measures 16 pairs per QoS cycle. For each pair it sends one unrecorded warm-up ping, then three measured ICMP echo requests. It rotates through eight deterministic batches; therefore a complete pair matrix is covered across eight QoS phases (approximately 80 seconds at the nominal alternating-cycle cadence). The CSV records warm-up reachability/latency separately from measured RTT, the total pair count, pairs scheduled in the current batch, valid measurements, batch index, batch duration, and whether the probe batch fit within its four-second target slot.

The `latency_ms` and `jitter_ms` fields are calculated from the three measured probes after the separate warm-up ping. The `jitter_ms` field is Linux ping's RTT `mdev` (standard deviation of round-trip samples). It is recorded as an RTT-variation indicator, **not** as an RFC 3550 inter-arrival jitter measurement. Longer repeated runs are still recommended for final statistical reporting.

A rotating one-second iperf3 TCP sample is attempted only when the ICMP batch finishes with enough time left in the QoS slot. It is opportunistic and may be skipped when the probe batch consumes the budget. The separate traffic demo is the repeatable offered-load throughput experiment.

## Output files

| File | Contents |
|---|---|
| `data/component_energy.csv` | Modeled switch/link power and energy, including the actual sample interval and component state inventory |
| `data/qos_pair_measurements.csv` | Per-pair RTT, RTT variation, packet loss, reachability, and optional throughput sample |
| `data/qos_cycle_summary.csv` | Per-batch coverage, valid/reachable pair counts, summary QoS values, and timing |
| `data/traffic_demo/run_*/traffic_demo_phases.csv` | Wall-clock baseline, load-profile, and cooldown windows |
| `data/traffic_demo/run_*/traffic_demo_summary.csv` | Per-flow UDP sender/receiver throughput, sender/receiver target-attainment percentages, loss, and jitter |
| `graphs/monitoring/traffic_utilisation.png` | Inter-switch link utilization history |
| `/tmp/sdn_energy_cycle.json` | Latest controller cycle snapshot, including topology counts and port-stat reply coverage |
| `/tmp/sdn_energy_qos_request.json` | Current QoS-phase request consumed by the Mininet-side worker |

Mininet writes `/tmp/sdn_energy_port_map.json` from the actual switch interfaces and port numbers. The controller uses this physical inventory for port-to-link rate attribution, including links blocked by STP. The controller snapshot reports `topology_switch_count`, `topology_inter_switch_link_count` (physical links in the Mininet inventory), `expected_inter_switch_link_count`, `physical_port_map_source`, `physical_link_inventory_complete`, `lldp_discovered_inter_switch_link_count`, `lldp_missing_inter_switch_links`, `link_metric_count`, `switches_expected_replies`, `switches_received_replies`, `switch_reply_coverage_percent`, `stats_complete`, and `energy_interval_seconds`.

LLDP is retained as a separate diagnostic view. With STP enabled, LLDP may only see forwarding links and therefore fewer than all 32 physical links. Do not treat the LLDP count alone as the physical topology inventory. When a previous QoS CSV uses an older header, the monitor preserves that file and automatically writes the new schema to a versioned sibling such as `qos_pair_measurements_v2.csv` or a later schema version when fields change. It reuses that versioned file on subsequent runs when its header matches, rather than mixing rows with different schemas. For a stable k=4 run, the expected inventory is 20 switches and 32 inter-switch links. The LLDP-discovered link count can be lower while STP is active or discovery is incomplete; use the explicit missing-link list rather than treating the discovered count as the physical inventory. A partial port-stat response is explicitly marked; do not treat a cycle with incomplete switch replies as a complete measurement.

## Interpreting the energy data

The current literature-derived defaults are 300/20 W for active/sleep switches and 50/1 W for active/sleep inter-switch links. They are simulation coefficients, not readings from physical equipment. The Mininet/OVS experiment validates traffic, state tracking, and measurement behavior; it cannot establish physical wattage or measured facility energy savings.

The optimizer is not yet actuating link sleep/wake operations. Until such actuation is implemented and validated, component states remain active and the modeled energy series is a baseline model output.

## Recommended baseline procedure

1. Start the controller, Mininet, and QoS worker; allow topology discovery and at least one full pair sweep to complete.
2. Run the traffic demo and confirm all four UDP flows report `status=ok`. Also inspect sender/receiver target-attainment percentages; a completed iperf3 process does not guarantee that the requested rate was achieved.
3. Check the controller snapshot for 20 switches, 32 physical inter-switch links from `mininet_net_links`, the separate LLDP-visible link count, and complete OpenFlow reply coverage.
4. Check that each QoS summary reports 120 expected pairs, 16 (or the final remainder) scheduled pairs, and valid measurements for reachable pairs. Confirm the separate warm-up fields are populated.
5. Repeat the experiment from a fresh controller session at least three times before calculating mean and variability for the baseline.
6. Retain each run's raw CSV files and manifest. Join traffic-profile windows to controller/QoS samples by timestamps and the corresponding controller session ID where available.
