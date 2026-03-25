# Schemas and Data Models

Reference documentation for the data formats used in the DASH/MASH event infrastructure.

## CoP Database (Pending)

The contractor team is building the Track Manager and SmartPack Manager databases for MASH. We don't have the final schema yet. When it arrives, map the entity types below to the actual database fields.

**Key design decision from the planning meeting:** All database objects have a freeform JSON metadata dictionary (string key-value pairs). If extracted data doesn't map to a strict schema field, put it in metadata. Don't lose data just because we can't categorize it yet.

## BattleEffect Schema V2

From DASH 2, this is the vendor output format. It shows the entity model we'll likely be populating:

```json
{
  "id": "string",
  "status": "pending | active | completed",
  "entityOfInterest": {
    "id": "string",
    "type": "string",
    "subtype": "string",
    "name": "string",
    "position": {
      "grid": "string",
      "latitude": 0.0,
      "longitude": 0.0,
      "altitude": 0,
      "accuracy": 0,
      "source": "string",
      "timestamp": "ISO-8601"
    },
    "velocity": {
      "speed": 0,
      "heading": 0,
      "climbRate": 0,
      "units": "string"
    },
    "trackId": "string",
    "trackNumber": "string",
    "trackQuality": 0,
    "platform": "string",
    "affiliation": "string",
    "affiliationConfidence": "string",
    "threatLevel": "string",
    "lane": "string",
    "tags": [
      {
        "type": "string",
        "value": "string",
        "description": "string",
        "attributes": {}
      }
    ],
    "history": [
      {
        "timestamp": "ISO-8601",
        "position": {},
        "affiliation": "string",
        "source": "string"
      }
    ]
  },
  "effectOperator": "DESTROY | JAM | MONITOR | ...",
  "timeWindow": {
    "type": "IMMEDIATE | ASAP | ...",
    "description": "string"
  },
  "chatMessage": "string",
  "justification": "string",
  "formattedText": "string",
  "tags": [],
  "objectiveAlignments": []
}
```

## GenMSG Track Fields

Track data from the MACE simulation, received via TCP (port 5001) or UDP multicast (224.9.8.63:5003).

### Key Fields

| Group | Field | Description |
| ----- | ----- | ----------- |
| Metadata | `numBytes` | Packet size |
| Metadata | `timeSeconds` | Unix timestamp |
| Metadata | `trackNumber` | Internal track number |
| Position | `e1.latitude` | Geodetic latitude (degrees) |
| Position | `e1.longitude` | Geodetic longitude (degrees) |
| Position | `e1.altitude` | Altitude MSL (meters) |
| Kinematics | `e1.groundSpeed` | Speed over ground (m/s) |
| Kinematics | `e1.heading` | Direction of travel |
| Quality | `e1.trackQuality` | Confidence (0=low, 7+=high) |
| Classification | `e1.trackCategory` | Air, Land, Surface, Space |
| Classification | `e1.trackId` | Friend, Hostile, Unknown |
| Classification | `e1.platform` | Platform class (Fighter, Bomber, Ship, etc.) |
| Classification | `e1.platformActivity` | On Mission, RTB, Engaged |
| IFF | `e7.mode3` | Mode 3/A transponder code |
| Callsign | `e12.callsign1-8` | ASCII callsign characters |
| Source | `e15.sourceId` | Reporting radar/sensor ID |
| Link 16 | `e24.JTN` | J-series Track Number |

### Coordinate Systems in Chat

Chat messages use three coordinate systems that must be parsed:

1. **Lat/Lon** (easiest): `N21.774294, W72.279964` or `N 24 03.6134' W 074 31.4900'`
2. **Bullseye/Cigar** (requires reference point): `CIGAR 324/275` = bearing 324, range 275 from bullseye
3. **MGRS** (in fire missions): `17QNE9779269855`

## IRC Server Configuration

From DASH 3 `.http/irc/config.json`:

```json
{
  "server": {
    "url": "ws://10.5.185.72:8097",
    "autojoin": "#isr_reports,#jprc,#c2_coord,#fires,#stt_C2Coord,#stt_mesquiteBMA,#stt_hydroBMA,#stt_crusherBMA,#stt_taipanBMA,#vegas_internal"
  }
}
```

Protocol is WebSocket, not raw IRC. All voice STT is already piped into IRC as `#stt_*` channels.

## Track Number Extraction Patterns

Regex rules for extracting track numbers from chat (adapted from prior DASH event tooling):

```json
{
  "rules": [
    {"pattern": "[tT]\\s*[mM]\\s*(\\d{3})", "label": "Track TM###"},
    {"pattern": "[dD]\\s*[aA]\\s*(\\d{3})", "label": "Track DA###"},
    {"pattern": "(?:working|wrking|wkrg|wkg)\\s*(\\d{5})", "label": "Working track"},
    {"pattern": "(?:ctn|cnt|cttn)\\s*(\\d{5})", "label": "Contact track"},
    {"pattern": "(?:tracks|trks|tn|TN:)\\s*(\\d{5})", "label": "Track number"},
    {"pattern": "(?:contact|contacts)\\s*(\\d{5})", "label": "Contact"},
    {"pattern": "(\\d{5})\\s*(?:match)", "label": "Match track"},
    {"pattern": "(?:ref)\\s*(\\d{5})", "label": "Reference track"},
    {"pattern": "(?:id)\\s*(\\d{5})", "label": "ID track"}
  ]
}
```

## GenMSG Listener Scripts

### TCP Listener

```python
import socket

SERVER_IP = '10.5.185.9'
SERVER_PORT = 5001

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.connect((SERVER_IP, SERVER_PORT))
    while True:
        data = s.recv(1024)
        if not data:
            break
        print(f"[RECEIVED] {data.decode()}")
```

### UDP Multicast Listener

```python
import socket
import struct

MCAST_GRP = '224.9.8.63'
MCAST_PORT = 5003

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(('', MCAST_PORT))

mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

while True:
    data, addr = sock.recvfrom(50240)
    print(f"Received from {addr}: {data.decode(errors='replace')}")
```
