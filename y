[1mdiff --git a/controller/monitor.py b/controller/monitor.py[m
[1mindex 8fdbd82..fc67153 100644[m
[1m--- a/controller/monitor.py[m
[1m+++ b/controller/monitor.py[m
[36m@@ -32,11 +32,12 @@[m [mArchitecture:[m
 [m
 Energy model:[m
 [m
[31m-    Modeled Power = Active Ports × 0.2 W[m
[32m+[m[32m    Modeled power is calculated from configured switch/link operational states,[m
[32m+[m[32m    independently of traffic-active-port statistics.[m
 [m
 IMPORTANT:[m
[31m-    This is a SOFTWARE MODEL, not a measurement of physical[m
[31m-    electrical power.[m
[32m+[m
[32m+[m[32m    Results are literature-parameterized estimates, not physical watt-meter readings.[m
 [m
 At this milestone:[m
 [m
[36m@@ -54,6 +55,9 @@[m [mto remain active for resilience.[m
 """[m
 [m
 import time[m
[32m+[m[32mimport json[m
[32m+[m[32mimport os[m
[32m+[m[32mimport uuid[m
 [m
 from os_ken.base import app_manager[m
 from os_ken.controller import ofp_event[m
[36m@@ -67,10 +71,8 @@[m [mfrom os_ken.lib import hub[m
 from os_ken.topology import event as topo_event[m
 from os_ken.topology.api import get_switch, get_link[m
 [m
[31m-from algorithm.energy_model import ([m
[31m-    record_state,[m
[31m-    generate_all_graphs[m
[31m-)[m
[32m+[m[32mfrom algorithm.component_energy_model import ComponentEnergyModel[m
[32m+[m[32mfrom algorithm.active_port_metrics import record_active_port_metrics[m
 [m
 from algorithm.traffic_utilisation import ([m
     record_link_utilisation,[m
[36m@@ -86,14 +88,14 @@[m [mfrom config import get_link_capacity[m
 # ================================================================[m
 [m
 # How often a complete monitoring cycle starts.[m
[31m-POLL_INTERVAL_SECONDS = 10[m
[32m+[m[32mPOLL_INTERVAL_SECONDS = 5[m
 [m
 # Maximum amount of time to wait for all switch replies.[m
 #[m
 # Normally all OVS switches should respond very quickly.[m
 # This prevents one disconnected/unresponsive switch from[m
 # blocking the energy calculation forever.[m
[31m-CYCLE_TIMEOUT_SECONDS = 3[m
[32m+[m[32mCYCLE_TIMEOUT_SECONDS = 2[m
 [m
 # Number of bytes that must have been transferred for a port to[m
 # be considered traffic-active.[m
[36m@@ -101,6 +103,9 @@[m [mCYCLE_TIMEOUT_SECONDS = 3[m
 # 0 means any increase in RX/TX bytes makes the port active.[m
 TRAFFIC_THRESHOLD_BYTES = 0[m
 [m
[32m+[m[32mCYCLE_SNAPSHOT_PATH = "/tmp/sdn_energy_cycle.json"[m
[32m+[m[32mQOS_REQUEST_PATH = "/tmp/sdn_energy_qos_request.json"[m
[32m+[m
 [m
 class TopologyMonitor(app_manager.OSKenApp):[m
 [m
[36m@@ -160,6 +165,8 @@[m [mclass TopologyMonitor(app_manager.OSKenApp):[m
         # Unique number for each monitoring cycle.[m
         self.cycle_number = 0[m
         self.current_link_rates = {}[m
[32m+[m[32m        self.session_id = uuid.uuid4().hex[m
[32m+[m[32m        self.component_energy_model = ComponentEnergyModel()[m
         self.port_to_link = {}[m
 [m
         # ==========================================================[m
[36m@@ -431,6 +438,8 @@[m [mclass TopologyMonitor(app_manager.OSKenApp):[m
             # Start a new monitoring cycle.[m
             # ------------------------------------------------------[m
 [m
[32m+[m[32m            cycle_loop_started = time.monotonic()[m
[32m+[m
             self.cycle_number += 1[m
 [m
             expected_dpids = {[m
[36m@@ -439,6 +448,7 @@[m [mclass TopologyMonitor(app_manager.OSKenApp):[m
 [m
             self.current_cycle = {[m
                 "cycle": self.cycle_number,[m
[32m+[m[32m                "phase": "energy" if self.cycle_number % 2 == 1 else "qos",[m
                 "start_time": time.time(),[m
                 "expected_dpids": expected_dpids,[m
                 "received_dpids": set(),[m
[36m@@ -447,6 +457,9 @@[m [mclass TopologyMonitor(app_manager.OSKenApp):[m
                 "timed_out": False[m
                 }[m
 [m
[32m+[m[32m            if self.current_cycle["phase"] == "qos":[m
[32m+[m[32m                self._publish_qos_request(self.current_cycle)[m
[32m+[m
             # Reset link-rate collection for this cycle.[m
             self._refresh_topology_mapping()[m
             self.current_link_rates = {}[m
[36m@@ -545,12 +558,10 @@[m [mclass TopologyMonitor(app_manager.OSKenApp):[m
                 self._finalize_cycle()[m
 [m
             # ------------------------------------------------------[m
[31m-            # Wait before beginning the next cycle.[m
[31m-            # ------------------------------------------------------[m
[31m-[m
[31m-            hub.sleep([m
[31m-                POLL_INTERVAL_SECONDS[m
[31m-            )[m
[32m+[m[32m            # Keep cycle start-to-start cadence at five seconds.[m
[32m+[m[32m            remaining = POLL_INTERVAL_SECONDS - (time.monotonic() - cycle_loop_started)[m
[32m+[m[32m            if remaining > 0:[m
[32m+[m[32m                hub.sleep(remaining)[m
 [m
     # ================================================================[m
     # REQUEST PORT STATISTICS[m
[36m@@ -1007,6 +1018,70 @@[m [mclass TopologyMonitor(app_manager.OSKenApp):[m
     # FINALIZE MONITORING CYCLE[m
     # ================================================================[m
 [m
[32m+[m[32m    def _publish_qos_request(self, cycle):[m
[32m+[m[32m        """Signal Mininet to begin active probes during this QoS phase."""[m
[32m+[m[32m        request = {[m
[32m+[m[32m            "session_id": self.session_id,[m
[32m+[m[32m            "cycle": int(cycle["cycle"]),[m
[32m+[m[32m            "phase": cycle["phase"],[m
[32m+[m[32m            "timestamp_epoch": time.time(),[m
[32m+[m[32m            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),[m
[32m+[m[32m            "interval_seconds": POLL_INTERVAL_SECONDS,[m
[32m+[m[32m        }[m
[32m+[m[32m        temporary_path = f"{QOS_REQUEST_PATH}.{os.getpid()}.tmp"[m
[32m+[m[32m        with open(temporary_path, "w", encoding="utf-8") as handle:[m
[32m+[m[32m            json.dump(request, handle)[m
[32m+[m[32m        os.replace(temporary_path, QOS_REQUEST_PATH)[m
[32m+[m[32m        self.logger.info([m
[32m+[m[32m            "Published QoS phase request: cycle=%d path=%s",[m
[32m+[m[32m            cycle["cycle"], QOS_REQUEST_PATH,[m
[32m+[m[32m        )[m
[32m+[m
[32m+[m[32m    def _publish_cycle_snapshot(self, cycle):[m
[32m+[m[32m        """Atomically publish the completed 5-second cycle for the Mininet QoS worker."""[m
[32m+[m[32m        topology_switches = get_switch(self, None)[m
[32m+[m[32m        topology_links = get_link(self, None)[m
[32m+[m[32m        link_metrics = [][m
[32m+[m[32m        for link_name, rates in sorted(self.current_link_rates.items()):[m
[32m+[m[32m            if not rates:[m
[32m+[m[32m                continue[m
[32m+[m[32m            src, dst = link_name.split("-", 1)[m
[32m+[m[32m            capacity_mbps = get_link_capacity(src, dst)[m
[32m+[m[32m            if capacity_mbps is None:[m
[32m+[m[32m                continue[m
[32m+[m[32m            rate_mbps = max(rates)[m
[32m+[m[32m            link_metrics.append({[m
[32m+[m[32m                "link": link_name,[m
[32m+[m[32m                "traffic_mbps": rate_mbps,[m
[32m+[m[32m                "capacity_mbps": capacity_mbps,[m
[32m+[m[32m                "utilization_percent": (rate_mbps / capacity_mbps) * 100.0,[m
[32m+[m[32m            })[m
[32m+[m
[32m+[m[32m        snapshot = {[m
[32m+[m[32m            "session_id": self.session_id,[m
[32m+[m[32m            "cycle": int(cycle["cycle"]),[m
[32m+[m[32m            "phase": cycle["phase"],[m
[32m+[m[32m            "timestamp_epoch": time.time(),[m
[32m+[m[32m            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),[m
[32m+[m[32m            "interval_seconds": POLL_INTERVAL_SECONDS,[m
[32m+[m[32m            "switches": sorted({f"s{sw.dp.id}" for sw in topology_switches}),[m
[32m+[m[32m            "links": sorted({[m
[32m+[m[32m                self._link_name(self._canonical_link(link.src.dpid, link.dst.dpid))[m
[32m+[m[32m                for link in topology_links[m
[32m+[m[32m            }),[m
[32m+[m[32m            "network_active_ports": sum(cycle["active_ports"].values()),[m
[32m+[m[32m            "link_metrics": link_metrics,[m
[32m+[m[32m            "energy": cycle.get("energy_result", {}),[m
[32m+[m[32m        }[m
[32m+[m[32m        temporary_path = f"{CYCLE_SNAPSHOT_PATH}.{os.getpid()}.tmp"[m
[32m+[m[32m        with open(temporary_path, "w", encoding="utf-8") as handle:[m
[32m+[m[32m            json.dump(snapshot, handle)[m
[32m+[m[32m        os.replace(temporary_path, CYCLE_SNAPSHOT_PATH)[m
[32m+[m[32m        self.logger.info([m
[32m+[m[32m            "Published cycle snapshot: cycle=%d phase=%s path=%s",[m
[32m+[m[32m            cycle["cycle"], cycle["phase"], CYCLE_SNAPSHOT_PATH,[m
[32m+[m[32m        )[m
[32m+[m
     def _finalize_cycle(self):[m
 [m
         cycle = self.current_cycle[m
[36m@@ -1092,49 +1167,47 @@[m [mclass TopologyMonitor(app_manager.OSKenApp):[m
         # monitoring cycle.[m
         #[m
 [m
[31m-        result = record_state([m
[31m-            network_active_ports,[m
[31m-            duration_seconds=POLL_INTERVAL_SECONDS[m
[31m-        )[m
[31m-[m
[31m-        # ==========================================================[m
[31m-        # ENERGY LOG[m
[31m-        # ==========================================================[m
[31m-[m
[31m-        self.logger.info([m
[31m-            ""[m
[31m-        )[m
[31m-[m
[31m-        self.logger.info([m
[31m-            "**************** ENERGY MODEL ****************"[m
[31m-        )[m
[31m-[m
[31m-        self.logger.info([m
[31m-            "Active Ports       : %d",[m
[31m-            result["active_ports"][m
[31m-        )[m
[31m-[m
[31m-        self.logger.info([m
[31m-            "Power Coefficient  : 0.2 W/port"[m
[31m-        )[m
[31m-[m
[31m-        self.logger.info([m
[31m-            "Modeled Power      : %.4f W",[m
[31m-            result["power_w"][m
[32m+[m[32m        # Record traffic-active port counts separately. These are traffic[m
[32m+[m[32m        # observations, not the input to the component power model.[m
[32m+[m[32m        record_active_port_metrics([m
[32m+[m[32m            cycle=cycle["cycle"],[m
[32m+[m[32m            phase=cycle["phase"],[m
[32m+[m[32m            active_ports_by_switch=cycle["active_ports"],[m
[32m+[m[32m            session_id=self.session_id,[m
         )[m
 [m
[31m-        self.logger.info([m
[31m-            "Interval Energy    : %.8f Wh",[m
[31m-            result["interval_energy_wh"][m
[32m+[m[32m        topology_switches = get_switch(self, None)[m
[32m+[m[32m        topology_links = get_link(self, None)[m
[32m+[m[32m        switch_names = [f"s{sw.dp.id}" for sw in topology_switches][m
[32m+[m[32m        link_names = [[m
[32m+[m[32m            (f"s{link.src.dpid}", f"s{link.dst.dpid}")[m
[32m+[m[32m            for link in topology_links[m
[32m+[m[32m        ][m
[32m+[m[32m        self.component_energy_model.sync_topology(switch_names, link_names)[m
[32m+[m[32m        energy_result = self.component_energy_model.record_cycle([m
[32m+[m[32m            cycle=cycle["cycle"],[m
[32m+[m[32m            duration_seconds=POLL_INTERVAL_SECONDS,[m
[32m+[m[32m            phase=cycle["phase"],[m
[32m+[m[32m            session_id=self.session_id,[m
         )[m
[32m+[m[32m        cycle["energy_result"] = energy_result[m
 [m
         self.logger.info([m
[31m-            "Cumulative Energy  : %.8f Wh",[m
[31m-            result["cumulative_energy_wh"][m
[32m+[m[32m            "COMPONENT-STATE ENERGY | phase=%s | switches=%d active/%d sleeping | "[m
[32m+[m[32m            "links=%d active/%d sleeping | modeled_power=%.2f W | "[m
[32m+[m[32m            "interval_energy=%.6f Wh | cumulative_energy=%.6f Wh",[m
[32m+[m[32m            cycle["phase"],[m
[32m+[m[32m            energy_result["switches_active"],[m
[32m+[m[32m            energy_result["switches_sleeping"],[m
[32m+[m[32m            energy_result["links_active"],[m
[32m+[m[32m            energy_result["links_sleeping"],[m
[32m+[m[32m            energy_result["total_power_w"],[m
[32m+[m[32m            energy_result["interval_energy_wh"],[m
[32m+[m[32m            energy_result["cumulative_energy_wh"],[m
         )[m
[31m-[m
         self.logger.info([m
[31m-            "***********************************************"[m
[32m+[m[32m            "Traffic-active ports remain a separate metric: %d",[m
[32m+[m[32m            network_active_ports,[m
         )[m
 [m
         # ================================================================[m
[36m@@ -1155,18 +1228,12 @@[m [mclass TopologyMonitor(app_manager.OSKenApp):[m
         )[m
 [m
 [m
[31m-        # ================================================================[m
[31m-        # ENERGY GRAPHS[m
[31m-        # ================================================================[m
[31m-[m
[31m-        generate_all_graphs()[m
[31m-[m
[31m-        self.logger.info([m
[31m-            "Energy graphs updated in ./graphs/"[m
[31m-        )[m
[32m+[m[32m        # Component energy is recorded in data/component_energy.csv.[m
 [m
         # ==========================================================[m
         # CLEAR CURRENT CYCLE[m
         # ==========================================================[m
 [m
[32m+[m[32m        self._publish_cycle_snapshot(cycle)[m
[32m+[m
         self.current_cycle = None[m
\ No newline at end of file[m
