"""OpenAI-compatible backend: works with Ollama, vLLM, LiteLLM, cloud APIs.

This single class covers every model we'd want to use. The only
configuration difference is the base_url and model name.

Uses `instructor` for structured output with Pydantic validation.
"""

from __future__ import annotations

import asyncio

import instructor
from openai import AsyncOpenAI
from pydantic import BaseModel

from chat_to_cop.metrics import metrics


class RetryableError(Exception):
    """Backend error that may succeed on retry (timeout, 429, 503)."""


class PermanentError(Exception):
    """Backend error that will not succeed on retry (400, schema)."""


class OpenAICompatibleBackend:
    """LLM backend using any OpenAI-compatible API endpoint.

    Works with Ollama, vLLM, LiteLLM, Ask Sage, cloud APIs — anything
    that serves the /v1/chat/completions endpoint.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434/v1",
        model: str = "qwen3:30b-a3b",
        api_key: str = "not-needed",
        timeout: float = 10.0,
        max_retries: int = 2,
        num_ctx: int = 8192,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.num_ctx = num_ctx

        self._client = instructor.from_openai(
            AsyncOpenAI(
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
            ),
            mode=instructor.Mode.JSON,
        )
        self._max_retries = max_retries

    async def extract(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
    ) -> BaseModel:
        """Extract structured data from messages according to schema.

        Args:
            messages: OpenAI-format message list (role + content dicts).
            schema: Pydantic model class defining the expected output.

        Returns:
            An instance of `schema` populated with extracted data.

        Raises:
            RetryableError: On timeout, rate limit, or server error.
            PermanentError: On bad request or schema validation failure.
        """
        labels = {"backend": "openai_compat", "model": self.model}
        metrics.inc("backend_calls_total", labels=labels)

        try:
            async with metrics.async_timer("extraction_latency_seconds", labels=labels):
                kwargs = dict(
                    model=self.model,
                    messages=messages,
                    response_model=schema,
                    max_retries=self._max_retries,
                )
                # Only pass num_ctx for Ollama (other providers reject unknown options)
                if self.num_ctx and ("11434" in self.base_url or "ollama" in self.base_url.lower()):
                    kwargs["extra_body"] = {"options": {"num_ctx": self.num_ctx}}
                result = await self._client.chat.completions.create(**kwargs)
            metrics.inc("backend_successes_total", labels=labels)
            return result

        except asyncio.TimeoutError as e:
            metrics.inc("backend_failures_total", labels={**labels, "reason": "timeout"})
            raise RetryableError(f"Timeout after {self.timeout}s") from e
        except Exception as e:
            err_str = str(e).lower()
            if any(code in err_str for code in ("429", "rate limit", "503", "502", "timeout")):
                metrics.inc("backend_failures_total", labels={**labels, "reason": "retryable"})
                raise RetryableError(str(e)) from e
            metrics.inc("backend_failures_total", labels={**labels, "reason": "permanent"})
            raise PermanentError(str(e)) from e

    def __repr__(self) -> str:
        return f"OpenAICompatibleBackend(url={self.base_url!r}, model={self.model!r})"


def build_system_prompt(
    glossary: str = "",
    world_state_summary: str = "",
    speaker_context: str = "",
) -> str:
    """Build the system prompt for world-state extraction.

    Args:
        glossary: Military abbreviations and exercise-specific terms.
        world_state_summary: Current believed battlespace state.
        speaker_context: Known speaker profiles for this channel.

    Returns:
        Complete system prompt string.
    """
    parts = [
        "You are a military chat message interpreter for a Common Operating Picture (CoP) database.",
        "Extract world-state changes from the LATEST message ONLY and return structured JSON.",
        "",
        "RULES:",
        "- Only extract from the LAST message in the conversation. Prior messages are context only.",
        "- Do NOT invent coordinates, positions, or data not explicitly stated in the message.",
        "- If a message is an ack (c, copy, ., NSTR) or banter/jokes, set update_type to 'none'.",
        "- STARTEX/ENDEX are exercise control, not world-state — set update_type to 'none'.",
        "- Set extraction_method to 'llm' always.",
        "- Extract ALL entities mentioned. If multiple aircraft/ships are listed, create an entity for EACH.",
        "- For complex multi-section messages (SITREP, C2 HANDOVER), focus on the key state changes.",
        "- Keep entities simple. Only include fields you are confident about.",
        "- STT messages may have <speaker> tags — the real speaker is inside the tag.",
        "- STT may garble numbers: 'cigar 3 1 5 3 80' means cigar 315/380.",
        "- For corrections ('disregard last', 'correction:'), update the entity to the corrected state.",
        "- Radio checks are NOT operational — set update_type to 'none'.",
        "- Be concise in reasoning. Focus on what changed in the battlespace.",
        "",
        "EXAMPLES:",
        "",
        'Message: "Hydro_Tank: RR15 F+40, RL36 F+50"',
        "Output: update_type=fuel, confidence=0.9",
        "  entities: [{callsign:RR15, fuel_state:F+40}, {callsign:RL36, fuel_state:F+50}]",
        "",
        'Message: "HYDRO_SL: ZEUS 12,13,14 shot down by TTG"',
        "Output: update_type=status_change, confidence=0.95",
        "  entities: [{callsign:ZEUS12, status:DESTROYED},",
        "    {callsign:ZEUS13, status:DESTROYED}, {callsign:ZEUS14, status:DESTROYED}]",
        "",
        'Message: "AOC_SIDO: tacrep, 4th-gen sam active, cigar 316/398, jtn TM677"',
        "Output: update_type=threat, confidence=0.9",
        "  entities: [{track_number:TM677, affiliation:HOSTILE,",
        "    bearing:316, range_nm:398, subsystem_status:4th-gen SAM active}]",
        "",
        'Message: "VEGAS_ABM2: ."',
        "Output: update_type=none, confidence=0.0, entities=[]",
        "",
        'Message: "ORCA01 gadget bent, RTB"',
        "Output: update_type=status_change, confidence=0.9",
        "  entities: [{callsign:ORCA01, operational_status:RTB,",
        "    subsystem_status:radar inop}]",
        "",
        'Message: "SITREP / AIR: ZEUS12,13 shot down / TANKER: REDBULL22 canx"',
        "Output: update_type=sitrep, confidence=0.9",
        "  entities: [{callsign:ZEUS12, operational_status:DESTROYED},",
        "    {callsign:ZEUS13, operational_status:DESTROYED},",
        "    {callsign:REDBULL22, operational_status:INOP}]",
        "",
        'Message: "vegas crew good, lets get this bread"',
        "Output: update_type=none, confidence=0.0, entities=[]",
        "",
        'Message: "***STARTEX DASH 3 GBC Run 10***"',
        "Output: update_type=none, confidence=0.0, entities=[]",
        "",
        'Message: "Disregard last, TN 44504 is NOT DDG1, reassessing"',
        "Output: update_type=entity_id, confidence=0.8",
        "  entities: [{track_number:44504, affiliation:UNKNOWN}]",
        "  reasoning: Correction — previous ID retracted",
        "",
        'Message: "<WF2> Radio check Radio check C2 cord."',
        "Output: update_type=none, confidence=0.0, entities=[]",
        "  reasoning: Radio check, not operational content",
        "",
        'Message: "JPRC_COORD: MISREP / ZEUS14 pilot ejected vic N22.15 W078.30, initiating CSAR"',
        "Output: update_type=csar, confidence=0.95",
        "  entities: [{callsign:ZEUS14, operational_status:PILOT_EJECTED,",
        "    latitude:22.15, longitude:-78.30, metadata:{event:CSAR_initiated, report_type:MISREP}}]",
        "",
        'Message: "RESCORT01: survivor located, SANDY21 on station, extraction in progress"',
        "Output: update_type=csar, confidence=0.9",
        "  entities: [{callsign:RESCORT01, metadata:{role:CSAR_escort}},",
        "    {callsign:SANDY21, operational_status:ON_STATION, metadata:{mission:CSAR_extraction}}]",
        "",
        'Message: "JPRC: ZEUS14 pilot recovered, DUSTOFF inbound to FARP DELTA"',
        "Output: update_type=csar, confidence=0.95",
        "  entities: [{callsign:ZEUS14, operational_status:PILOT_RECOVERED,",
        "    metadata:{recovery_status:recovered, destination:FARP DELTA}}]",
        "",
        'Message: "FIRES_COORD: fire mission TGT AQ1234, 2x JDAM, TOT 1430Z"',
        "Output: update_type=fire_mission, confidence=0.9",
        "  entities: [{track_number:AQ1234, metadata:{weapon:JDAM, qty:2, TOT:1430Z}}]",
        "",
        'Message: "3MARDIV_FIRES: CAS request, TIC at grid 17QNE9779269855, troops in contact"',
        "Output: update_type=fire_mission, confidence=0.9",
        "  entities: [{metadata:{mission_type:CAS, grid:17QNE9779269855, situation:troops_in_contact}}]",
        "",
        'Message: "FIRES_BDA: BDA TGT AQ1234, 2x hits, target destroyed"',
        "Output: update_type=fire_mission, confidence=0.9",
        "  entities: [{track_number:AQ1234, operational_status:DESTROYED,",
        "    metadata:{BDA:2x_hits, assessment:target_destroyed}}]",
        "",
        'Message: "EW_COORD: GPS jamming detected vicinity waypoint ALPHA, affecting blue CAS stack"',
        "Output: update_type=cyber_ew, confidence=0.85",
        "  entities: [{metadata:{threat_type:GPS_jamming, location:waypoint ALPHA,",
        "    impact:blue CAS stack affected}, capability_impact:act}]",
        "",
        'Message: "SIGINT01: enemy comms intercept, SA-20 battery activating, grid 38TLM1234567890"',
        "Output: update_type=cyber_ew, confidence=0.85",
        "  entities: [{platform_type:SA-20, affiliation:HOSTILE,",
        "    operational_status:ACTIVE, metadata:{grid:38TLM1234567890,",
        "    source:SIGINT, event:battery_activating}, capability_impact:sense}]",
    ]

    if glossary:
        parts.append(f"\nGLOSSARY:\n{glossary}")

    if world_state_summary:
        parts.append(f"\nCURRENT WORLD STATE:\n{world_state_summary}")

    if speaker_context:
        parts.append(f"\nKNOWN SPEAKERS:\n{speaker_context}")

    return "\n\n".join(parts)


# Default glossary from DASH event analysis + equifinality repo enrichment
DEFAULT_GLOSSARY = """## Brevity / Tactical Terms
- "gadget bent" = radar failure
- "buzzer on" = EW jamming active
- "splash" = target destroyed
- "FOX 1/2/3" = missile launch (semi-active/IR/active radar)
- "RTB" = return to base
- "angels XX" = altitude in thousands of feet
- "bullseye/cigar XXX/YYY" = bearing/range from reference point
- "F+XX" = fuel above frag in thousands of lbs
- "playtime XX Mike" = XX minutes of fuel remaining
- "c" or "copy" = acknowledgment (no state change)
- "NSTR" = nothing significant to report
- "TTG" = terminal threat group (enemy air defense)
- "BDA" = battle damage assessment
- "SITREP" = situation report
- "TACREP" = tactical report
- "INTSUM" = intelligence summary
- "BMA" = battle management area
- "AR" = aerial refueling
- "on boom" = currently refueling
- "SEAD" = suppression of enemy air defenses
- "DEAD" = destruction of enemy air defenses
- "ISR" = intelligence, surveillance, reconnaissance
- "BVR" = beyond visual range
- "WVR" = within visual range
- "COA" or "CAO" = course of action (DASH uses both interchangeably)
- "GBC" = generate battle course of action
- "MEF" = match effectors to targets
- "SCL" = station configuration loadout (weapon arrangement on aircraft)
- "F2T2EA" = find, fix, track, target, engage, assess (kill chain)
- "IFTU" = in-flight target update

