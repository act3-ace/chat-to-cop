"""Quick smoke test: run 5 real DASH messages through the pipeline.

Usage:
    # Local Ollama (default)
    python scripts/quick_test.py --model qwen2.5:7b

    # OpenAI-compatible cloud (Gemini Flash, Groq, OpenAI, vLLM, etc.)
    python scripts/quick_test.py \
        --url https://generativelanguage.googleapis.com/v1beta/openai/ \
        --model gemini-2.5-flash

    # Anthropic API direct (uses ANTHROPIC_API_KEY env var)
    python scripts/quick_test.py --anthropic --model claude-haiku-4-5-20251001

    # AWS Bedrock (uses AWS credentials)
    python scripts/quick_test.py --bedrock \
        --model us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0
"""

import argparse
import asyncio
import os
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


def _build_backend(args):
    """Build the appropriate backend based on CLI flags."""
    if args.bedrock:
        from chat_to_cop.backend.bedrock import BedrockBackend

        return BedrockBackend(
            model=args.model,
            aws_region=args.bedrock_region,
            timeout=120.0,
            max_retries=3,
        )
    if args.anthropic:
        from chat_to_cop.backend.anthropic_direct import AnthropicDirectBackend

        return AnthropicDirectBackend(
            model=args.model,
            timeout=120.0,
            max_retries=3,
        )
    # Pick the right API key based on the endpoint, with sensible fallbacks.
    url = args.url.lower()
    if "googleapis.com" in url or "generativelanguage" in url:
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    elif "groq.com" in url:
        api_key = os.environ.get("GROQ_API_KEY")
    elif "openai.com" in url:
        api_key = os.environ.get("OPENAI_API_KEY")
    else:
        api_key = None
    # Explicit override always wins; "not-needed" for local Ollama.
    api_key = os.environ.get("CHAT_TO_COP_LLM_API_KEY") or api_key or os.environ.get("OPENAI_API_KEY") or "not-needed"
    return OpenAICompatibleBackend(
        base_url=args.url,
        model=args.model,
        api_key=api_key,
        timeout=120.0,
        max_retries=3,
    )


async def run_test(args):
    if args.bedrock:
        label = f"Bedrock {args.model} ({args.bedrock_region})"
    elif args.anthropic:
        label = f"Anthropic {args.model}"
    else:
        label = f"{args.model} @ {args.url}"

    print(f"\n{'=' * 60}")
    print(f"SMOKE TEST: {label}")
    print(f"{'=' * 60}\n")

    backend = _build_backend(args)
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
    parser.add_argument("--url", default="http://127.0.0.1:11434/v1", help="OpenAI-compatible endpoint URL")
    parser.add_argument("--model", default="qwen2.5:3b", help="Model name")
    parser.add_argument("--bedrock", action="store_true", help="Use AWS Bedrock instead of OpenAI-compatible")
    parser.add_argument("--bedrock-region", default="us-gov-west-1", help="AWS region for Bedrock")
    parser.add_argument("--anthropic", action="store_true", help="Use Anthropic API directly (ANTHROPIC_API_KEY)")
    args = parser.parse_args()
    asyncio.run(run_test(args))


if __name__ == "__main__":
    main()
