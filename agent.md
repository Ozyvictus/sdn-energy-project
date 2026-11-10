# Project: SDN Data Center Energy Optimization

## Objective
Implement hierarchical energy optimization in an SDN data center network while preserving QoS, security, connectivity, and resilience.

## Environment
- Ubuntu VM
- Mininet
- Open vSwitch
- OS-Ken 4.2.2
- Python virtual environment
- Project directory: ~/sdn-energy-project

## Current topology
The feature/fattree-k4 branch uses a canonical k=4 Fat-Tree:
- 4 core switches
- 8 aggregation switches
- 8 edge switches
- 20 total switches
- 16 hosts
- 32 inter-switch links
- 16 host-facing links
- 48 total links
- 4 pods; each pod has 2 aggregation and 2 edge switches
- Each edge switch serves 2 hosts
- Each aggregation switch connects to 2 core switches, one from each core group

Deterministic names:
- Core: s1-s4
- Aggregation: s5-s12
- Edge: s13-s20
- Hosts: h1-h16
- Host IPs: 10.0.0.1/8 through 10.0.0.16/8

Topology source of truth:
- topology/fattree_spec.py contains the pure-Python, Mininet-independent topology specification.
- topology/mininet_topo.py translates the specification into Mininet.
- config.py derives link capacities, switch names, host names, and link classes from the same specification.
- Use build_fat_tree_spec(k=4) for structural metadata.
- Do not recreate a second hardcoded list of topology links.

The original 6-switch, 8-host topology remains on the ccnsdemo branch. Do not silently copy its old topology assumptions into feature/fattree-k4.

## Existing architecture
- launch.py starts the controller and OS-Ken topology discovery.
- controller/monitor.py collects topology and OpenFlow port statistics. Its snapshot distinguishes the expected physical Fat-Tree link inventory from links currently visible through OS-Ken/LLDP; do not treat LLDP discovery as proof that all physical links are mapped.
- controller/l2_forwarding.py is currently a basic L2 learning switch.
- algorithm/component_energy_model.py models component operational-state energy.
- algorithm/active_port_metrics.py tracks traffic-active ports separately.
- algorithm/qos_monitor.py measures rotating 16-pair batches (one separate warm-up ping plus three measured ICMP probes per pair) across the full host-pair matrix, plus opportunistic rotating TCP throughput.
- algorithm/traffic_demo.py generates four concurrent UDP flows across all four pods.
- algorithm/measurement_utils.py calculates actual start-to-start sample intervals for energy integration.
- docs/measurement-pipeline.md documents the baseline measurement procedure and output files.
- algorithm/optimizer.py currently models link-sleep decisions but is not integrated with live OpenFlow reconfiguration.

## Link capacities
- Host-facing links: 100 Mbps
- Inter-switch links: 1000 Mbps
- Capacities are derived from topology.fattree_spec; do not hardcode a second topology map.

## Energy model
- Switch active: 300 W
- Switch sleep: 20 W
- Inter-switch link active: 50 W
- Inter-switch link sleep: 1 W
- These are literature-derived simulation coefficients, not physical Mininet/OVS power measurements.
- Host-facing links are excluded from the inter-switch link energy model.
- Energy must use actual elapsed time and component-state durations, not traffic-active port counts or assumed fixed cycle durations. The first sample uses the nominal interval; subsequent intervals use monotonic cycle start-to-start time.
- QoS pair coverage must be explicit: k=4 has 16 hosts and 120 unique unordered pairs; 16 pairs are measured per QoS cycle and a full sweep spans eight QoS cycles.
- The QoS jitter_ms field is Linux ping RTT mdev (round-trip-time standard deviation), not RFC 3550 inter-arrival jitter.
- QoS monitoring is started explicitly from the Mininet CLI; it is not started automatically by the topology launcher.

## Optimization strategy
1. Monitor traffic and topology.
2. Identify eligible low-utilization inter-switch links.
3. Consolidate traffic where feasible.
4. Evaluate actual QoS measurements separately from link utilization.
5. Preserve configured security policies; do not report security as passed when no policies are configured or evaluated.
6. Preserve required host connectivity and the explicitly configured resilience level.
7. Attempt link sleeping first.
8. Attempt switch sleeping only after safe host migration/reconnection and failure handling are implemented.
9. Wake components on increased demand or failure.

## Critical requirements
- Inspect existing code before modifying.
- Preserve existing working functionality.
- Do not create a second controller entry point.
- Do not invent APIs or topology links.
- Do not assume STP leaves all physical paths available for optimization.
- Do not disable STP while retaining flooding-based forwarding.
- Never claim physical energy savings from Mininet.
- Do not claim optimization is implemented unless the relevant network changes are actually executed and tested.
- Keep monitoring, energy model, QoS, security policy, and optimization responsibilities separated.
- Validate the canonical topology and measurement pipeline with python -m unittest discover -s tests -p "test_*.py" -v.
- Run Mininet-specific tests in the Ubuntu VM; local structural tests do not prove Mininet startup, OpenFlow discovery, or host connectivity.
- Explain every major modification.
- Run syntax checks and tests.
- Make incremental changes, not a massive rewrite.