## Personnel Recovery
- "CSAR" = combat search and rescue
- "MISREP" = mission report (personnel recovery)
- "JPRC" = Joint Personnel Recovery Center
- "SANDY" = A-10/CSAR on-scene commander callsign
- "RESCORT" = rescue escort
- "DUSTOFF" = medical evacuation helicopter

## Fires / CAS
- "TOT" = time on target
- "CAS" = close air support
- "TIC" = troops in contact
- "FARP" = forward arming and refueling point

## EW / Cyber
- "EW" = electronic warfare
- "SIGINT" = signals intelligence
- "COMINT" = communications intelligence
- "ELINT" = electronic intelligence (radar, weapons systems)
- "DRFM" = digital radio frequency memory (radar jammer)
- "Dazzler" = laser-based sensor disruption system

## Blue Platform Types
- "F-15E" = Strike Eagle, dual-role fighter-bomber
- "F-16C" = Fighting Falcon, multirole fighter
- "F-22" = Raptor, 5th-gen air superiority fighter (stealth, supercruise)
- "F-35A" = Lightning II, 5th-gen stealth multirole (USAF variant)
- "F/A-18E" or "F/A-18F" = Super Hornet, carrier-based multirole fighter
- "EA-18G" = Growler, electronic attack variant of Super Hornet (SEAD/DEAD)
- "EA-37B" = Compass Call, electronic attack aircraft
- "E-2D" = Advanced Hawkeye, carrier-based AEW (airborne early warning)
- "E-3" = AWACS, airborne warning and control
- "E-7" = Wedgetail, AEW&C platform (replacing E-3)
- "B-1B" = Lancer, supersonic strategic bomber
- "B-2" = Spirit, stealth strategic bomber
- "B-52" = Stratofortress, long-range strategic bomber
- "KC-135" = Stratotanker, aerial refueling tanker
- "KC-46" = Pegasus, next-gen aerial refueling tanker
- "MQ-9" = Reaper, armed ISR UAV
- "RQ-4" = Global Hawk, high-altitude ISR UAV
- "RC-135" = Rivet Joint, SIGINT reconnaissance aircraft
- "U-2" = Dragon Lady, high-altitude reconnaissance aircraft
- "P-8" = Poseidon, maritime patrol / ASW aircraft
- "CVN" = nuclear aircraft carrier (Nimitz/Ford-class)
- "DDG" = guided missile destroyer (Arleigh Burke-class, Aegis)
- "CG" = guided missile cruiser (Ticonderoga-class, Aegis)
- "LCS" = littoral combat ship
- "LHD" = amphibious assault ship (can operate F-35B)
- "SSN" = nuclear attack submarine
- "SSGN" = guided missile submarine (154 Tomahawks)
- "SOF" = special operations forces

