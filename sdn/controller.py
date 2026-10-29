
import ipaddress
import os
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

    # -----------------------------------------
    # PROJECT CONFIGURATION
    # -----------------------------------------

    CLIENT_TO_BLOCK = "10.0.0.3"
    SERVER_IP = "10.0.0.4"
    SERVER_PORT = 5000

    # Switch s1
    CLIENT_SWITCH_DPID = 1

    # Local demonstration command file
    POLICY_FILE = "/tmp/sdn_policy_command.txt"

    # Traffic monitoring configuration
    MONITOR_INTERVAL = 5

    # Bottleneck link configuration
    LINK_BANDWIDTH_MBPS = 10.0
    CONGESTION_THRESHOLD_MBPS = 8.0

    # Ports on the inter-switch link
    # Confirm these using "net" in Mininet
    BOTTLENECK_PORTS = {
        (1, 4),  # s1 port 4
        (2, 1)   # s2 port 1
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Learning switch information
        self.mac_to_port = {}

        # Connected OpenFlow switches
        self.datapaths = {}

        # Previous traffic statistics
        self.previous_stats = {}

        # IPs currently blocked from reaching the file server
        self.blocked_ips = set()

        # Clear commands from previous runs
        if os.path.exists(self.POLICY_FILE):
            os.remove(self.POLICY_FILE)

        # Start monitoring tasks
        self.monitor_thread = hub.spawn(self.monitor)
        self.policy_thread = hub.spawn(self.policy_monitor)

        self.logger.info(
            "[START] File Sharing SDN Controller started"
        )

    # -----------------------------------------
    # SWITCH INITIALIZATION
    # -----------------------------------------

    @set_ev_cls(
        ofp_event.EventOFPSwitchFeatures,
        CONFIG_DISPATCHER
    )
    def switch_features_handler(self, ev):

        dp = ev.msg.datapath
        ofproto = dp.ofproto
        parser = dp.ofproto_parser

        self.logger.info(
            "[SWITCH] Connected: %s",
            dp.id
        )

        # Table-miss rule:
        # Send unmatched packets to controller
        match = parser.OFPMatch()

        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER
            )
        ]

        self.add_flow(dp, 0, match, actions)

        # Restore blocking policy after reconnect
        if dp.id == self.CLIENT_SWITCH_DPID:
            for ip in self.blocked_ips:
                self.install_block_rule(dp, ip)

    # -----------------------------------------
    # INSTALL OPENFLOW RULE
    # -----------------------------------------

    def add_flow(self, dp, priority, match, actions):

        ofproto = dp.ofproto
        parser = dp.ofproto_parser

        instructions = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        mod = parser.OFPFlowMod(
            datapath=dp,
            priority=priority,
            match=match,
            instructions=instructions
        )

        dp.send_msg(mod)

    # -----------------------------------------
    # LEARNING SWITCH FUNCTIONALITY
    # -----------------------------------------

    @set_ev_cls(
        ofp_event.EventOFPPacketIn,
        MAIN_DISPATCHER
    )
    def packet_in_handler(self, ev):

        msg = ev.msg
        dp = msg.datapath

        ofproto = dp.ofproto
        parser = dp.ofproto_parser

        dpid = dp.id
        in_port = msg.match["in_port"]

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

        # Learn source MAC and incoming port
        self.mac_to_port[dpid][src] = in_port

        # Find destination port
        out_port = self.mac_to_port[dpid].get(
            dst,
            ofproto.OFPP_FLOOD
        )

        actions = [
            parser.OFPActionOutput(out_port)
        ]

        # Install learned forwarding rule
        if out_port != ofproto.OFPP_FLOOD:

            match = parser.OFPMatch(
                in_port=in_port,
                eth_dst=dst
            )

            self.add_flow(
                dp,
                1,
                match,
                actions
            )

        # Forward current packet
        data = None

        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )

        dp.send_msg(out)

    # -----------------------------------------
    # TRACK CONNECTED SWITCHES
    # -----------------------------------------

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER]
    )
    def state_change_handler(self, ev):

        dp = ev.datapath

        if ev.state == MAIN_DISPATCHER:

            if dp.id not in self.datapaths:

                self.datapaths[dp.id] = dp

                self.logger.info(
                    "[MONITOR] Switch %s registered",
                    dp.id
                )

        elif ev.state == DEAD_DISPATCHER:

            self.datapaths.pop(dp.id, None)
            self.mac_to_port.pop(dp.id, None)

            # Clear old statistics
            for key in list(self.previous_stats):

                if key[0] == dp.id:
                    del self.previous_stats[key]

            self.logger.info(
                "[MONITOR] Switch %s disconnected",
                dp.id
            )

    # -----------------------------------------
    # PERIODIC TRAFFIC MONITORING
    # -----------------------------------------

    def monitor(self):

        while True:

            for dp in list(self.datapaths.values()):
                self.request_stats(dp)

            hub.sleep(self.MONITOR_INTERVAL)

    # -----------------------------------------
    # REQUEST PORT STATISTICS
    # -----------------------------------------

    def request_stats(self, dp):

        parser = dp.ofproto_parser
        ofproto = dp.ofproto

        request = parser.OFPPortStatsRequest(
            dp,
            0,
            ofproto.OFPP_ANY
        )

        dp.send_msg(request)

    # -----------------------------------------
    # PROCESS TRAFFIC STATISTICS
    # -----------------------------------------

    @set_ev_cls(
        ofp_event.EventOFPPortStatsReply,
        MAIN_DISPATCHER
    )
    def port_stats_reply_handler(self, ev):

        dp = ev.msg.datapath
        dpid = dp.id

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

                delta_rx = rx_bytes - old_rx
                delta_tx = tx_bytes - old_tx

                if (
                    elapsed > 0
                    and delta_rx >= 0
                    and delta_tx >= 0
                ):

                    # Calculate traffic rates
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

                    # General traffic monitoring
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

                    # ---------------------------------
                    # CONGESTION DETECTION
                    # ---------------------------------

                    if (
                        (dpid, stat.port_no)
                        in self.BOTTLENECK_PORTS
                    ):

                        traffic_rate = max(
                            rx_mbps,
                            tx_mbps
                        )

                        utilization = (
                            traffic_rate
                            / self.LINK_BANDWIDTH_MBPS
                        ) * 100

                        if (
                            traffic_rate
                            >= self.CONGESTION_THRESHOLD_MBPS
                        ):

                            self.logger.warning(
                                "[CONGESTION] "
                                "Switch=%s Port=%s "
                                "Rate=%.2f Mbps "
                                "Utilization=%.1f%% "
                                "Threshold exceeded!",
                                dpid,
                                stat.port_no,
                                traffic_rate,
                                utilization
                            )

            # Save counters for next interval
            self.previous_stats[key] = (
                rx_bytes,
                tx_bytes,
                now
            )

    # -----------------------------------------
    # TCP BLOCKING RULE
    # -----------------------------------------

    def block_match(self, dp, ip):

        parser = dp.ofproto_parser

        return parser.OFPMatch(
            eth_type=0x0800,
            ip_proto=6,
            ipv4_src=ip,
            ipv4_dst=self.SERVER_IP,
            tcp_dst=self.SERVER_PORT
        )

    # -----------------------------------------
    # INSTALL BLOCK RULE
    # -----------------------------------------

    def install_block_rule(self, dp, ip):

        match = self.block_match(dp, ip)

        # Empty actions = DROP
        # Priority 100 overrides learning rules
        self.add_flow(
            dp,
            100,
            match,
            []
        )

        self.logger.info(
            "[POLICY] BLOCK rule sent to switch %s: "
            "%s -> %s TCP/%s",
            dp.id,
            ip,
            self.SERVER_IP,
            self.SERVER_PORT
        )

    # -----------------------------------------
    # REMOVE BLOCK RULE
    # -----------------------------------------

    def remove_block_rule(self, dp, ip):

        ofproto = dp.ofproto
        parser = dp.ofproto_parser

        match = self.block_match(dp, ip)

        mod = parser.OFPFlowMod(
            datapath=dp,
            command=ofproto.OFPFC_DELETE_STRICT,
            priority=100,
            match=match,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY
        )

        dp.send_msg(mod)

        self.logger.info(
            "[POLICY] UNBLOCK rule removal sent "
            "to switch %s",
            dp.id
        )

    # -----------------------------------------
    # DYNAMIC POLICY COMMAND MONITOR
    #
    # Commands are read from POLICY_FILE, one per line:
    #   BLOCK [ip]     drop ip -> server TCP/5000 (default CLIENT_TO_BLOCK)
    #   UNBLOCK [ip]   remove that rule
    #   STATUS         log the blocked IPs
    # Written by hand during the demo, or by the file server
    # (server/sdn_bridge.py) after repeated failed logins.
    # -----------------------------------------

    def handle_policy_command(self, line):

        parts = line.split()

        if not parts:
            return

        command = parts[0].upper()

        ip = parts[1] if len(parts) > 1 else self.CLIENT_TO_BLOCK

        if command in ("BLOCK", "UNBLOCK"):

            try:
                ipaddress.IPv4Address(ip)

            except ValueError:
                self.logger.warning(
                    "[POLICY] Invalid IP address: %s",
                    ip
                )
                return

        dp = self.datapaths.get(self.CLIENT_SWITCH_DPID)

        if command == "BLOCK":

            self.blocked_ips.add(ip)

            if dp is not None:
                self.install_block_rule(dp, ip)

            else:
                self.logger.warning("[POLICY] s1 not connected")

        elif command == "UNBLOCK":

            self.blocked_ips.discard(ip)

            if dp is not None:
                self.remove_block_rule(dp, ip)

            else:
                self.logger.warning("[POLICY] s1 not connected")

        elif command == "STATUS":

            self.logger.info(
                "[POLICY] Blocked IPs: %s",
                sorted(self.blocked_ips)
            )

        else:

            self.logger.warning(
                "[POLICY] Unknown command: %s",
                line
            )

    def policy_monitor(self):

        while True:

            try:

                if os.path.isfile(self.POLICY_FILE):

                    with open(
                        self.POLICY_FILE,
                        "r"
                    ) as f:

                        lines = f.read().splitlines()

                    os.remove(self.POLICY_FILE)

                    for line in lines:
                        self.handle_policy_command(line.strip())

            except OSError as error:

                self.logger.error(
                    "[POLICY] File error: %s",
                    error
                )

            hub.sleep(1)
