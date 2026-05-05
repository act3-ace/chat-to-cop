# Chat Data Analysis: World-State Update Taxonomy

Analysis of downloaded DASH 1-3 IRC chat logs to characterize what information flows through battle manager communications and needs to be pushed to the CoP database.

**Sources analyzed:** DASH 1 (Apr 2025, 26 files), DASH 2 (Jul 2025, 46 files), DASH 3 GBC (Sep 2025, 68 files)

## Summary

- **~55-65% of messages contain actionable world-state information**
- **~25-30% are operational noise** (acknowledgments: "c", "copy", ".", radio checks)
- **~3-5% are non-operational chatter** (jokes, wrong room, Superbowl scores)
- **~5-8% are exercise control** (STARTEX, ENDEX, calibration)
- **Message rate:** 2-7 msg/min typical, spikes to 8-12/min during engagements

## Update Categories

### 1. Entity Identification (10-15% of ops messages)

Messages that resolve an unknown track to a specific entity type. **LLM difficulty: Trivial.**

```
Intel_OPS: @ Hydro_MSO TN 44504 is DDG1
Intel_OPS: @ Hydro_MSO, 43537 is confirmed J-10.
Intel_OPS: @ Hydro_MSO TN 44506 is 4x J-15s.
Intel_OPS: @ Hydro_MSO, 43570 is commercial aircraft.
```

**Maps to:** `track_number`, `entity_type`, `classification` (HOSTILE/NEUTRAL/COMMERCIAL), `count`, `platform_type`

### 2. Platform Status Changes (8-12%)

Platform availability, damage, equipment failures, RTB. **LLM difficulty: Moderate** (brevity codes like "gadget bent" = radar failure).

```
ORCA01 (E-2D HAWKEYE): We are gadget bent and have to RTB.
REDBULL (KC46) has a broken boom, RTB in Redrock BMA.
FLOATER_02_MAYA: Port CIWS is out of ammo
FLOATER_02_MAYA: BRIDGE HAS BEEN HIT, CURRENTLY ONLY HAVE CIC OPERATIONAL
BOOMBOX (EA37) is buzzer on in Lane Luxor
```

**Maps to:** `callsign`, `operational_status` (OPERATIONAL/DEGRADED/INOP/RTB), `subsystem_status`, `reason`, `ammo_state`

### 3. Weapons Employment & Engagement (5-8%)

Weapons launches, kill results, ammunition expenditure. **LLM difficulty: Moderate.**

```
Floater11_OPS (USS Zumwalt DDG): 24 TLAMS launched, 38 TLAMS remaining
Floater02_OPS (USS Danzig DDG): 4xSM6 launched for track #L0056
DUSTER01 (PATRIOT Battery): Splash 6x cruise missiles. Need to reload launchers 1 and 3. ETA 25 min
michael_murphy_cc: birds away cnt 44743 x5
tiger 21 Splash leader bullseye 31077  [STT]
```

**Maps to:** `shooter_callsign`, `weapon_type`, `quantity_launched`, `quantity_remaining`, `target_track`, `result` (SPLASH/MISS), `reload_eta`

### 4. Location & Position Reports (10-15%)

Position updates using bullseye/cigar notation, lat/lon, or MGRS. **LLM difficulty: Moderate-Hard** (three coordinate systems).

```
JSTARS_01: TBM Track # L2079 inbound, IVO N21.774294, W72.279964, CEP .5km, ETA +30 min
2x J-16s observed on EO/IR Imagery IVO 25.045310, -77.464458
WOMBAT: TACREP JULIET 23-1, SKUNK, CIGAR 324/275, TRACK TM565, SPD 10Kt/HDG 095
hydro_Surv: this will be the hydro location N 24 03.6134' W 074 31.4900'
3MARDIV_FIRES: FIRE MISSION! ... 17QNE9779269855 [MGRS]
```

**Maps to:** `track_number`, `latitude`, `longitude`, `bearing`, `range`, `altitude`, `speed`, `heading`

**Note:** Three coordinate systems in use:
- Lat/lon decimal or DMS (easy to parse)
- Bullseye/Cigar bearing/range (requires reference point lookup)
- MGRS grid (in fire missions)

