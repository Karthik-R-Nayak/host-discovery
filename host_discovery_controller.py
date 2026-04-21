"""
host_discovery_controller.py — Ryu SDN Controller with Terminal Output
Run with: ryu-manager host_discovery_controller.py --observe-links
"""

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

# ── Terminal ANSI colors ───────────────────────────────────────────────────────
R  = "\033[0m"    # reset
B  = "\033[1m"    # bold
CY = "\033[96m"   # cyan
GR = "\033[92m"   # green
YL = "\033[93m"   # yellow
RD = "\033[91m"   # red
BL = "\033[94m"   # blue
DM = "\033[90m"   # dim grey
MG = "\033[95m"   # magenta

INACTIVE_AFTER = 30    # seconds without a packet → mark host inactive
MONITOR_EVERY  = 10    # background check interval in seconds


def now_str():
    return datetime.datetime.now().strftime("%H:%M:%S")


def line(char="─", width=74):
    return DM + char * width + R


def print_banner():
    os.system("clear")
    print(line("═"))
    print(f"{B}{CY}   SDN HOST DISCOVERY CONTROLLER{R}   {DM}OpenFlow 1.3  ·  Ryu Framework{R}")
    print(line("═"))
    print(f"  {DM}Controller port : 6653{R}")
    print(f"  {DM}Inactive timeout: {INACTIVE_AFTER}s{R}")
    print(f"  {DM}Press Ctrl+C to stop{R}")
    print(line())
    print()


def print_host_table(host_db: dict):
    """Render the full host database as a formatted terminal table."""
    hosts = list(host_db.values())
    total    = len(hosts)
    active   = sum(1 for h in hosts if h["status"] == "active")
    inactive = total - active
    switches = len({h["dpid"] for h in hosts})

    print()
    print(line("═"))
    print(
        f"{B}{CY}  HOST DATABASE{R}  "
        f"{DM}total={B}{total}{R}{DM}  active={R}{GR}{active}{R}"
        f"{DM}  inactive={R}{RD}{inactive}{R}"
        f"{DM}  switches={R}{BL}{switches}{R}"
    )
    print(line("═"))

    if not hosts:
        print(f"\n  {DM}No hosts discovered yet.{R}")
        print(f"  {DM}→ Run{R} {B}pingall{R} {DM}in the Mininet CLI to trigger discovery.{R}\n")
        print(line())
        return

    # Column header
    print(
        f"  {B}{DM}"
        f"{'#':<4}"
        f"{'MAC Address':<20}"
        f"{'IP Address':<16}"
        f"{'Switch DPID':<22}"
        f"{'Port':<6}"
        f"{'Pkts':<7}"
        f"{'Status':<11}"
        f"{'First Seen':<12}"
        f"{'Last Seen':<10}"
        f"{R}"
    )
    print(line("─"))

    for i, h in enumerate(hosts, 1):
        if h["status"] == "active":
            status_col = f"{GR}● ACTIVE  {R}"
        else:
            status_col = f"{RD}○ INACTIVE{R}"

        ip_col = h["ip"] if h["ip"] != "—" else f"{DM}resolving…{R}"

        print(
            f"  {DM}{i:<4}{R}"
            f"{CY}{h['mac']:<20}{R}"
            f"{B}{ip_col:<16}{R}"
            f"{BL}{h['dpid']:<22}{R}"
            f"{YL}{h['port']:<6}{R}"
            f"{MG}{h['pkt_count']:<7}{R}"
            f"{status_col}"
            f"{DM}{h['first_seen']:<12}{h['last_seen']:<10}{R}"
        )

    print(line("─"))
    print(f"  {DM}Updated: {now_str()}{R}")
    print(line())
    print()