## Weapon Systems & Aliases
- "AIM-9" or "Sidewinder" = short-range IR air-to-air missile
- "AIM-120" or "AMRAAM" = medium-range radar-guided air-to-air missile
- "AGM-84" or "Harpoon" or "SLAM" = air-launched anti-ship/land attack cruise missile
- "AGM-88" or "HARM" = high-speed anti-radiation missile (SEAD)
- "AGM-114" or "Hellfire" = air-to-ground missile (helicopters, UAVs)
- "AGM-158" or "JASSM" = stealthy long-range air-to-surface standoff missile
- "AGM-158C" or "LRASM" = long-range anti-ship missile
- "JDAM" = Joint Direct Attack Munition (GPS-guided bomb: GBU-31/32/38)
- "GBU-39" or "SDB" = small diameter bomb (250 lb GPS-guided)
- "GBU-53" or "Stormbreaker" or "SDB II" = multi-mode guided bomb (moving targets)
- "TLAM" = Tomahawk land attack missile (ship/sub-launched cruise missile)
- "MST" = Maritime Strike Tomahawk (anti-ship Tomahawk variant)
- "SM6" or "SM-6" = Standard Missile 6 (multi-role: air defense, BMD, anti-surface)
- "SM3" or "SM-3" = Standard Missile 3 (ballistic missile defense interceptor)
- "PAC-3" or "Patriot" = ground-based air and missile defense
- "THAAD" = terminal high altitude area defense (ground-based BMD)
- "ATACMS" = Army Tactical Missile System (ground-launched)
- "ADM-160" or "MALD" = miniature air-launched decoy
- "APKWS" or "AGR20" = advanced precision kill weapon system (laser-guided rocket)

## C2 / Sensor Systems
- "TAOC" = tactical air operations center (USMC air defense C2)
- "CRC" = control and reporting center (air surveillance/battle management)
- "CRE" = control and reporting element (mobile CRC)
- "AEGIS" = ship-based combat system (SPY-1 radar + SM missiles)
- "Link 16" = secure NATO tactical datalink

## Red Force Identifiers (DASH exercise)
- "J-20" = Chinese 5th-gen stealth fighter
- "HQ-9" or "SA-21" = Chinese/Russian long-range SAM (surface-to-air missile)
- "SA-20" = Russian S-300 long-range SAM system
- "4th-gen SAM" = older-generation surface-to-air missile system"""
