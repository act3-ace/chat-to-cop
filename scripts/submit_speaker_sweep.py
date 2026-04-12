#!/usr/bin/env python3
"""Submit the full speaker model sweep on Narwhal.

Generates and submits Slurm array jobs for all cells of the factorial
experiment: model_size x speakers x temperature.

Usage (from the Narwhal login node, or via narwhal-ssh.py):
    cd $WORKDIR/chat-to-cop
    python scripts/submit_speaker_sweep.py --n 100 --models 3b,7b,14b,32b
    python scripts/submit_speaker_sweep.py --n 100 --models 7b,14b --seed-only
    python scripts/submit_speaker_sweep.py --dry-run  # print sbatch commands without submitting

Prerequisites:
    - All models pulled (run with --pull-models first)
    - Latest source installed (git pull && pip install -e . -q)
"""

from __future__ import annotations

import argparse
import subprocess

# Walltime per model (generous; speakers-ON adds ~70% overhead)
WALLTIMES = {
    "3b": "03:00:00",
    "7b": "05:00:00",
    "14b": "08:00:00",
    "32b": "16:00:00",
}

# LLM timeout per model (seconds per extraction call)
TIMEOUTS = {
    "3b": 60,
    "7b": 120,
    "14b": 180,
    "32b": 600,
}

# Max concurrent tasks per array (prevents overwhelming the scheduler/filesystem)
MAX_CONCURRENT = 20


def submit_cell(
    model: str,
    speakers: bool,
    deterministic: bool,
    n: int,
    dry_run: bool,
    max_concurrent: int = MAX_CONCURRENT,
) -> str | None:
    """Submit one Slurm array job for a single cell."""
    spk_label = "on" if speakers else "off"
    det_label = "det" if deterministic else "stoch"
    sweep_name = f"{model}_{spk_label}_{det_label}"
    temperature = "0" if deterministic else "default"

    walltime = WALLTIMES.get(model, "08:00:00")
    timeout = TIMEOUTS.get(model, 180)

    env_vars = ",".join(
        [
            f"SWEEP_MODEL_BASE=qwen2.5:{model}",
            f"SWEEP_SPEAKERS={'true' if speakers else 'false'}",
            f"SWEEP_TEMPERATURE={temperature}",
            f"SWEEP_NAME={sweep_name}",
            "SWEEP_NUM_CTX=8192",
            f"SWEEP_TIMEOUT={timeout}",
        ]
    )

    cmd = (
        f"sbatch"
        f" --job-name=sw-{sweep_name}"
        f" --time={walltime}"
        f" --array=1-{n}%{max_concurrent}"
        f" --output=sweep_{sweep_name}_%A_%a.out"
        f" --error=sweep_{sweep_name}_%A_%a.err"
        f" --export=ALL,{env_vars}"
        f" scripts/narwhal_speaker_sweep.sh"
    )

    if dry_run:
        print(f"  [DRY-RUN] {cmd}")
        return None

    print(f"  Submitting {sweep_name} (N={n}, {walltime})...", end=" ", flush=True)
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAILED: {result.stderr.strip()}")
        return None

    # Parse "Submitted batch job 12345"
    job_id = result.stdout.strip().split()[-1]
    print(f"job array {job_id}")
    return job_id


def pull_models(models: list[str]) -> None:
    """Ensure all models are pulled on the local Ollama instance."""
    import os

    ollama_bin = os.path.join(os.environ.get("WORKDIR", "/p/work1/hsclouse"), "bin", "ollama")

    for model in models:
        tag = f"qwen2.5:{model}"
        print(f"  Pulling {tag}...", end=" ", flush=True)
        result = subprocess.run(
            [ollama_bin, "pull", tag],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            print("OK")
        else:
            print(f"FAILED: {result.stderr.strip()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Submit speaker model sweep on Narwhal",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=100,
        help="Runs per cell (default: 100)",
    )
    parser.add_argument(
        "--models",
        default="3b,7b,14b,32b",
        help="Comma-separated model sizes (default: 3b,7b,14b,32b)",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only submit deterministic (temp=0) cells",
    )
    parser.add_argument(
        "--stoch-only",
        action="store_true",
        help="Only submit stochastic (default temp) cells",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print sbatch commands without submitting",
    )
    parser.add_argument(
        "--pull-models",
        action="store_true",
        help="Pull models from Ollama registry before submitting",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=MAX_CONCURRENT,
        help=f"Max concurrent tasks per array (default: {MAX_CONCURRENT})",
    )
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",")]
    deterministic_settings = []
    if not args.stoch_only:
        deterministic_settings.append(True)
    if not args.seed_only:
        deterministic_settings.append(False)

    max_concurrent = args.max_concurrent

    # Pre-pull models if requested
    if args.pull_models:
        print("Pulling models...")
        pull_models(models)
        print("")

    # Calculate totals
    n_cells = len(models) * 2 * len(deterministic_settings)  # 2 for speakers on/off
    n_total = n_cells * args.n

    est_hours = 0
    hourly_rates = {"3b": 0.42, "7b": 1.42, "14b": 1.95, "32b": 4.17}
    for m in models:
        rate = hourly_rates.get(m, 2.0)
        est_hours += rate * args.n * 2 * len(deterministic_settings)

    print("Speaker Model Sweep")
    print(f"  Models:     {', '.join(models)}")
    print(f"  N per cell: {args.n}")
    print(f"  Cells:      {n_cells}")
    print(f"  Total jobs: {n_total}")
    print(f"  Est. V100-hours: ~{est_hours:.0f}")
    print(f"  Max concurrent per array: {max_concurrent}")
    print("")

    # Submit all cells
    job_ids = []
    for det in deterministic_settings:
        det_label = "DETERMINISTIC (temp=0)" if det else "STOCHASTIC (default temp)"
        print(f"--- {det_label} ---")
        for model in models:
            for speakers in [False, True]:  # OFF first, then ON
                jid = submit_cell(model, speakers, det, args.n, args.dry_run, max_concurrent)
                if jid:
                    job_ids.append(jid)
        print("")

    if args.dry_run:
        print(f"Dry run complete. {n_total} jobs would be submitted.")
        return

    # Summary
    print("=" * 60)
    print(f"Submitted {len(job_ids)} array jobs ({n_total} total tasks)")
    if job_ids:
        jid_list = ",".join(job_ids)
        print(f"Watch: squeue -u $USER -j {jid_list}")
        print("Count: squeue -u $USER | wc -l")
    print("")
    print("After all jobs complete, run:")
    print("  python scripts/eval_speaker_sweep.py \\")
    print("      --sweep-dir $WORKDIR/output/sweep \\")
    print("      --labels data/labels/dash3_silver_labels_opus.jsonl \\")
    print("      -o docs/SPEAKER_MODEL_RESULTS.md")


if __name__ == "__main__":
    main()
