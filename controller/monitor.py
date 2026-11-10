"""
controller/monitor.py

Milestone 2:
    Topology discovery
    + OpenFlow port-statistics monitoring
    + traffic-rate calculation
    + network-wide active-port detection
    + cycle-based energy-model integration

Architecture:

    Mininet / OVS
          |
      OpenFlow 1.3
          |
      OS-Ken Controller
          |
      monitor.py
          |
      Port Statistics
          |
      10-second cycle
          |
      Network-wide active ports
          |
      energy_model.py
          |
      Modeled Power / Energy
          |
      graphs/

Energy model:

    Modeled power is calculated from configured switch/link operational states,
    independently of traffic-active-port statistics.

IMPORTANT:

    Results are literature-parameterized estimates, not physical watt-meter readings.

At this milestone:

    active port = a port that transferred traffic during the
                  latest completed monitoring cycle.

Later, when the optimization algorithm is implemented, we will
separate:

    1. traffic activity
    2. operational/optimizer-active state

This is important because an idle redundant link may still need
to remain active for resilience.
"""

import time
import json
import os
import uuid

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import (
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls
)
from os_ken.lib import hub

from os_ken.topology import event as topo_event
from os_ken.topology.api import get_switch, get_link

from algorithm.component_energy_model import ComponentEnergyModel
from algorithm.active_port_metrics import record_active_port_metrics

from algorithm.traffic_utilisation import (
    record_link_utilisation,
    advance_time,
    generate_utilisation_graph
)

from config import get_link_capacity


# ================================================================
# CONFIGURATION
# ================================================================

# How often a complete monitoring cycle starts.
POLL_INTERVAL_SECONDS = 5

# Maximum amount of time to wait for all switch replies.
#
# Normally all OVS switches should respond very quickly.
# This prevents one disconnected/unresponsive switch from
# blocking the energy calculation forever.
CYCLE_TIMEOUT_SECONDS = 2

# Number of bytes that must have been transferred for a port to
# be considered traffic-active.
#
# 0 means any increase in RX/TX bytes makes the port active.
TRAFFIC_THRESHOLD_BYTES = 0

CYCLE_SNAPSHOT_PATH = "/tmp/sdn_energy_cycle.json"
QOS_REQUEST_PATH = "/tmp/sdn_energy_qos_request.json"


