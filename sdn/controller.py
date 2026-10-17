import time

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls
)
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet, ethernet, ether_types
from os_ken.lib import hub


class FileSharingController(app_manager.OSKenApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # MAC address learning table
        self.mac_to_port = {}

        # Connected SDN switches
        self.datapaths = {}

        # Previous port statistics
        self.previous_stats = {}

        # Start periodic traffic monitoring
        self.monitor_thread = hub.spawn(self.monitor)

        self.logger.info(
            "File Sharing SDN Controller started"
        )

    # ------------------------------------------
    # SWITCH CONNECTION AND INITIAL FLOW RULES
    # ------------------------------------------

    @set_ev_cls(
        ofp_event.EventOFPSwitchFeatures,
        CONFIG_DISPATCHER
    )
    def switch_features_handler(self, ev):

        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        self.logger.info(
            "[SWITCH] Connected: %s",
            datapath.id
        )

        # Table-miss flow rule:
        # Send unmatched packets to controller
        match = parser.OFPMatch()

        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER
            )
        ]

        self.add_flow(
            datapath,
            0,
            match,
            actions
        )

    # ------------------------------------------
    # INSTALL OPENFLOW RULES
    # ------------------------------------------

    def add_flow(self, datapath, priority, match, actions):

        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        instructions = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=instructions
        )

        datapath.send_msg(mod)

    # ------------------------------------------
    # LEARNING SWITCH PACKET HANDLING
    # ------------------------------------------

    @set_ev_cls(
        ofp_event.EventOFPPacketIn,
        MAIN_DISPATCHER
    )
    def packet_in_handler(self, ev):

        msg = ev.msg
        datapath = msg.datapath

        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        in_port = msg.match['in_port']
        dpid = datapath.id

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        # Ignore LLDP packets
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        src = eth.src
        dst = eth.dst

        self.mac_to_port.setdefault(dpid, {})

        # Learn source MAC address
        self.mac_to_port[dpid][src] = in_port

        # Find forwarding port
        out_port = self.mac_to_port[dpid].get(
            dst,
            ofproto.OFPP_FLOOD
        )

        actions = [
            parser.OFPActionOutput(out_port)
        ]

        # Install forwarding rule for known destination
        if out_port != ofproto.OFPP_FLOOD:

            match = parser.OFPMatch(
                in_port=in_port,
                eth_dst=dst
            )

            self.add_flow(
                datapath,
                1,
                match,
                actions
            )

        # Forward current packet
        data = None

        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )

        datapath.send_msg(out)

    # ------------------------------------------
    # TRACK CONNECTED SWITCHES
    # ------------------------------------------

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER]
    )
    def state_change_handler(self, ev):

        datapath = ev.datapath

        if ev.state == MAIN_DISPATCHER:

            if datapath.id not in self.datapaths:

                self.datapaths[datapath.id] = datapath

                self.logger.info(
                    "[MONITOR] Switch %s registered",
                    datapath.id
                )

        elif ev.state == DEAD_DISPATCHER:

            if datapath.id in self.datapaths:

                del self.datapaths[datapath.id]

                # Clear old counters on disconnection
                for key in list(self.previous_stats):
                    if key[0] == datapath.id:
                        del self.previous_stats[key]

                self.logger.info(
                    "[MONITOR] Switch %s disconnected",
                    datapath.id
                )

    # ------------------------------------------
    # PERIODIC TRAFFIC MONITORING
    # ------------------------------------------

    def monitor(self):

        while True:

            for datapath in list(self.datapaths.values()):
                self.request_stats(datapath)

            # Poll switches every 5 seconds
            hub.sleep(5)

    # ------------------------------------------
    # REQUEST PORT STATISTICS
    # ------------------------------------------

    def request_stats(self, datapath):

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        request = parser.OFPPortStatsRequest(
            datapath,
            0,
            ofproto.OFPP_ANY
        )

        datapath.send_msg(request)

    # ------------------------------------------
    # PROCESS PORT STATISTICS
    # ------------------------------------------

    @set_ev_cls(
        ofp_event.EventOFPPortStatsReply,
        MAIN_DISPATCHER
    )
    def port_stats_reply_handler(self, ev):

        datapath = ev.msg.datapath
        dpid = datapath.id

        now = time.monotonic()

        for stat in ev.msg.body:

            # Ignore reserved OpenFlow ports
            if stat.port_no > 0xFFFFFF00:
                continue

            key = (dpid, stat.port_no)

            rx_bytes = stat.rx_bytes
            tx_bytes = stat.tx_bytes

            previous = self.previous_stats.get(key)

            if previous is not None:

                old_rx, old_tx, old_time = previous

                elapsed = now - old_time

                if elapsed > 0:

                    delta_rx = rx_bytes - old_rx
                    delta_tx = tx_bytes - old_tx

                    # Avoid misleading rates if counters reset
                    if delta_rx >= 0 and delta_tx >= 0:

                        rx_mbps = (
                            delta_rx * 8
                            / elapsed
                            / 1_000_000
                        )

                        tx_mbps = (
                            delta_tx * 8
                            / elapsed
                            / 1_000_000
                        )

                        self.logger.info(
                            "[TRAFFIC] Switch=%s Port=%s "
                            "RX=%.3f Mbps TX=%.3f Mbps "
                            "RX_packets=%s TX_packets=%s",
                            dpid,
                            stat.port_no,
                            rx_mbps,
                            tx_mbps,
                            stat.rx_packets,
                            stat.tx_packets
                        )

            # Save counters for next interval
            self.previous_stats[key] = (
                rx_bytes,
                tx_bytes,
                now
            )