### 5. Threat Assessments & Intelligence (10-15%)

Hostile force characterization and disposition. **LLM difficulty: Moderate.**

```
AOC_SIDO: tacrep e10-4, 4th-gen sam active, cigar 316/398, jtn TM677
AOC_SIDO: TACREP ALPHA 25-1, probable J-15s and J-20s IVO Cigar 272/330
SPACEOPS: adversary aew confirmed tm651, cigar 277/516, in search mode
CYBEROPS: HEADS UP down ech traffic for tbm launch orders in the florida network enclave
aoc_intel: ref ctn 44448, ambiguous, possible adversary destroyer, could be commercial drilling ship
```

**Maps to:** `threat_id/jtn`, `threat_type`, `generation` (4th/5th), `status` (ACTIVE/DESTROYED), `location_cigar`, `confidence`, `source`

### 6. Mission Tasking & Re-tasking (15-20%)

Orders, effector matching, BattleCOA directives. **LLM difficulty: Moderate.**

```
AOC_Ops: @Hydro_Strike Destroy the 2x H-6s at N33:22:14 W087:31:20. UID DA011
HYDRO_Strike: ref DA011: Rank 1 FLOATER11. Rank 2 HS, HA, YA.
VEGAS_SL: @vegas_abm1 Generate BattleCOA 6-1 for: SUNNY21 STRIKE TN TM636
VEGAS_SL: HYDRO IMMEDIATE: BattleCOA 15-17 for: SINATRA DIRECTS HADES31 STRIKE ENEMY CARRIER TN TM707
hydro_SL: ref trks 44469, THOR01 COMMIT to ID
```

**Maps to:** `task_uid`, `task_type` (STRIKE/DEFEND/LOCATE/CSAR), `assigned_asset`, `target_track`, `priority`, `timing`

### 7. Fuel & Logistics (5-7%)

Tanker states, aerial refueling coordination. **LLM difficulty: Hard** (heavily abbreviated).

```
Hydro_Tank: RR15 F+40, RL36 F+50
Hydro_Tank: current F+'s: RR15 F+0, RL36 F+0, MR26 F+40, BG01 F+25
Hydro_Tank: New AR plan: MN01 -> RT55 20k V-30, MN03 -> BG45 20k V-25
HYDRO_Strike: HADES flt on boom
tiger 21 play time 15 Mike  [STT — 15 minutes fuel remaining]
```

**Maps to:** `tanker_callsign`, `fuel_available_lbs`, `receiver_callsign`, `ar_track`, `playtime_remaining_min`

**Note:** "F+40" = 40,000 lbs above frag. "MN01 -> RT55 20k V-30" = callsign MN01 to tanker RT55 for 20k, velocity minus 30 offset.

### 8. C2 Handover & Airspace (3-5%)

Control transfers, BMA management, airbase status. **LLM difficulty: Moderate.**

```
AOC_Ops: @TOC01_OPS You now have SADC duties for Lanes 2 and 3
vegas_sl: @hydro_sl, do y'all have the capacity to take over crusher's BMA?
AOC_SODO: all airbases on Jamaica are inop, no recoveries ufn.
CRUSHER_SL: @Orca can we move SHARK31, ZEUS31 to 267/73 for 15-2 tasking?
```

**Maps to:** `controlling_entity`, `bma_name`, `lanes[]`, `handover_from/to`, `airbase_status`

### 9. Personnel Recovery / CSAR (2-3%)

Bailout reports, survivor locations. **LLM difficulty: Moderate.** Dedicated `#jprc` channel in DASH 3.

```
Hydro_SL: @JPRC, FG31 bailout, searching for chutes, OSC is FG31
Hydro_SL: @JPRC, FG31 records 272/43 2 good chutes
WOMBAT01: HY22 bailout location 318/217
AOC_SODO: HP12/14 shot down by TTG; TR13 shot down by TTG
```

**Maps to:** `downed_callsign`, `bailout_location`, `chute_count`, `osc_callsign`, `csar_asset`

