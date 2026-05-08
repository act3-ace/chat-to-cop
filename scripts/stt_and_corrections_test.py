"""Test STT messages and correction handling against real LLM.

Tests:
- Garbled ASR output (radio checks, ASR hallucinations)
- Operational STT content (SAM reports, cigar notation with spaces)
- Correction messages ("disregard last")
"""

import argparse
import asyncio
from datetime import datetime, timezone

from chat_to_cop.agent.channel_agent import ChannelAgent
from chat_to_cop.backend.openai_compat import OpenAICompatibleBackend
from chat_to_cop.models.messages import IRCMessage

TEST_MESSAGES = [
    # --- STT: Radio checks (should be filtered or none) ---
    ("<WF2> Radio check Radio check C2 cord.", "#stt_C2Coord", "afrl_lavgn"),
    ("<WF8> Crusher BMA control at Radio 2.", "#stt_crusherBMA", "afrl_lavgn"),
    # --- STT: ASR garbage (should be none) ---
    ("<Vegas_ABM1> Hyro priest Hyro priest", "#stt_C2Coord", "afrl_lavgn"),
    ("<Vegas_ABM1> min the noise min the noise min the noise for the boys", "#stt_C2Coord", "afrl_lavgn"),
    # --- STT: Real operational content (SAM reports with mangled cigar notation) ---
    (
        "<SL> Crusher SL Vegas SL multiple 4th and 5th generation SAMs active within your BMA.",
        "#stt_C2Coord",
        "afrl_lavgn",
    ),
    (
        "<WF2> Advised, generation SAM active bullseye 3 1 5 379 passing to the rest of your guys today",
        "#stt_hydroBMA",
        "afrl_lavgn",
    ),
    ("<WF5> 5th gen SAM awake cigar 3 1 5 3 80 4th gen SAM cigar 2 8 7 2 77.", "#stt_taipanBMA", "afrl_lavgn"),
    # --- Correction messages ---
    ("Disregard last, TN 44504 is NOT DDG1, reassessing", "#c2_coord", "Intel_OPS"),
    ("correction: ZEUS14 NOT shot down, ZEUS14 is operational", "#c2_coord", "HYDRO_SL"),
    # --- Normal typed chat for comparison ---
    ("TN 44506 is 4x J-15s.", "#isr_reports", "Intel_OPS"),
]


async def run_test(url: str, model: str):
    print(f"\n{'=' * 70}")
    print(f"STT & CORRECTION TEST: {model} @ {url}")
    print(f"{'=' * 70}\n")

    backend = OpenAICompatibleBackend(base_url=url, model=model, timeout=120.0)
    agents: dict[str, ChannelAgent] = {}

    for i, (content, channel, sender) in enumerate(TEST_MESSAGES):
        if channel not in agents:
            agents[channel] = ChannelAgent(channel=channel, backend=backend, use_speaker_models=False)

        msg = IRCMessage(
            timestamp=datetime(2025, 9, 23, 14, 0, 0, tzinfo=timezone.utc)
            + __import__("datetime").timedelta(seconds=i * 10),
            channel=channel,
            sender=sender,
            content=content,
        )

        # Determine expected category
        if i < 2:
            expected = "NOISE (radio check)"
        elif i < 4:
            expected = "NOISE (ASR garbage)"
        elif i < 7:
            expected = "THREAT (SAM report)"
        elif i < 9:
            expected = "CORRECTION"
        else:
            expected = "ENTITY_ID"

        print(f"[{i + 1:2d}] [{channel:18s}] {sender}: {content[:70]}")
        print(f"     EXPECTED: {expected}")

        updates = await agents[channel].process_message(msg)

        if not updates:
            print("     GOT:      FILTERED (no LLM call)")
        elif updates[0].extraction_method == "error":
            reason = (updates[0].reasoning or "")[:80]
            print(f"     GOT:      ERROR — {reason}")
        else:
            u = updates[0]
            print(f"     GOT:      {u.update_type.value} (conf={u.confidence:.2f})")
            for e in u.entities:
                fields = {k: v for k, v in e.model_dump().items() if v is not None and k != "metadata" and v != {}}
                print(f"               entity: {fields}")
            if u.reasoning:
                print(f"               reasoning: {u.reasoning[:100]}")
        print()

    print(f"{'=' * 70}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="qwen2.5:3b")
    args = parser.parse_args()
    asyncio.run(run_test(args.url, args.model))


if __name__ == "__main__":
    main()
