# ===============================
# SDN HOST DISCOVERY CONTROLLER
# ===============================
# This is a Ryu SDN controller that:
# - Learns hosts (MAC, IP, switch, port)
# - Tracks activity (active/inactive)
# - Prints a live table in terminal

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, arp, ipv4
from ryu.lib import hub

import datetime
import threading
import time
import os

# ===============================
# CONFIGURATION
# ===============================
INACTIVE_AFTER = 30   # seconds before marking host inactive
MONITOR_EVERY  = 10   # how often to check inactivity

# ===============================
# HELPER FUNCTIONS
# ===============================

def now_str():
    """Return current time as HH:MM:SS string"""
    return datetime.datetime.now().strftime("%H:%M:%S")


def print_banner():
    """Print startup banner in terminal"""
    os.system("clear")
    print("SDN HOST DISCOVERY CONTROLLER")
    print("Controller running on port 6653")


def print_host_table(host_db):
    """Display all discovered hosts in a table"""
    print("\n==== HOST TABLE ====")

    if not host_db:
        print("No hosts discovered yet")
        return

    for mac, h in host_db.items():
        print(f"MAC: {mac} | IP: {h['ip']} | Switch: {h['dpid']} | Port: {h['port']} | Status: {h['status']}")


# ===============================
# MAIN CONTROLLER CLASS
# ===============================

class HostDiscoveryController(app_manager.RyuApp):

    # Use OpenFlow 1.3
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        """Initialize controller"""
        super().__init__(*args, **kwargs)

        # MAC learning table: dpid -> {mac -> port}
        self.mac_to_port = {}

        # Host database: mac -> host info
        self.host_db = {}

        # Thread lock for safe updates
        self._lock = threading.Lock()

        print_banner()
        print(f"[{now_str()}] Controller started")

        # Start background thread to monitor inactive hosts
        self.monitor_thread = hub.spawn(self._monitor_loop)


    # ===============================
    # SWITCH CONNECTION HANDLER
    # ===============================

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Called when a switch connects"""

        dp = ev.msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        # Install table-miss rule (send unknown packets to controller)
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER)]

        self._add_flow(dp, 0, match, actions)

        print(f"[{now_str()}] SWITCH CONNECTED: {dp.id}")


    # ===============================
    # PACKET HANDLER (CORE LOGIC)
    # ===============================

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """Handles every packet sent to controller"""

        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        in_port = msg.match['in_port']
        dpid = dp.id

        # Parse packet
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # Ignore invalid packets
        if eth is None:
            return

        # Ignore LLDP packets
        if eth.ethertype == 0x88cc:
            return

        src_mac = eth.src
        dst_mac = eth.dst

        # Learn MAC -> port mapping
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src_mac] = in_port

        # Extract IP address from packet
        src_ip = self._extract_ip(pkt)

        # Update host database
        self._update_host(src_mac, src_ip, dpid, in_port)

        # Decide output port
        out_port = self.mac_to_port[dpid].get(dst_mac, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        # Install flow rule if destination is known
        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_src=src_mac, eth_dst=dst_mac)
            self._add_flow(dp, 1, match, actions)

        # Send packet out
        dp.send_msg(parser.OFPPacketOut(
            datapath=dp,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data
        ))


    # ===============================
    # HOST DATABASE UPDATE
    # ===============================

    def _update_host(self, mac, ip, dpid, port):
        """Add or update host entry"""

        ts = now_str()
        dpid_str = str(dpid)

        with self._lock:
            if mac not in self.host_db:
                # New host discovered
                self.host_db[mac] = {
                    "mac": mac,
                    "ip": ip or "unknown",
                    "dpid": dpid_str,
                    "port": port,
                    "first_seen": ts,
                    "last_seen": ts,
                    "pkt_count": 1,
                    "status": "active",
                    "_last_ts": time.time()
                }

                print(f"[{ts}] NEW HOST: {mac} ({ip})")

            else:
                # Existing host update
                h = self.host_db[mac]
                h["last_seen"] = ts
                h["pkt_count"] += 1
                h["status"] = "active"
                h["_last_ts"] = time.time()

                # Update IP if newly discovered
                if ip and h["ip"] == "unknown":
                    h["ip"] = ip
                    print(f"[{ts}] IP RESOLVED: {mac} -> {ip}")

        print_host_table(self.host_db)


    # ===============================
    # INACTIVE HOST MONITOR
    # ===============================

    def _monitor_loop(self):
        """Background thread to mark inactive hosts"""

        while True:
            hub.sleep(MONITOR_EVERY)
            now_ts = time.time()

            with self._lock:
                for mac, h in self.host_db.items():
                    # If no packets for threshold time
                    if now_ts - h["_last_ts"] > INACTIVE_AFTER:
                        h["status"] = "inactive"
                        print(f"[{now_str()}] HOST INACTIVE: {mac}")


    # ===============================
    # FLOW INSTALLATION
    # ===============================

    def _add_flow(self, dp, priority, match, actions):
        """Install flow rule in switch"""

        ofp = dp.ofproto
        parser = dp.ofproto_parser

        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]

        dp.send_msg(parser.OFPFlowMod(
            datapath=dp,
            priority=priority,
            match=match,
            instructions=inst
        ))


    # ===============================
    # IP EXTRACTION
    # ===============================

    def _extract_ip(self, pkt):
        """Extract IP from ARP or IPv4 packet"""

        # Check ARP packet
        a = pkt.get_protocol(arp.arp)
        if a:
            return a.src_ip

        # Check IPv4 packet
        i = pkt.get_protocol(ipv4.ipv4)
        if i:
            return i.src

        return None
