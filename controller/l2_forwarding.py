"""
controller/l2_forwarding.py

Minimal L2 learning switch -- os-ken 4.2.2 compatible.
Milestone 1 sanity-check app: confirms the controller can load, listen
for OpenFlow connections, and handle packet_in events.
"""
from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet, ethernet, ether_types


# IEEE 802.1 link-local control group: bridge protocols such as LLDP/STP
# must not be learned or flooded as ordinary host traffic.
BRIDGE_CONTROL_MAC_PREFIX = "01:80:c2:00:00:"


class L2Switch(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(L2Switch, self).__init__(*args, **kwargs)
        self.mac_to_port = {}

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                           ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        self.logger.info("Switch connected: dpid=%s", datapath.id)

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                 match=match, instructions=inst)
        datapath.send_msg(mod)
        self.logger.info(
            "FLOW_MOD sent: dpid=%s priority=%s match=%s actions=%s",
            datapath.id, priority, match, actions,
        )

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            self.logger.warning(
                "PacketIn without Ethernet header: dpid=%s in_port=%s",
                dpid, in_port,
            )
            return

        dst = eth.dst.lower()

        # OS-Ken's topology discovery app injects LLDP frames to discover
        # links. The discovery app consumes their PacketIn events too, but
        # this learning switch must not flood them back into the fabric.
        # The 01:80:c2:00:00:xx group is reserved for link-local bridge
        # control protocols and is not ordinary end-host traffic.
        if (
            eth.ethertype == ether_types.ETH_TYPE_LLDP
            or dst.startswith(BRIDGE_CONTROL_MAC_PREFIX)
        ):
            self.logger.debug(
                "Ignoring bridge control frame: dpid=%s in_port=%s "
                "src=%s dst=%s ethertype=0x%04x",
                dpid, in_port, eth.src, dst, eth.ethertype,
            )
            return

        src = eth.src

        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        out_port = self.mac_to_port[dpid].get(dst, ofproto.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        self.logger.info(
            "PACKET_IN: dpid=%s in_port=%s src=%s dst=%s buffer_id=%s "
            "decision=%s",
            dpid, in_port, src, dst, msg.buffer_id,
            "FLOOD" if out_port == ofproto.OFPP_FLOOD else f"PORT_{out_port}",
        )

        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
            self.add_flow(datapath, 1, match, actions)

        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                   in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)
        self.logger.info(
            "PACKET_OUT sent: dpid=%s in_port=%s out_port=%s "
            "buffer_id=%s data_included=%s",
            dpid, in_port, out_port, msg.buffer_id, data is not None,
        )