class TopologyMonitor(app_manager.OSKenApp):

    def __init__(self, *args, **kwargs):
        super(TopologyMonitor, self).__init__(*args, **kwargs)

        # ==========================================================
        # CONNECTED SWITCHES
        # ==========================================================

        # dpid -> datapath object
        #
        # These are the switches currently connected to the
        # OS-Ken controller.
        self.datapaths = {}

        # ==========================================================
        # PREVIOUS PORT STATISTICS
        # ==========================================================

        # Key:
        #     (dpid, port_no)
        #
        # Value:
        #     {
        #         "rx_bytes": ...,
        #         "tx_bytes": ...,
        #         "rx_packets": ...,
        #         "tx_packets": ...,
        #         "timestamp": ...
        #     }
        #
        # Used to calculate traffic rate between two polling cycles.
        self.previous_port_stats = {}

        # ==========================================================
        # CURRENT MONITORING CYCLE
        # ==========================================================

        # Each cycle collects one reply from every switch.
        #
        # Example:
        #
        # {
        #     1: {
        #         "active_ports": 2,
        #         "ports_processed": 4
        #     },
        #     2: {
        #         "active_ports": 3,
        #         "ports_processed": 4
        #     }
        # }
        #
        self.current_cycle = None

        # Unique number for each monitoring cycle.
        self.cycle_number = 0
        self.current_link_rates = {}
        self.session_id = uuid.uuid4().hex
        self.component_energy_model = ComponentEnergyModel()
        self.port_to_link = {}

        # ==========================================================
        # START MONITORING THREAD
        # ==========================================================

        self.monitor_thread = hub.spawn(
            self._monitor_loop
        )

        self.logger.info(
            "TopologyMonitor started. "
            "Polling interval=%s seconds",
            POLL_INTERVAL_SECONDS
        )

    # ================================================================
    # SWITCH CONNECTION STATE
    # ================================================================

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER]
    )
    def _state_change_handler(self, ev):

        datapath = ev.datapath

        # ----------------------------------------------------------
        # SWITCH CONNECTED
        # ----------------------------------------------------------

        if ev.state == MAIN_DISPATCHER:

            if datapath.id not in self.datapaths:

                self.logger.info(
                    "Monitor: now tracking dpid=%s "
                    "for statistics polling",
                    datapath.id
                )

            self.datapaths[datapath.id] = datapath

        # ----------------------------------------------------------
        # SWITCH DISCONNECTED
        # ----------------------------------------------------------

        elif ev.state == DEAD_DISPATCHER:

            if datapath.id in self.datapaths:

                self.logger.info(
                    "Monitor: dpid=%s disconnected, "
                    "stopped polling",
                    datapath.id
                )

                del self.datapaths[datapath.id]

    # ================================================================
    # TOPOLOGY DISCOVERY
    # ================================================================

    def _refresh_topology_mapping(self):
        """
        Refresh the port-to-logical-link mapping
        from OS-Ken's topology database.
        """

        links = get_link(self, None)

        if not links:
            self.logger.warning(
                "TOPOLOGY REFRESH: no links discovered yet"
            )
            return

        self.port_to_link.clear()

        for link in links:

            src = link.src
            dst = link.dst

            logical_link = self._canonical_link(
                src.dpid,
                dst.dpid
            )

            self.port_to_link[
                (src.dpid, src.port_no)
            ] = logical_link

            self.port_to_link[
                (dst.dpid, dst.port_no)
            ] = logical_link

        self.logger.info(
            "TOPOLOGY REFRESH: %d links, %d port mappings",
            len(links),
            len(self.port_to_link)
        )

    @set_ev_cls(topo_event.EventSwitchEnter)
    def _switch_enter_handler(self, ev):

        self.logger.info(
            "Topology: switch entered dpid=%s",
            ev.switch.dp.id
        )

        self._log_topology_snapshot()

    @set_ev_cls(topo_event.EventLinkAdd)
    def _link_add_handler(self, ev):

        src = ev.link.src
        dst = ev.link.dst

        logical_link = self._canonical_link(
            src.dpid,
            dst.dpid
        )

        if logical_link is None:
            self.logger.warning(
                "Could not create logical link for "
                "dpid=%s <-> dpid=%s",
                src.dpid,
                dst.dpid
            )
            return

        # Store both endpoints of the physical link.
        self.port_to_link[
            (src.dpid, src.port_no)
        ] = logical_link

        self.port_to_link[
            (dst.dpid, dst.port_no)
        ] = logical_link

        self.logger.info(
            "LINK DISCOVERED | %s | "
            "%s:%s <-> %s:%s",
            self._link_name(logical_link),
            src.dpid,
            src.port_no,
            dst.dpid,
            dst.port_no
        )

        self._log_topology_snapshot()

    def _log_topology_snapshot(self):

        switches = get_switch(self, None)
        links = get_link(self, None)

        self.logger.info(
            "Topology snapshot: %d switch(es), %d link(s) known",
            len(switches),
            len(links)
        )

        self.logger.info(
            "Port-to-link mappings: %d",
            len(self.port_to_link)
        )

        for (dpid, port_no), logical_link in sorted(
            self.port_to_link.items()
        ):
            self.logger.info(
                "PORT MAP | dpid=%s port=%s -> %s",
                dpid,
                port_no,
                self._link_name(logical_link)
            )

    def _get_logical_link(self, dpid, port_no):
        """
        Return the logical inter-switch link associated
        with an OpenFlow port.
        """

        return self.port_to_link.get(
            (dpid, port_no)
        )
        


    def _canonical_link(self, dpid_a, dpid_b):
        """
        Represent a physical link in one consistent direction.

        Example:

            s3-s1
            s1-s3

        both become:

            s1-s3
        """

        if dpid_a < dpid_b:
            return (
                f"s{dpid_a}",
                f"s{dpid_b}"
            )

        return (
            f"s{dpid_b}",
            f"s{dpid_a}"
        )


    def _link_name(self, link):
        """
        Convert:

            ("s1", "s3")

        into:

            "s1-s3"
        """

        return f"{link[0]}-{link[1]}"

    # ================================================================
    # MONITORING LOOP
    # ================================================================

    def _monitor_loop(self):

        while True:

            # ------------------------------------------------------
            # Take a snapshot of currently connected switches.
            #
            # This prevents the dictionary changing while we are
            # creating the monitoring cycle.
            # ------------------------------------------------------

            datapaths = list(
                self.datapaths.values()
            )

            # ------------------------------------------------------
            # If no switches are connected, wait and try again.
            # ------------------------------------------------------

            if not datapaths:

                self.logger.info(
                    "Energy monitor: no connected switches"
                )

                hub.sleep(
                    POLL_INTERVAL_SECONDS
                )

                continue

            # ------------------------------------------------------
            # Start a new monitoring cycle.
            # ------------------------------------------------------

            cycle_loop_started = time.monotonic()

            self.cycle_number += 1

            expected_dpids = {
                dp.id for dp in datapaths
            }

            self.current_cycle = {
                "cycle": self.cycle_number,
                "phase": "energy" if self.cycle_number % 2 == 1 else "qos",
                "start_time": time.time(),
                "expected_dpids": expected_dpids,
                "received_dpids": set(),
                "active_ports": {},
                "traffic_rates": {},
                "timed_out": False
                }

            if self.current_cycle["phase"] == "qos":
                self._publish_qos_request(self.current_cycle)

            # Reset link-rate collection for this cycle.
            self._refresh_topology_mapping()
            self.current_link_rates = {}

            self.logger.info(
                ""
            )

            self.logger.info(
                "=================================================="
            )

            self.logger.info(
                "STARTING MONITORING CYCLE %d",
                self.cycle_number
            )

            self.logger.info(
                "Expected switch replies: %d",
                len(expected_dpids)
            )

            self.logger.info(
                "=================================================="
            )

            # ------------------------------------------------------
            # Request statistics from every connected switch.
            # ------------------------------------------------------

            for dp in datapaths:

                self._request_port_stats(dp)

            # ------------------------------------------------------
            # Wait for replies.
            #
            # Normally replies arrive quickly.
            # We wait until either:
            #
            #   1. every switch has replied
            #a
            # OR
            #
            #   2. CYCLE_TIMEOUT_SECONDS has passed.
            # ------------------------------------------------------

            cycle_start = time.time()

            while True:

                if self.current_cycle is None:
                    break

                received = (
                    self.current_cycle["received_dpids"]
                )

                expected = (
                    self.current_cycle["expected_dpids"]
                )

                # All switches replied.
                if received >= expected:

                    break

                # Timeout.
                if (
                    time.time() - cycle_start
                    >= CYCLE_TIMEOUT_SECONDS
                ):

                    self.current_cycle["timed_out"] = True

                    self.logger.warning(
                        "Monitoring cycle %d timed out. "
                        "Received %d/%d switch replies.",
                        self.cycle_number,
                        len(received),
                        len(expected)
                    )

                    break

                # Give other OS-Ken green threads time to process
                # OpenFlow replies.
                hub.sleep(0.05)

            # ------------------------------------------------------
            # Finalize this cycle.
            # ------------------------------------------------------

            if self.current_cycle is not None:

                self._finalize_cycle()

            # ------------------------------------------------------
            # Keep cycle start-to-start cadence at five seconds.
            remaining = POLL_INTERVAL_SECONDS - (time.monotonic() - cycle_loop_started)
            if remaining > 0:
                hub.sleep(remaining)

    # ================================================================
    # REQUEST PORT STATISTICS
    # ================================================================

    def _request_port_stats(self, datapath):

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        req = parser.OFPPortStatsRequest(
            datapath,
            0,
            ofproto.OFPP_ANY
        )

        datapath.send_msg(req)

    # ================================================================
    # PORT STATISTICS RESPONSE
    # ================================================================

    @set_ev_cls(
        ofp_event.EventOFPPortStatsReply,
        MAIN_DISPATCHER
    )
    def _port_stats_reply_handler(self, ev):

        # ----------------------------------------------------------
        # Ignore unexpected replies when there is no active cycle.
        # ----------------------------------------------------------

        if self.current_cycle is None:

            self.logger.warning(
                "Received port statistics reply from dpid=%s "
                "outside an active monitoring cycle",
                ev.msg.datapath.id
            )

            return

        dpid = ev.msg.datapath.id

        # ----------------------------------------------------------
        # Ignore duplicate replies from the same switch during
        # the current cycle.
        # ----------------------------------------------------------

        if dpid in self.current_cycle["received_dpids"]:

            self.logger.warning(
                "Duplicate port statistics reply from dpid=%s "
                "in cycle %d",
                dpid,
                self.current_cycle["cycle"]
            )

            return

        current_time = time.time()

        active_ports_for_switch = 0

        traffic_rates_for_switch = {}

        self.logger.info(
            ""
        )

        self.logger.info(
            "--------------------------------------------------"
        )

        self.logger.info(
            "PORT STATISTICS: dpid=%s | cycle=%d",
            dpid,
            self.current_cycle["cycle"]
        )

        self.logger.info(
            "--------------------------------------------------"
        )

        # ==========================================================
        # PROCESS EACH PORT
        # ==========================================================

        for stat in ev.msg.body:

            # ------------------------------------------------------
            # Ignore OpenFlow reserved/local ports.
            # ------------------------------------------------------

            if stat.port_no > 0xffffff00:

                continue

            port_no = stat.port_no

            # ------------------------------------------------------
            # Current counters
            # ------------------------------------------------------

            current_rx_bytes = stat.rx_bytes
            current_tx_bytes = stat.tx_bytes

            current_rx_packets = stat.rx_packets
            current_tx_packets = stat.tx_packets

            # ------------------------------------------------------
            # Port identifier
            # ------------------------------------------------------

            key = (
                dpid,
                port_no
            )

            # ------------------------------------------------------
            # FIRST OBSERVATION
            #
            # We cannot calculate traffic rate until we have two
            # measurements.
            # ------------------------------------------------------

            if key not in self.previous_port_stats:

                self.previous_port_stats[key] = {
                    "rx_bytes": current_rx_bytes,
                    "tx_bytes": current_tx_bytes,
                    "rx_packets": current_rx_packets,
                    "tx_packets": current_tx_packets,
                    "timestamp": current_time
                }

                self.logger.info(
                    "Port %s: baseline established "
                    "rx=%s tx=%s",
                    port_no,
                    current_rx_bytes,
                    current_tx_bytes
                )

                continue

            # ------------------------------------------------------
            # Previous counters
            # ------------------------------------------------------

            previous = (
                self.previous_port_stats[key]
            )

            previous_rx_bytes = (
                previous["rx_bytes"]
            )

            previous_tx_bytes = (
                previous["tx_bytes"]
            )

            previous_rx_packets = (
                previous["rx_packets"]
            )

            previous_tx_packets = (
                previous["tx_packets"]
            )

            previous_time = (
                previous["timestamp"]
            )

            # ------------------------------------------------------
            # Time elapsed
            # ------------------------------------------------------

            elapsed = (
                current_time -
                previous_time
            )

            if elapsed <= 0:

                continue

            # ======================================================
            # COUNTER DIFFERENCES
            # ======================================================

            delta_rx_bytes = (
                current_rx_bytes -
                previous_rx_bytes
            )

            delta_tx_bytes = (
                current_tx_bytes -
                previous_tx_bytes
            )

            delta_rx_packets = (
                current_rx_packets -
                previous_rx_packets
            )

            delta_tx_packets = (
                current_tx_packets -
                previous_tx_packets
            )

            # ------------------------------------------------------
            # Protect against counter reset/wraparound.
            # ------------------------------------------------------

            if delta_rx_bytes < 0:
                delta_rx_bytes = 0

            if delta_tx_bytes < 0:
                delta_tx_bytes = 0

            if delta_rx_packets < 0:
                delta_rx_packets = 0

            if delta_tx_packets < 0:
                delta_tx_packets = 0

            # ======================================================
            # TRAFFIC CALCULATION
            # ======================================================

            delta_bytes = (
                delta_rx_bytes +
                delta_tx_bytes
            )

            delta_packets = (
                delta_rx_packets +
                delta_tx_packets
            )

            # ------------------------------------------------------
            # Mbps
            # ------------------------------------------------------

            traffic_rate_mbps = (
                delta_bytes
                * 8
                / elapsed
                / 1_000_000
            )

            # ==============================================================
            # LOGICAL LINK UTILISATION DATA
            # ==============================================================

            logical_link = self._get_logical_link(
                dpid,
                port_no
            )

            if logical_link is not None:

                link_name = self._link_name(
                    logical_link
                )

                if link_name not in self.current_link_rates:

                    self.current_link_rates[link_name] = []

                self.current_link_rates[link_name].append(
                    traffic_rate_mbps
                )

            # ------------------------------------------------------
            # Packet rate
            # ------------------------------------------------------

            packet_rate = (
                delta_packets /
                elapsed
            )

            # ======================================================
            # ACTIVE PORT DETECTION
            # ======================================================

            if (
                delta_bytes >
                TRAFFIC_THRESHOLD_BYTES
            ):

                active_ports_for_switch += 1

                port_state = "ACTIVE"

            else:

                port_state = "IDLE"

            # Store traffic rate for future optimizer use.
            traffic_rates_for_switch[port_no] = (
                traffic_rate_mbps
            )

            # ======================================================
            # LOG
            # ======================================================

            self.logger.info(
                "Port %s | state=%s | "
                "RX=%s bytes | TX=%s bytes | "
                "rate=%.2f Mbps | "
                "packet_rate=%.2f pkt/s",
                port_no,
                port_state,
                current_rx_bytes,
                current_tx_bytes,
                traffic_rate_mbps,
                packet_rate
            )

            # ======================================================
            # SAVE CURRENT COUNTERS
            # ======================================================

            self.previous_port_stats[key] = {
                "rx_bytes": current_rx_bytes,
                "tx_bytes": current_tx_bytes,
                "rx_packets": current_rx_packets,
                "tx_packets": current_tx_packets,
                "timestamp": current_time
            }

        # ==========================================================
        # STORE THIS SWITCH'S RESULT FOR THE CURRENT CYCLE
        # ==========================================================

        self.current_cycle["active_ports"][dpid] = (
            active_ports_for_switch
        )

        self.current_cycle["traffic_rates"][dpid] = (
            traffic_rates_for_switch
        )

        self.current_cycle["received_dpids"].add(
            dpid
        )

        self.logger.info(
            "dpid=%s cycle result: "
            "active_ports=%d",
            dpid,
            active_ports_for_switch
        )

    def _record_cycle_link_utilisation(self):
        """
        Convert collected OpenFlow port traffic rates into
        logical inter-switch link utilisation.

        Each physical link is represented by two OpenFlow
        endpoints. Therefore, multiple measurements may exist
        for the same logical link.

        We use the maximum observed endpoint rate to avoid
        double-counting the same physical link.
        """

        self.logger.info(
            "TRAFFIC UTILISATION: %d logical links collected",
            len(self.current_link_rates)
        )

        for link_name, rates in self.current_link_rates.items():

            self.logger.info(
                "TRAFFIC DATA: %s -> %s",
                link_name,
                rates
            )

            if not rates:
                continue

        for link_name, rates in self.current_link_rates.items():

            if not rates:
                continue

            # Both ends of a link should report approximately
            # the same traffic volume.
            #
            # Taking the maximum avoids counting:
            #
            #   S1 -> S3
            #   S3 -> S1
            #
            # as two separate physical links.
            traffic_mbps = max(rates)

            try:
                src, dst = link_name.split("-")

            except ValueError:

                self.logger.warning(
                    "Invalid logical link name: %s",
                    link_name
                )

                continue

            capacity_mbps = get_link_capacity(
                src,
                dst
            )

            if capacity_mbps is None:

                self.logger.warning(
                    "No capacity configured for %s",
                    link_name
                )

                continue

            utilisation = (
                traffic_mbps
                / capacity_mbps
                * 100.0
            )

            self.logger.info(
                "LINK UTILISATION | %s | "
                "traffic=%.2f Mbps | "
                "capacity=%d Mbps | "
                "utilisation=%.2f%%",
                link_name.upper(),
                traffic_mbps,
                capacity_mbps,
                utilisation
            )

            record_link_utilisation(
                link_name,
                traffic_mbps,
                capacity_mbps
            )

    # ================================================================
    # FINALIZE MONITORING CYCLE
    # ================================================================

    def _publish_qos_request(self, cycle):
        """Signal Mininet to begin active probes during this QoS phase."""
        request = {
            "session_id": self.session_id,
            "cycle": int(cycle["cycle"]),
            "phase": cycle["phase"],
            "timestamp_epoch": time.time(),
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "interval_seconds": POLL_INTERVAL_SECONDS,
        }
        temporary_path = f"{QOS_REQUEST_PATH}.{os.getpid()}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(request, handle)
        os.replace(temporary_path, QOS_REQUEST_PATH)
        self.logger.info(
            "Published QoS phase request: cycle=%d path=%s",
            cycle["cycle"], QOS_REQUEST_PATH,
        )

    def _publish_cycle_snapshot(self, cycle):
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

    def _finalize_cycle(self):

        cycle = self.current_cycle

        if cycle is None:

            return

        # ----------------------------------------------------------
        # Network-wide active ports
        # ----------------------------------------------------------

        network_active_ports = sum(
            cycle["active_ports"].values()
        )

        # ----------------------------------------------------------
        # Total switches that replied
        # ----------------------------------------------------------

        received_switches = len(
            cycle["received_dpids"]
        )

        expected_switches = len(
            cycle["expected_dpids"]
        )

        # ----------------------------------------------------------
        # Cycle duration
        # ----------------------------------------------------------

        cycle_duration = (
            time.time() -
            cycle["start_time"]
        )

        # ==========================================================
        # LOG CYCLE SUMMARY
        # ==========================================================

        self.logger.info(
            ""
        )

        self.logger.info(
            "=================================================="
        )

        self.logger.info(
            "MONITORING CYCLE %d COMPLETE",
            cycle["cycle"]
        )

        self.logger.info(
            "Switch replies: %d/%d",
            received_switches,
            expected_switches
        )

        self.logger.info(
            "Cycle duration: %.3f seconds",
            cycle_duration
        )

        self.logger.info(
            "Network active ports: %d",
            network_active_ports
        )

        self.logger.info(
            "=================================================="
        )

        # ==========================================================
        # ENERGY MODEL
        # ==========================================================

        #
        # Do not calculate energy once per switch.
        #
        # Calculate it exactly once for the complete network-wide
        # monitoring cycle.
        #

        # Record traffic-active port counts separately. These are traffic
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

        # ================================================================
        # TRAFFIC UTILISATION GRAPH
        # ================================================================

        self._record_cycle_link_utilisation()

        advance_time(
            POLL_INTERVAL_SECONDS
        )

        generate_utilisation_graph()

        self.logger.info(
            "Traffic utilisation graph updated in "
            "./graphs/monitoring/traffic_utilisation.png"
        )


        # Component energy is recorded in data/component_energy.csv.

        # ==========================================================
        # CLEAR CURRENT CYCLE
        # ==========================================================

        self._publish_cycle_snapshot(cycle)

        self.current_cycle = None