class HostDiscoveryController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # mac_to_port[dpid][mac] = port_no
        self.mac_to_port: dict[int, dict[str, int]] = {}

        # host_db[mac] = { ... host info ... }
        self.host_db: dict[str, dict] = {}

        self._lock = threading.Lock()

        print_banner()
        print(f"  {GR}[{now_str()}] Controller started — waiting for switches…{R}\n")

        # Background greenlet to mark stale hosts inactive
        self.monitor_thread = hub.spawn(self._monitor_loop)

    # ── Switch connects ────────────────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp     = ev.msg.datapath
        ofp    = dp.ofproto
        parser = dp.ofproto_parser

        # Install table-miss: send all unmatched packets to controller
        match   = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self._add_flow(dp, priority=0, match=match, actions=actions)

        dpid_str = f"{dp.id:#018x}"
        print(f"  {GR}[{now_str()}]  SWITCH CONNECTED{R}   dpid={CY}{dpid_str}{R}")

    # ── Packet arrives ─────────────────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg     = ev.msg
        dp      = msg.datapath
        ofp     = dp.ofproto
        parser  = dp.ofproto_parser
        in_port = msg.match["in_port"]
        dpid    = dp.id

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return
        if eth.ethertype == 0x88cc:   # ignore LLDP
            return

        src_mac = eth.src
        dst_mac = eth.dst

        # ── MAC learning ──
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src_mac] = in_port

        src_ip = self._extract_ip(pkt)
        self._update_host(src_mac, src_ip, dpid, in_port)

        # ── Forwarding ──
        out_port = self.mac_to_port[dpid].get(dst_mac, ofp.OFPP_FLOOD)
        actions  = [parser.OFPActionOutput(out_port)]

        if out_port != ofp.OFPP_FLOOD:
            m = parser.OFPMatch(in_port=in_port, eth_dst=dst_mac, eth_src=src_mac)
            self._add_flow(dp, priority=1, match=m, actions=actions)

        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(parser.OFPPacketOut(
            datapath=dp, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions, data=data
        ))

    # ── Host DB logic ──────────────────────────────────────────────────────────

    def _update_host(self, mac: str, ip: str | None, dpid: int, port: int):
        ts       = now_str()
        dpid_str = f"{dpid:#018x}"
        is_new   = mac not in self.host_db

        with self._lock:
            if is_new:
                self.host_db[mac] = {
                    "mac":        mac,
                    "ip":         ip or "—",
                    "dpid":       dpid_str,
                    "port":       port,
                    "first_seen": ts,
                    "last_seen":  ts,
                    "pkt_count":  1,
                    "status":     "active",
                    "_last_ts":   time.time(),
                }
                print(
                    f"\n  {GR}[{ts}]  ★  NEW HOST DISCOVERED{R}\n"
                    f"        MAC    : {CY}{mac}{R}\n"
                    f"        IP     : {B}{ip if ip else 'resolving…'}{R}\n"
                    f"        DPID   : {BL}{dpid_str}{R}\n"
                    f"        Port   : {YL}{port}{R}"
                )
                print_host_table(self.host_db)

            else:
                entry = self.host_db[mac]
                entry["last_seen"] = ts
                entry["pkt_count"] += 1
                entry["status"]   = "active"
                entry["_last_ts"] = time.time()
                changed = False

                # IP resolved for the first time
                if ip and entry["ip"] == "—":
                    entry["ip"] = ip
                    print(
                        f"\n  {BL}[{ts}]  IP RESOLVED{R}  "
                        f"{CY}{mac}{R}  →  {B}{ip}{R}"
                    )
                    changed = True

                # Host moved to a different port or switch
                if entry["port"] != port or entry["dpid"] != dpid_str:
                    print(
                        f"\n  {YL}[{ts}]  HOST MOVED{R}  {CY}{mac}{R}\n"
                        f"        port  : {entry['port']} → {YL}{port}{R}\n"
                        f"        dpid  : {entry['dpid']} → {BL}{dpid_str}{R}"
                    )
                    entry["port"] = port
                    entry["dpid"] = dpid_str
                    changed = True

                if changed:
                    print_host_table(self.host_db)

    # ── Background monitor ─────────────────────────────────────────────────────

    def _monitor_loop(self):
        while True:
            hub.sleep(MONITOR_EVERY)
            now_ts  = time.time()
            changed = False

            with self._lock:
                for mac, h in self.host_db.items():
                    if h["status"] == "active" and (now_ts - h["_last_ts"]) > INACTIVE_AFTER:
                        h["status"] = "inactive"
                        print(
                            f"\n  {RD}[{now_str()}]  HOST INACTIVE{R}  "
                            f"{CY}{mac}{R}  {DM}(no packets for {INACTIVE_AFTER}s){R}"
                        )
                        changed = True

                if changed:
                    print_host_table(self.host_db)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _add_flow(self, dp, priority, match, actions):
        ofp  = dp.ofproto
        par  = dp.ofproto_parser
        inst = [par.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(par.OFPFlowMod(
            datapath=dp, priority=priority,
            match=match, instructions=inst
        ))

    @staticmethod
    def _extract_ip(pkt) -> str | None:
        a = pkt.get_protocol(arp.arp)
        if a:
            return a.src_ip
        i = pkt.get_protocol(ipv4.ipv4)
        if i:
            return i.src
        return None
