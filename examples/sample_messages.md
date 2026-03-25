# Sample Chat Messages by Update Type

Real examples from DASH 1-3 events showing the range of messages the pipeline must handle.
Use these for system prompt few-shot examples and testing.

## Entity Identification

```text
[14:40:25] Intel_OPS: @ Hydro_MSO TN 44504 is DDG1
[14:41:09] Intel_OPS: @ Hydro_MSO TN 44506 is 4x J-15s.
[09:15:33] Intel_OPS: @ Hydro_MSO, 43537 is confirmed J-10.
[09:22:11] aoc_intel: ref ctn 44448, ambiguous indications, possible adversary destroyer
```

Expected output:
```json
{
  "update_type": "entity_id",
  "track_number": "44504",
  "entity_type": "DDG",
  "entity_name": "DDG1",
  "affiliation": "HOSTILE",
  "confidence": 0.95
}
```

## Platform Status Change

```text
[15:22:10] ORCA01 (E-2D HAWKEYE): We are gadget bent and have to RTB.
[16:05:44] FLOATER_02_MAYA: Port CIWS is out of ammo
[16:12:33] FLOATER_02_MAYA: starboard is ammo yellow
[16:30:01] REDBULL (KC46) has a broken boom, RTB in Redrock BMA.
```

Expected output:
```json
{
  "update_type": "status_change",
  "callsign": "ORCA01",
  "platform": "E-2D HAWKEYE",
  "status": "RTB",
  "subsystem": "radar",
  "subsystem_status": "INOP",
  "reason": "equipment_failure",
  "confidence": 0.9
}
```

## Weapons Employment

```text
[14:38:15] Floater11_OPS (USS Zumwalt DDG): 24 TLAMS launched, 38 TLAMS remaining
[15:01:22] DUSTER01 (PATRIOT Battery): Splash 6x cruise missiles. Need to reload launchers 1 and 3. ETA 25 min
[09:45:33] michael_murphy_cc: birds away cnt 44743 x5
```

Expected output:
```json
{
  "update_type": "weapons",
  "callsign": "Floater11_OPS",
  "platform": "USS Zumwalt DDG",
  "weapon_type": "TLAM",
  "quantity_launched": 24,
  "quantity_remaining": 38,
  "target_track": null,
  "confidence": 0.95
}
```

## Threat Assessment (TACREP)

```text
[08:15:22] AOC_SIDO: tacrep e10-4, 4th-gen sam active, cigar 316/398, jtn TM677
[09:30:11] AOC_SIDO: TACREP ALPHA 25-1, probable J-15s and J-20s IVO Cigar 272/330
[10:05:44] SPACEOPS: adversary aew confirmed tm651, cigar 277/516, in search mode
```

Expected output:
```json
{
  "update_type": "threat",
  "tacrep_id": "e10-4",
  "threat_type": "SAM",
  "generation": "4th-gen",
  "status": "ACTIVE",
  "location_bearing": 316,
  "location_range": 398,
  "track_number": "TM677",
  "confidence": 0.9
}
```

## Noise (should be filtered/ignored)

```text
[14:31:34] Hydro_SL: .
[14:35:25] Hydro_SL: c
[14:36:18] WF_FYST: c standby.
[09:22:05] VEGAS_ABM2: Blood for the blood god
[10:15:33] AWACS_LNO: Superbowl 58 score 49ers 46, Jets 3
[08:00:01] wf_beep: *****STARTEX DASH 3 GBC Run 10*****
```

Expected output:
```json
{
  "update_type": "none",
  "reason": "acknowledgment"
}
```

## Fire Mission (Structured — could be regex-parsed)

```text
[11:23:45] 3MARDIV_FIRES: FIRE MISSION! 1. Spotter: Labradoodle 69 Firing Unit: BAYONET 2. MLRS, 17QNE9779269855 Type: DPICM Time: Immediate 3. GTL 245 4. MAXORD 15.5k 5. Ready, Time of flight 42 seconds
```

Expected output:
```json
{
  "update_type": "fire_mission",
  "spotter": "Labradoodle 69",
  "firing_unit": "BAYONET",
  "weapon_system": "MLRS",
  "target_mgrs": "17QNE9779269855",
  "munition_type": "DPICM",
  "timing": "Immediate",
  "gtl": 245,
  "maxord_ft": 15500,
  "tof_seconds": 42,
  "confidence": 0.95
}
```

## Voice STT (Noisy — lower confidence)

```text
[14:35:09] google-speech-to-text: Hydro wizard flight checking in as fragged
[14:38:24] google-speech-to-text: Hydro single groups are 31396 24000 and track North hostile forecast
[14:36:47] google-speech-to-text: 4 3 hydrocodones
```

Note: "hydrocodones" = "Hydro copies" (ASR garbling). STT messages should be processed with lower base confidence and additional validation against known callsigns/track numbers.
