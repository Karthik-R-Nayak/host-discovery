# SDN Host Discovery — Terminal Edition

## What You Need to Install

### 1. Mininet
```bash
sudo apt update
sudo apt install -y mininet
```
Verify: `mn --version`

### 2. Open vSwitch (usually installed with Mininet, but confirm)
```bash
sudo apt install -y openvswitch-switch
sudo service openvswitch-switch start
```
Verify: `ovs-vsctl show`

### 3. Ryu SDN Framework
```bash
pip3 install ryu
```
If that fails due to a Python version conflict, try:
```bash
pip3 install ryu --break-system-packages
```
Verify: `ryu-manager --version`

### 4. Python packages used by Ryu (usually auto-installed)
```bash
pip3 install eventlet oslo.config
```

That's it — no Flask, no browser, no frontend needed.

---

## Files

```
sdn_host_discovery/
├── host_discovery_controller.py   ← Ryu controller (run this first)
└── topology.py                    ← Mininet topology  (run this second)
```

---

## How to Run (2 Terminals)

### Terminal 1 — Start the Ryu Controller
```bash
ryu-manager host_discovery_controller.py --observe-links
```
You will see:
```
══════════════════════════════════════════════════════════════════════════
   SDN HOST DISCOVERY CONTROLLER   OpenFlow 1.3  ·  Ryu Framework
══════════════════════════════════════════════════════════════════════════
  Controller port : 6653
  [12:00:01] Controller started — waiting for switches…
```

### Terminal 2 — Start Mininet
```bash
sudo python3 topology.py
```
You will see the Mininet CLI prompt: `mininet>`

### Trigger Discovery
In the Mininet CLI:
```
mininet> pingall
```

Watch **Terminal 1** — you will see hosts appear in real time.

---

## Sample Terminal Output

```
  [12:00:05]  SWITCH CONNECTED   dpid=0x0000000000000001
  [12:00:05]  SWITCH CONNECTED   dpid=0x0000000000000002
  [12:00:05]  SWITCH CONNECTED   dpid=0x0000000000000003

  [12:00:08]  ★  NEW HOST DISCOVERED
        MAC    : 00:00:00:00:00:01
        IP     : 10.0.0.1
        DPID   : 0x0000000000000002
        Port   : 1

══════════════════════════════════════════════════════════════════════════
  HOST DATABASE  total=1  active=1  inactive=0  switches=1
══════════════════════════════════════════════════════════════════════════
  #   MAC Address         IP Address      Switch DPID           Port  Pkts   Status      First Seen  Last Seen
  ─────────────────────────────────────────────────────────────────────────
  1   00:00:00:00:00:01   10.0.0.1        0x000000000002        1     3      ● ACTIVE    12:00:08    12:00:08
  ─────────────────────────────────────────────────────────────────────────
```

---

## What Each Event Means

| Event printed            | Meaning                                          |
|--------------------------|--------------------------------------------------|
| `SWITCH CONNECTED`       | OVS switch completed OpenFlow handshake          |
| `★ NEW HOST DISCOVERED`  | A new MAC was seen for the first time            |
| `IP RESOLVED`            | ARP packet revealed the IP of a known MAC        |
| `HOST MOVED`             | Host reappeared on a different port or switch    |
| `HOST INACTIVE`          | No packet from host in 30 seconds                |

---

## Useful Mininet CLI Commands

```bash
mininet> pingall          # ping every host pair → triggers full discovery
mininet> ping h1 h4       # ping between specific hosts
mininet> h1 ifconfig      # show h1 network interface
mininet> net              # show links
mininet> dump             # show all nodes
mininet> exit             # stop the network
```

## Cleanup (if Mininet crashes)
```bash
sudo mn -c
```
