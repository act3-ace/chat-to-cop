"""Quick smoke test: run 5 real DASH messages through the pipeline with Ollama.

Usage:
    python scripts/quick_test.py
    python scripts/quick_test.py --model qwen2.5:7b
    python scripts/quick_test.py --url http://remote:11434/v1
"""

import argparse
import asyncio
from datetime import datetime, timezone

from chat_to_cop.agent.channel_agent import ChannelAgent
from chat_to_cop.backend.openai_compat import OpenAICompatibleBackend

# Real messages from DASH 3 GBC chat data
TEST_MESSAGES = [
    ("[14:03:36] HYDRO_SL: SITREP / AIR: ZEUS 12,13,14 shot down by TTG; YAMA11 flight shot down by TTG", "#c2_coord"),
    ("[14:10:15] Hydro_Tank: RR15 F+40, RL36 F+50", "#c2_coord"),
    ("[14:03:03] VEGAS_ABM2: .", "#c2_coord"),
    ("[14:20:00] AOC_SIDO: tacrep e10-4, 4th-gen sam active, cigar 316/398, jtn TM677", "#isr_reports"),
    ("[14:05:22] VEGAS_SL: @Hydro_SL: copy harpy12 harpy14 thor13 shark14 all shot down", "#c2_coord"),
]


async def run_test(url: str, model: str):
    print(f"\n{'=' * 60}")
    print(f"SMOKE TEST: {model} @ {url}")
    print(f"{'=' * 60}\n")

    import os

    api_key = os.environ.get("OPENAI_API_KEY", "not-needed")
    backend = OpenAICompatibleBackend(base_url=url, model=model, api_key=api_key, timeout=120.0, max_retries=3)
    agent = ChannelAgent(channel="#c2_coord", backend=backend)

    for raw_line, channel in TEST_MESSAGES:
        # Parse the message manually (simplified)
        import re

        m = re.match(r"\[(\d{2}:\d{2}:\d{2})\]\s+(\S+?):\s*(.*)", raw_line)
        if not m:
            continue

        from chat_to_cop.models.messages import IRCMessage

        msg = IRCMessage(
            timestamp=datetime(
                2025, 9, 23, int(m.group(1)[:2]), int(m.group(1)[3:5]), int(m.group(1)[6:8]), tzinfo=timezone.utc
            ),
            channel=channel,
            sender=m.group(2),
            content=m.group(3),
            raw_line=raw_line,
        )

        print(f"INPUT:  [{msg.channel}] {msg.sender}: {msg.content}")
        print(f"        (is_stt={msg.is_stt}, priority={msg.priority.value})")

        try:
            updates = await agent.process_message(msg)

            if not updates:
                print("OUTPUT: [filtered — no world-state change detected]")
            else:
                for u in updates:
                    print(
                        f"OUTPUT: type={u.update_type.value}, confidence={u.confidence:.2f},"
                        f" method={u.extraction_method}"
                    )
                    if u.entities:
                        for e in u.entities:
                            fields = {
                                k: v for k, v in e.model_dump().items() if v is not None and k != "metadata" and v != {}
                            }
                            print(f"        entity: {fields}")
                    if u.reasoning:
                        print(f"        reasoning: {u.reasoning[:120]}")
        except Exception as e:
            print(f"ERROR:  {type(e).__name__}: {e}")

        print()

    print(f"{'=' * 60}")
    print(f"Messages processed: {agent.message_count}")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="qwen2.5:3b")
    args = parser.parse_args()
    asyncio.run(run_test(args.url, args.model))


if __name__ == "__main__":
    main()
