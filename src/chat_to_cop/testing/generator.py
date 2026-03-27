"""Synthetic military chat data generator for testing.

Produces realistic wargame-style IRC messages with known ground truth labels.
Each generated message is paired with the expected CoPUpdate for precision/recall measurement.

Usage:
    from chat_to_cop.testing.generator import generate_messages
    messages, ground_truth = generate_messages(count=50, noise_ratio=0.3)
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from chat_to_cop.models.cop_update import CoPUpdate, EntityUpdate, UpdateType
from chat_to_cop.models.messages import IRCMessage

# --- Entity pools (from DASH 3 data) ---

CALLSIGNS = [
    "ZEUS11",
    "ZEUS12",
    "ZEUS13",
    "ZEUS14",
    "HADES11",
    "HADES12",
    "HADES31",
    "ORCA01",
    "ORCA02",
    "THOR01",
    "THOR13",
    "SHARK14",
    "SHARK31",
    "HARPY12",
    "HARPY14",
    "GIZMO11",
    "MERLIN11",
    "REDBULL22",
    "ROCKSTAR31",
    "SINATRA",
    "SUNNY21",
]

TANKERS = ["RR15", "RL36", "MN01", "BG01", "RT55", "MR26"]

TRACK_NUMBERS = [f"TM{n}" for n in range(636, 720)] + [f"{n}" for n in range(44400, 44600)]

CHANNELS = ["#c2_coord", "#isr_reports", "#fires", "#jprc"]

SPEAKERS_WC = ["WF_Clark", "wf_beep", "WF_FYST", "AOC_SIDO", "AOC_Ops", "AOC_SODO"]
SPEAKERS_BM = ["Hydro_SL", "HYDRO_Strike", "Hydro_Tank", "VEGAS_SL", "CRUSHER_SL", "TAIPAN_SL"]

NOISE_MESSAGES = [".", "..", "...", "c", "copy", "word", "NSTR", "test", "roger", "affirm", "wilco"]

BANTER = [
    "lets get this bread",
    "for the emperor",
    "good morning everyone",
    "happy friday",
    "who wants coffee",
    "GM crew",
]

PLATFORMS = ["DDG1", "DDG2", "J-10", "J-15", "J-16", "J-20", "H-6", "SU-27"]


def _rand_speaker(white_cell: bool = False) -> str:
    return random.choice(SPEAKERS_WC if white_cell else SPEAKERS_BM)


def _rand_channel() -> str:
    return random.choice(CHANNELS)


def _rand_track() -> str:
    return random.choice(TRACK_NUMBERS)


def _rand_callsign() -> str:
    return random.choice(CALLSIGNS)


def _rand_tanker() -> str:
    return random.choice(TANKERS)


# --- Message generators (each returns message text + expected CoPUpdate) ---


def _gen_entity_id() -> tuple[str, str, CoPUpdate]:
    """Generate an entity identification message."""
    tn = _rand_track()
    platform = random.choice(PLATFORMS)
    speaker = _rand_speaker(white_cell=True)
    text = f"@ Hydro_MSO TN {tn} is {platform}"
    update = CoPUpdate(
        update_type=UpdateType.ENTITY_ID,
        confidence=0.9,
        extraction_method="ground_truth",
        entities=[EntityUpdate(track_number=tn, platform_type=platform, affiliation="HOSTILE")],
        source_channel=_rand_channel(),
        source_speaker=speaker,
        source_message=text,
        timestamp=datetime.now(tz=timezone.utc),
    )
    return text, speaker, update


def _gen_status_change() -> tuple[str, str, CoPUpdate]:
    """Generate a platform status change."""
    cs = _rand_callsign()
    statuses = [
        ("gadget bent, RTB", "RTB", "radar inop"),
        ("RTB fuel press issue", "RTB", None),
        ("shot down by TTG", "DESTROYED", None),
        ("buzzer on", "OPERATIONAL", "EW jamming active"),
        ("winchester", "DEGRADED", "out of ammo"),
    ]
    status_text, status_val, subsystem = random.choice(statuses)
    speaker = _rand_speaker()
    text = f"{cs} {status_text}"
    update = CoPUpdate(
        update_type=UpdateType.STATUS_CHANGE,
        confidence=0.9,
        extraction_method="ground_truth",
        entities=[EntityUpdate(callsign=cs, operational_status=status_val, subsystem_status=subsystem)],
        source_channel=_rand_channel(),
        source_speaker=speaker,
        source_message=text,
        timestamp=datetime.now(tz=timezone.utc),
    )
    return text, speaker, update


def _gen_fuel() -> tuple[str, str, CoPUpdate]:
    """Generate a fuel state update."""
    t1 = _rand_tanker()
    t2 = _rand_tanker()
    while t2 == t1:
        t2 = _rand_tanker()
    f1 = random.randint(0, 60)
    f2 = random.randint(0, 60)
    speaker = "Hydro_Tank"
    text = f"{t1} F+{f1}, {t2} F+{f2}"
    update = CoPUpdate(
        update_type=UpdateType.FUEL,
        confidence=0.9,
        extraction_method="ground_truth",
        entities=[
            EntityUpdate(callsign=t1, fuel_state=f"F+{f1}"),
            EntityUpdate(callsign=t2, fuel_state=f"F+{f2}"),
        ],
        source_channel="#c2_coord",
        source_speaker=speaker,
        source_message=text,
        timestamp=datetime.now(tz=timezone.utc),
    )
    return text, speaker, update


def _gen_weapons() -> tuple[str, str, CoPUpdate]:
    """Generate a weapons employment message."""
    cs = _rand_callsign()
    weapon = random.choice(["SM6", "TLAM", "AMRAAM"])
    qty = random.randint(1, 6)
    remaining = random.randint(0, 20)
    speaker = _rand_speaker()
    text = f"{cs}: {qty}x{weapon} launched, {remaining} remaining"
    update = CoPUpdate(
        update_type=UpdateType.WEAPONS,
        confidence=0.9,
        extraction_method="ground_truth",
        entities=[
            EntityUpdate(callsign=cs, weapon_type=weapon, weapon_qty_launched=qty, weapon_qty_remaining=remaining)
        ],
        source_channel="#fires",
        source_speaker=speaker,
        source_message=text,
        timestamp=datetime.now(tz=timezone.utc),
    )
    return text, speaker, update


def _gen_threat() -> tuple[str, str, CoPUpdate]:
    """Generate a threat assessment (TACREP)."""
    tn = _rand_track()
    bearing = random.randint(0, 359)
    range_nm = random.randint(50, 500)
    speaker = "AOC_SIDO"
    threat = random.choice(["4th-gen SAM", "5th-gen fighter", "DDG", "SSN"])
    text = f"tacrep, {threat} active, cigar {bearing}/{range_nm}, jtn {tn}"
    update = CoPUpdate(
        update_type=UpdateType.THREAT,
        confidence=0.9,
        extraction_method="ground_truth",
        entities=[
            EntityUpdate(
                track_number=tn,
                affiliation="HOSTILE",
                bearing=float(bearing),
                range_nm=float(range_nm),
                subsystem_status=f"{threat} active",
            )
        ],
        source_channel="#isr_reports",
        source_speaker=speaker,
        source_message=text,
        timestamp=datetime.now(tz=timezone.utc),
    )
    return text, speaker, update


def _gen_csar() -> tuple[str, str, CoPUpdate]:
    """Generate a CSAR/bailout message."""
    cs = _rand_callsign()
    bearing = random.randint(0, 359)
    range_nm = random.randint(10, 100)
    chutes = random.randint(1, 2)
    speaker = _rand_speaker()
    text = f"@JPRC, {cs} bailout, {chutes} good chutes, {bearing}/{range_nm}"
    update = CoPUpdate(
        update_type=UpdateType.CSAR,
        confidence=0.9,
        extraction_method="ground_truth",
        entities=[EntityUpdate(callsign=cs, operational_status="DESTROYED")],
        source_channel="#jprc",
        source_speaker=speaker,
        source_message=text,
        timestamp=datetime.now(tz=timezone.utc),
    )
    return text, speaker, update


def _gen_tasking() -> tuple[str, str, CoPUpdate]:
    """Generate a tasking/BattleCOA message."""
    cs = _rand_callsign()
    tn = _rand_track()
    speaker = "VEGAS_SL"
    text = f"@vegas_abm1 Generate BattleCOA for: {cs} STRIKE TN {tn}"
    update = CoPUpdate(
        update_type=UpdateType.TASKING,
        confidence=0.9,
        extraction_method="ground_truth",
        entities=[EntityUpdate(callsign=cs, track_number=tn)],
        source_channel="#c2_coord",
        source_speaker=speaker,
        source_message=text,
        timestamp=datetime.now(tz=timezone.utc),
    )
    return text, speaker, update


def _gen_noise() -> tuple[str, str, CoPUpdate | None]:
    """Generate a noise message (ack, banter, etc.)."""
    text = random.choice(NOISE_MESSAGES + BANTER)
    speaker = random.choice(SPEAKERS_BM + SPEAKERS_WC)
    return text, speaker, None  # None = no expected CoPUpdate


# Registry of generators with weights
_GENERATORS = [
    (_gen_entity_id, 0.12),
    (_gen_status_change, 0.15),
    (_gen_fuel, 0.10),
    (_gen_weapons, 0.08),
    (_gen_threat, 0.12),
    (_gen_csar, 0.05),
    (_gen_tasking, 0.10),
]


def generate_messages(
    count: int = 50,
    noise_ratio: float = 0.30,
    start_time: datetime | None = None,
    rate_per_min: float = 5.0,
) -> tuple[list[IRCMessage], list[CoPUpdate | None]]:
    """Generate synthetic military chat messages with ground truth labels.

    Args:
        count: Number of messages to generate.
        noise_ratio: Fraction of messages that are noise (0.0-1.0).
        start_time: Starting timestamp (default: now).
        rate_per_min: Messages per minute (controls inter-message timing).

    Returns:
        Tuple of (messages, ground_truth). ground_truth[i] is the expected
        CoPUpdate for messages[i], or None if the message is noise.
    """
    if start_time is None:
        start_time = datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc)

    interval = timedelta(seconds=60.0 / rate_per_min)
    messages: list[IRCMessage] = []
    ground_truth: list[CoPUpdate | None] = []

    # Pre-compute generator weights
    gen_funcs = [g for g, _ in _GENERATORS]
    gen_weights = [w for _, w in _GENERATORS]

    for i in range(count):
        ts = start_time + interval * i

        if random.random() < noise_ratio:
            text, speaker, expected = _gen_noise()
            channel = _rand_channel()
        else:
            gen = random.choices(gen_funcs, weights=gen_weights, k=1)[0]
            text, speaker, expected = gen()
            if expected:
                expected.timestamp = ts
                channel = expected.source_channel
            else:
                channel = _rand_channel()

        msg = IRCMessage(
            timestamp=ts,
            channel=channel,
            sender=speaker,
            content=text,
            raw_line=f"[{ts.strftime('%H:%M:%S')}] {speaker}: {text}",
        )
        messages.append(msg)
        ground_truth.append(expected)

    return messages, ground_truth
