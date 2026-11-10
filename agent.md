
# Project: SDN Data Center Energy Optimization

## Objective
Implement hierarchical energy optimization in an SDN
data center network while preserving QoS, security,
connectivity, and resilience.

## Environment
- Ubuntu VM
- Mininet
- Open vSwitch
- OS-Ken 4.2.2
- Python virtual environment
- Project directory: ~/sdn-energy-project

## Existing architecture
- launch.py starts the controller
- controller/monitor.py collects topology and port stats
- algorithm/component_energy_model.py models energy
- algorithm/active_port_metrics.py tracks active ports
- algorithm/qos_monitor.py measures QoS
- algorithm/traffic_demo.py generates traffic

## Topology
- 6 switches: s1-s6
- 8 hosts: h1-h8
- s1 and s2 are core switches
- s3-s6 are edge switches
- edge switches connect to both core switches
- 9 intended inter-switch links
- host IPs: 10.0.0.1 through 10.0.0.8

## Energy model
- Switch active: 300 W
- Switch sleep: 20 W
- Inter-switch link active: 50 W
- Inter-switch link sleep: 1 W
- Values are simulation coefficients, not measured hardware power.

Energy = component power state × elapsed duration.
Use actual elapsed time, not an assumed fixed interval.

## Optimization strategy
1. Monitor traffic and topology.
2. Identify eligible low-utilization links.
3. Consolidate traffic where feasible.
4. Preserve QoS thresholds.
5. Preserve security policy restrictions.
6. Preserve connectivity and alternate paths.
7. Attempt link sleeping first.
8. Attempt switch sleeping only if safe.
9. Wake components on increased demand or failure.

## Critical requirements
- Inspect existing code before modifying.
- Preserve existing working functionality.
- Do not create a second controller.py.
- Do not invent APIs or topology links.
- Never claim physical energy savings from Mininet.
- Do not claim optimization is implemented unless tested.
- Separate monitoring, energy model, QoS, and algorithm.
- Explain every major modification.
- Run syntax checks and tests.
- Make incremental changes, not a massive rewrite.