### 10. Fire Missions (1-2%, DASH 3 only)

Structured artillery/missile fire requests. **LLM difficulty: Trivial** (numbered template).

```
3MARDIV_FIRES: FIRE MISSION!
  1. Spotter: Labradoodle 69 Firing Unit: BAYONET
  2. MLRS, 17QNE9779269855 Type: DPICM Time: Immediate
  3. GTL 245
  4. MAXORD 15.5k
  5. Ready, Time of flight 42 seconds
```

**Maps to:** `spotter`, `firing_unit`, `weapon_system`, `target_mgrs`, `munition_type`, `timing`, `tof_seconds`

### 11. Cyber & EW Events (2-4%)

Electronic warfare and cyber reports. **LLM difficulty: Moderate.**

```
CyberCOM_OPS: Ref network A, event: Lateral movement of compromised account to AOC file server
CYBEROPS: HEADS UP down ech traffic for tbm launch orders in florida enclave
CyberCOM_OPS: Malware forensics indicate RAT "NotaRat" used on ICS/OT targets
```

**Maps to:** `network_id`, `event_type`, `indicator`, `affected_system`

### 12. SITREP / Handover Reports (1-2%)

Structured status reports at shift change. **LLM difficulty: Moderate.** Information-dense (5-10 state changes per message).

```
HYDRO_SL: SITREP / AIR: ZEUS 12,13,14 shot down by TTG; YAMA11 shot down /
  LRSAMs: / NAVAL SAMs: / OTHER: //
HYDRO_SL: C2 HANDOVER / TARGETS UNSERVICED: / DT: /
  FALLOUT: ORCA01 early RTB fuel press / TANKER CHANGES: REDBULL22 msn canx //
```

**Maps to:** `air_losses[]`, `targets_unserviced[]`, `fallouts[]`, `tanker_changes[]`

### 13. Environmental & Admin (1-2%)

Weather, base conditions. **LLM difficulty: Trivial.**

```
WOC_OPS: Lightning within 5, all aircraft on a ground stop.
AOC_Ops: Smoke plume from eruption affecting AR Track NUGGET, unusable next 48 hours
```

## STT vs Typed Chat

STT channels (`#stt_*`) carry **unique data not in typed chat** — air-to-air radio brevity, picture calls, FOX/Splash calls. But parsability is significantly worse:

- **Number garbling:** "31587" could be bullseye 315/87 or 31/587
- **Callsign confusion:** "Hydra" vs "Hydro", "hydrocodones" for "Hydro copies"
- **ASR hallucinations:** "subtitles by the amaraorg community" (repeated 13x)
- **Multiple bot layers:** google-speech-to-text, SolipsysAI_bot, asr_bot — producing duplicate/conflicting transcriptions

**Assessment:** STT is ~60% noise. Useful for air picture updates but needs heavy denoising. Consider as a lower-priority data source compared to typed chat.

## White Cell vs Battle Manager Patterns

**White cell (WF_*, AOC_SODO, AOC_SADO, MOC_OPS, etc.):**
- Inject scenario stimuli in a structured format: `SENDER (Platform): @RECIPIENT (Role) [content] in Lane [name]`
- Most information-dense messages — contain the world-state changes
- Role-play multiple entities

**Battle managers (Hydro_SL, HYDRO_Strike, Hydro_Tank, etc.):**
- More natural language, more abbreviation, more typos
- Produce tasking decisions, effector matching, fuel plans
- Reference white cell injects by UID/track number

## Structural Differences Across Events

| Feature | DASH 1 (Apr 2025) | DASH 2 (Jul 2025) | DASH 3 (Sep 2025) |
|---------|-------------------|-------------------|-------------------|
| Chat format | Single log file | Single room | Per-channel logs + combined |
| Track ID | "Intel, color TN XXXXX" | Same + UID system | TACREP format w/ JTN |
| Tasking | Informal NL | UID DA### structured | BattleCOA 5-line format |
| Channels | 1 room | 1 room (MIRC) | 10+ channels |
| Coordinates | Lat/lon + bullseye | Lat/lon + bullseye | Cigar + MGRS + lat/lon |
