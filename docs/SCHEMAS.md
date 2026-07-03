# Schemas and Data Models

Reference documentation for the data formats used in the DASH/MASH event infrastructure.

## CoP Database (DISCOVERED 2026-05-05)

Full OpenAPI specs pulled from the live MASH network. Raw specs in `data/mash_schemas/<service>/openapi_spec.json`.

### Track Manager (http://10.0.0.1:3021)

The primary write target for chat-to-cop. 14 endpoints, 8 schemas.

**Write endpoints:**
- `POST /tracks` -- Create a new track (assigns ID if not provided)
- `PUT /tracks/{id}` -- Create or update (upsert) by ID

**Query endpoints:**
- `GET /tracks` -- All tracks
- `GET /tracks/{id}` -- By ID
- `GET /tracks/label/{label}` -- By label (callsign)
- `GET /tracks/identifier/{identifier}` -- By id, label, sourceId, JTN, or alias
- `GET /tracks/alias/{identifier}` -- Aliases for a track
- `GET /trackAliasByJtn/{jtn}` -- Aliases by JTN

**Streaming:**
- `GET /tracks-sse` -- Server-Sent Events stream of TrackCreatedOrUpdated

**Track schema:**
```json
{
  "id": "string",
  "sourceId": "string|null",
  "aliases": {"string": "string"},
  "originator": "string|null",
  "label": "string|null",
  "entityType": "string|null",
  "cotType": "string|null",
  "location": {"type": "Point", "coordinates": [lon, lat, alt]},
  "rollDeg": 0.0,
  "pitchDeg": 0.0,
  "headingDeg": 0.0,
  "speed": 0.0,
  "missionNumber": "string|null",
  "capabilities": ["Key:Value", ...],
  "comms": ["Key:Value", ...],
  "weapons": ["string", ...],
  "targets": ["string", ...],
  "missions": ["Key:Value", ...],
  "affiliation": 0,
  "aliasAffiliations": {"alias": affiliation_int},
  "dimension": 0,
  "manned": false,
  "lastUpdateTime": "ISO-8601",
  "detectionTime": "ISO-8601",
  "additionalInfo": "string|null",
  "icon": "string|null",
  "genMsgJtn": "string|null"
}
```

**Affiliation enum (integer):** 0=Unknown, 1=Friend, 2=Neutral, 3=Hostile (inferred from live data)

**Dimension enum (integer):** 0=Air, 1=Land, 2=Surface, 3=Subsurface, 4=Space (inferred)

**Key insight: missions[], capabilities[], comms[] use "Key:Value" string arrays.**
Observed keys in missions[]: Callsign, CallShort, AirEntityType, AirEntityTypeDesc,
AirActivity, AirActivityDesc, ModeI, ModeII, ModeIII, ModeIIICodeDesc, J13UpdateTime,
Fuel, FuelFn, MsgTimeZ, TrackNumber, AirSpecificType, AirSpecificTypeDesc, OpCapAircraft,
StoresSummation.

**Design note:** This confirms the "freeform metadata dictionary" approach from the planning meeting. The string arrays ARE the metadata. Our CoPWriter should format extracted data as "Key:Value" strings in the appropriate array.

### Event Manager (http://10.0.0.1:3016)

PAE (Perceive Actionable Entity) input/output pipeline. 16 endpoints, 18 schemas.

**Write endpoints:**
- `POST /paeoutputs` -- Write a single PAE output
- `POST /paeoutputs/bulk` -- Batch write
- `GET /paeoutputs-sse` -- SSE stream of outputs

**Key schemas:** PaeInput, PaeOutput, Event, EventAction, EventTarget, EventDetail

### Mission Manager (http://10.0.0.1:3022)

GBC (Generate Battle COA) output. 28 endpoints, 29 schemas.

**Write endpoints:**
- `POST /missions/coa` -- Create Course of Action
- `PUT /missions/coa/{id}` -- Upsert COA
- `POST /missions/coa-request` -- COA generation request
- `POST /missions/intent` -- Commander's Intent

**Key schemas:** Coa, CoaRequest, CommandersIntent, GbcOutput, BattleCoaHyperedge,
BattleCoaVertex, BattleCoaMetadata, SelectedAction, WindowOfOpportunity

### Effects Manager (http://10.0.0.1:3024)

Weapons and effects database. 30 endpoints, 23 schemas.

**Write endpoints (by category):**
- `/damageeffect`, `/damageeffects/bulk`
- `/cyberattacks`, `/cybereffect`, `/cybereffects/bulk`
- `/electronicattackeffect`, `/electronicattackeffects/bulk`
- `/sensingeffect`, `/sensingeffects/bulk`
- `/commtransporteffect`, `/commtransporteffects/bulk`
- `/effectorconfiguration/{callsign}`, `/effectorplays/bulk`
- `/decisionoperator/bulk`

**Key schemas:** DamageEffect (with Pk tables by target type), CyberAttack,
ElectronicAttackEffect, SensingEffect, EffectorConfiguration, EffectorPlays

### SmartPack Manager (http://10.0.0.1:3028)

Operational planning data. 12 endpoints, 33 schemas.

**CRUD endpoints:**
- `/api/AirOperationsDirective` -- AODs
- `/api/PulseJointTaskOrderTarget` -- JTO targets
- `/api/AirOperationsCenter` -- AOCs

**Key schemas:** DbAirOperationsDirective (with CDE, loadouts, targets),
DbPulseJointTaskOrderTarget, DbBullseye, DbAirfield, DbAreaOfResponsibility,
CommandersIntent, OperationalDesignSection, ExecutionSection

### Other Services

| Service | Port | Endpoints | Notes |
|---------|------|-----------|-------|
| AreaOfInterestManager | :3015 | 8 | AOI CRUD, 10 schemas |
| PointOfInterestManager | :3017 | 6 | POI CRUD, 2 schemas |
| MatchEffectorManager | :3027 | 10 | MEF output, 3 schemas |
| DELTRON | :3060 | 13 | Jeremy's importance scorer, 18 schemas |
| IRC Multiplexer | :3080 | 8 | Channel/message API, 3 schemas |

---

## Historical: BattleEffect Schema V2 (DASH 2 reference)

**Note:** The schemas above (from MASH live network) supersede this historical reference. Kept for comparison with DASH 2 vendor output format.

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
    "url": "ws://10.0.0.1:8097",
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

SERVER_IP = '10.0.0.1'
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
