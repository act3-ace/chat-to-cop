#!/usr/bin/env python3
"""Aggregate speaker sweep results and run statistical analysis.

Reads all sweep_*.db files from the sweep output directory, evaluates each
against the Opus silver labels, and produces:

1. Per-run accuracy CSV
2. Per-cell summary statistics (mean, SD, 95% CI)
3. 3-way ANOVA (model x speakers x temperature)
4. Per-type breakdown with cross-run variance
5. Markdown report

Usage:
    python scripts/eval_speaker_sweep.py \\
        --sweep-dir data/narwhal_results_sweep \\
        --labels data/labels/dash3_silver_labels_opus.jsonl \\
        -o docs/SPEAKER_MODEL_RESULTS.md

    # Or point to the Narwhal output directly:
    python scripts/eval_speaker_sweep.py \\
        --sweep-dir /p/work1/hsclouse/output/sweep \\
        --labels data/labels/dash3_silver_labels_opus.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class RunResult:
    """Result of evaluating one sweep run against labels."""

    sweep_name: str  # e.g., "7b_on_det"
    model: str  # e.g., "7b"
    speakers: bool
    deterministic: bool
    array_job: str
    task_id: str
    db_path: str
    n_labels: int
    n_matched: int
    match_rate: float
    type_exact: float
    type_relaxed: float
    entity_overlap: float
    noise_accuracy: float
    # Per-type accuracy dict
    per_type: dict[str, float]


def parse_sweep_name(name: str) -> tuple[str, bool, bool]:
    """Parse sweep name like '7b_on_det' -> ('7b', True, True)."""
    parts = name.split("_")
    model = parts[0]
    speakers = parts[1] == "on"
    deterministic = parts[2] == "det"
    return model, speakers, deterministic


def load_labels(path: Path) -> dict[tuple[str, str, str], dict]:
    """Load Opus silver labels keyed by (timestamp, channel, sender)."""
    labels = {}
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            key = (rec["timestamp"], rec["channel"], rec["sender"])
            labels[key] = rec
    return labels


def eval_db(db_path: Path, labels: dict) -> dict:
    """Evaluate a single replay DB against labels. Returns metrics dict."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            "SELECT timestamp, source_channel, source_speaker, update_type, "
            "confidence, extraction_method, entities FROM cop_updates"
        ).fetchall()
    except sqlite3.OperationalError:
        # Try the audit table if cop_updates doesn't exist
        rows = conn.execute(
            "SELECT timestamp, source_channel, source_speaker, update_type, "
            "confidence, extraction_method, entities FROM updates"
        ).fetchall()
    finally:
        conn.close()

    # Build extraction lookup
    extractions = {}
    for row in rows:
        key = (row["timestamp"], row["source_channel"], row["source_speaker"])
        extractions[key] = dict(row)

    # Match against labels
    matched = 0
    type_correct = 0
    type_relaxed_correct = 0
    entity_overlaps = []
    noise_total = 0
    noise_correct = 0
    per_type_correct: dict[str, int] = defaultdict(int)
    per_type_total: dict[str, int] = defaultdict(int)

    # Relaxed type mapping (synonyms)
    RELAXED_MAP = {
        "entity_id": "entity_id",
        "status_change": "status_change",
        "location": "location",
        "threat": "threat",
        "tasking": "tasking",
        "sitrep": "sitrep",
    }

    for key, label in labels.items():
        if key not in extractions:
            if label.get("update_type") == "none":
                noise_total += 1
                noise_correct += 1  # correctly not extracted
            continue

        extraction = extractions[key]
        matched += 1

        label_type = label.get("update_type", "none")
        ext_type = extraction.get("update_type", "none")

        per_type_total[label_type] += 1
        if ext_type == label_type:
            type_correct += 1
            per_type_correct[label_type] += 1
        if RELAXED_MAP.get(ext_type, ext_type) == RELAXED_MAP.get(label_type, label_type):
            type_relaxed_correct += 1

        # Entity overlap (Jaccard on callsigns)
        try:
            ext_entities = (
                json.loads(extraction.get("entities", "[]"))
                if isinstance(extraction.get("entities"), str)
                else extraction.get("entities", [])
            )
            label_entities = label.get("entities", [])
            ext_callsigns = {e.get("callsign", "") for e in (ext_entities or []) if e.get("callsign")}
            label_callsigns = {e.get("callsign", "") for e in (label_entities or []) if e.get("callsign")}
            if ext_callsigns or label_callsigns:
                jaccard = len(ext_callsigns & label_callsigns) / len(ext_callsigns | label_callsigns)
                entity_overlaps.append(jaccard)
        except (json.JSONDecodeError, TypeError):
            pass

        if label_type == "none":
            noise_total += 1
            if ext_type == "none":
                noise_correct += 1

    # Handle labels with no extraction as noise misses
    for key, label in labels.items():
        if key not in extractions and label.get("update_type") != "none":
            per_type_total[label.get("update_type", "unknown")] += 1

    n_labels = len(labels)
    per_type_rates = {}
    for t in per_type_total:
        per_type_rates[t] = per_type_correct.get(t, 0) / per_type_total[t] if per_type_total[t] > 0 else 0.0

    return {
        "n_labels": n_labels,
        "n_matched": matched,
        "match_rate": matched / n_labels if n_labels > 0 else 0.0,
        "type_exact": type_correct / matched if matched > 0 else 0.0,
        "type_relaxed": type_relaxed_correct / matched if matched > 0 else 0.0,
        "entity_overlap": float(np.mean(entity_overlaps)) if entity_overlaps else 0.0,
        "noise_accuracy": noise_correct / noise_total if noise_total > 0 else 0.0,
        "per_type": per_type_rates,
    }


def discover_dbs(sweep_dir: Path) -> list[tuple[str, Path]]:
    """Find all sweep_*.db files and parse their cell identity."""
    dbs = []
    pattern = re.compile(r"sweep_(\w+)_(\d+)_(\d+)\.db")
    for db_path in sorted(sweep_dir.glob("sweep_*.db")):
        m = pattern.match(db_path.name)
        if m:
            sweep_name = m.group(1)
            dbs.append((sweep_name, db_path))
    return dbs


def compute_ci(values: list[float], confidence: float = 0.95) -> tuple[float, float]:
    """Bootstrap 95% confidence interval."""
    if len(values) < 2:
        return (float(np.mean(values)), float(np.mean(values)))
    rng = np.random.default_rng(42)
    n_boot = 10000
    means = []
    arr = np.array(values)
    for _ in range(n_boot):
        sample = rng.choice(arr, size=len(arr), replace=True)
        means.append(np.mean(sample))
    means = sorted(means)
    alpha = (1 - confidence) / 2
    lo = means[int(alpha * n_boot)]
    hi = means[int((1 - alpha) * n_boot)]
    return (lo, hi)


def run_anova(results: list[RunResult]) -> str:
    """Run factorial ANOVA and return formatted results."""
    try:
        import pandas as pd
        from scipy import stats
    except ImportError:
        return "(scipy/pandas not available for ANOVA; install with pip install scipy pandas)"

    # Build dataframe
    rows = []
    for r in results:
        rows.append(
            {
                "model": r.model,
                "speakers": "ON" if r.speakers else "OFF",
                "temperature": "det" if r.deterministic else "stoch",
                "type_exact": r.type_exact * 100,  # percentage
                "entity_overlap": r.entity_overlap * 100,
                "match_rate": r.match_rate * 100,
            }
        )
    df = pd.DataFrame(rows)

    output = []
    output.append("### Factorial ANOVA (model x speakers x temperature)")
    output.append("")

    for metric in ["type_exact", "entity_overlap", "match_rate"]:
        output.append(f"**{metric}:**")
        output.append("")

        try:
            import statsmodels.api as sm
            from statsmodels.formula.api import ols

            formula = f"{metric} ~ C(model) * C(speakers) * C(temperature)"
            model_fit = ols(formula, data=df).fit()
            anova_table = sm.stats.anova_lm(model_fit, typ=2)
            output.append("```")
            output.append(anova_table.to_string())
            output.append("```")
        except ImportError:
            # Fallback: simple two-way comparison
            on = df[df["speakers"] == "ON"][metric].values
            off = df[df["speakers"] == "OFF"][metric].values
            t_stat, p_val = stats.ttest_ind(on, off)
            output.append(f"  Speakers ON vs OFF: t={t_stat:.3f}, p={p_val:.4f}")
            det = df[df["temperature"] == "det"][metric].values
            stoch = df[df["temperature"] == "stoch"][metric].values
            t_stat2, p_val2 = stats.ttest_ind(det, stoch)
            output.append(f"  Deterministic vs Stochastic: t={t_stat2:.3f}, p={p_val2:.4f}")

        output.append("")

    return "\n".join(output)


def generate_report(results: list[RunResult], output_path: Path | None) -> str:
    """Generate the full markdown report."""
    # Group by cell
    cells: dict[str, list[RunResult]] = defaultdict(list)
    for r in results:
        cells[r.sweep_name].append(r)

    lines = []
    lines.append("# Speaker Model Sweep Results (RQ1)")
    lines.append("")
    lines.append(f"**Total runs:** {len(results)}")
    lines.append(f"**Cells:** {len(cells)}")
    lines.append(f"**N per cell:** {', '.join(str(len(v)) for v in cells.values())}")
    lines.append("")

    # Summary table
    lines.append("## Per-Cell Summary")
    lines.append("")
    lines.append("| Cell | N | Type Exact (%) | SD | 95% CI | Entity Overlap (%) | SD | Match Rate (%) |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")

    for cell_name in sorted(cells.keys()):
        runs = cells[cell_name]
        n = len(runs)
        te = [r.type_exact * 100 for r in runs]
        eo = [r.entity_overlap * 100 for r in runs]
        mr = [r.match_rate * 100 for r in runs]
        te_ci = compute_ci(te)
        lines.append(
            f"| {cell_name} | {n} "
            f"| {np.mean(te):.1f} | {np.std(te):.2f} | [{te_ci[0]:.1f}, {te_ci[1]:.1f}] "
            f"| {np.mean(eo):.1f} | {np.std(eo):.2f} "
            f"| {np.mean(mr):.1f} |"
        )

    lines.append("")

    # Speaker effect per model (the headline finding)
    lines.append("## Speaker Effect by Model")
    lines.append("")
    lines.append("Delta = (speakers ON) - (speakers OFF), pooled across seed settings:")
    lines.append("")
    lines.append("| Model | Type Exact Delta (pp) | p-value | Entity Overlap Delta (pp) | p-value |")
    lines.append("| --- | --- | --- | --- | --- |")

    models_seen = sorted(set(r.model for r in results))
    try:
        from scipy import stats as sp_stats

        has_scipy = True
    except ImportError:
        has_scipy = False

    for model in models_seen:
        on_te = [r.type_exact * 100 for r in results if r.model == model and r.speakers]
        off_te = [r.type_exact * 100 for r in results if r.model == model and not r.speakers]
        on_eo = [r.entity_overlap * 100 for r in results if r.model == model and r.speakers]
        off_eo = [r.entity_overlap * 100 for r in results if r.model == model and not r.speakers]

        delta_te = np.mean(on_te) - np.mean(off_te)
        delta_eo = np.mean(on_eo) - np.mean(off_eo)

        if has_scipy and len(on_te) > 1 and len(off_te) > 1:
            _, p_te = sp_stats.ttest_ind(on_te, off_te)
            _, p_eo = sp_stats.ttest_ind(on_eo, off_eo)
            lines.append(f"| {model} | {delta_te:+.2f} | {p_te:.4f} | {delta_eo:+.2f} | {p_eo:.4f} |")
        else:
            lines.append(f"| {model} | {delta_te:+.2f} | N/A | {delta_eo:+.2f} | N/A |")

    lines.append("")

    # Determinism effect
    lines.append("## Determinism Effect")
    lines.append("")
    lines.append("Does temperature=0 (deterministic) reduce run-to-run variance?")
    lines.append("")
    lines.append("| Model | Speakers | Stochastic SD (type_exact) | Deterministic SD | Variance Ratio |")
    lines.append("| --- | --- | --- | --- | --- |")

    for model in models_seen:
        for spk in [False, True]:
            stoch = [
                r.type_exact * 100 for r in results if r.model == model and r.speakers == spk and not r.deterministic
            ]
            det = [r.type_exact * 100 for r in results if r.model == model and r.speakers == spk and r.deterministic]
            spk_label = "ON" if spk else "OFF"
            if stoch and det:
                sd_s = np.std(stoch)
                sd_d = np.std(det)
                ratio = (sd_s / sd_d) if sd_d > 0 else float("inf")
                lines.append(f"| {model} | {spk_label} | {sd_s:.3f} | {sd_d:.3f} | {ratio:.1f}x |")

    lines.append("")

    # ANOVA
    anova_text = run_anova(results)
    lines.append(anova_text)

    # Per-type breakdown (aggregate across runs, just for the stochastic condition)
    lines.append("## Per-Type Breakdown (stochastic runs only)")
    lines.append("")

    all_types = sorted(set(t for r in results for t in r.per_type.keys()))
    for model in models_seen:
        lines.append(f"### {model}")
        lines.append("")
        lines.append("| Type | OFF mean | ON mean | Delta (pp) | ON SD | OFF SD |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for utype in all_types:
            on_vals = [
                r.per_type.get(utype, 0) * 100
                for r in results
                if r.model == model and r.speakers and not r.deterministic
            ]
            off_vals = [
                r.per_type.get(utype, 0) * 100
                for r in results
                if r.model == model and not r.speakers and not r.deterministic
            ]
            if on_vals and off_vals:
                delta = np.mean(on_vals) - np.mean(off_vals)
                lines.append(
                    f"| {utype} | {np.mean(off_vals):.1f} | {np.mean(on_vals):.1f} "
                    f"| {delta:+.1f} | {np.std(on_vals):.2f} | {np.std(off_vals):.2f} |"
                )
        lines.append("")

    report = "\n".join(lines)

    if output_path:
        output_path.write_text(report, encoding="utf-8")
        print(f"Report written to {output_path}")

    # Also write CSV
    csv_path = output_path.with_suffix(".csv") if output_path else Path("sweep_results.csv")
    with open(csv_path, "w") as f:
        f.write(
            "sweep_name,model,speakers,deterministic,task_id,n_matched,match_rate,type_exact,entity_overlap,noise_accuracy\n"
        )
        for r in results:
            f.write(
                f"{r.sweep_name},{r.model},{r.speakers},{r.deterministic},{r.task_id},"
                f"{r.n_matched},{r.match_rate:.4f},{r.type_exact:.4f},{r.entity_overlap:.4f},{r.noise_accuracy:.4f}\n"
            )
    print(f"CSV written to {csv_path}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate speaker sweep results")
    parser.add_argument("--sweep-dir", type=Path, required=True, help="Directory containing sweep_*.db files")
    parser.add_argument("--labels", type=Path, default=Path("data/labels/dash3_silver_labels_opus.jsonl"))
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output markdown report path")
    args = parser.parse_args()

    print(f"Loading labels from {args.labels}...")
    labels = load_labels(args.labels)
    print(f"  {len(labels)} labels loaded")

    print(f"Discovering DBs in {args.sweep_dir}...")
    dbs = discover_dbs(args.sweep_dir)
    print(f"  {len(dbs)} DBs found")

    if not dbs:
        print("No sweep DBs found. Check --sweep-dir path.")
        sys.exit(1)

    # Evaluate each DB
    results = []
    for i, (sweep_name, db_path) in enumerate(dbs):
        model, speakers, deterministic = parse_sweep_name(sweep_name)
        pattern = re.compile(r"sweep_\w+_(\d+)_(\d+)\.db")
        m = pattern.match(db_path.name)
        array_job = m.group(1) if m else "?"
        task_id = m.group(2) if m else "?"

        if (i + 1) % 50 == 0 or i == 0:
            print(f"  Evaluating {i + 1}/{len(dbs)}: {db_path.name}...")

        metrics = eval_db(db_path, labels)
        results.append(
            RunResult(
                sweep_name=sweep_name,
                model=model,
                speakers=speakers,
                deterministic=deterministic,
                array_job=array_job,
                task_id=task_id,
                db_path=str(db_path),
                n_labels=metrics["n_labels"],
                n_matched=metrics["n_matched"],
                match_rate=metrics["match_rate"],
                type_exact=metrics["type_exact"],
                type_relaxed=metrics["type_relaxed"],
                entity_overlap=metrics["entity_overlap"],
                noise_accuracy=metrics["noise_accuracy"],
                per_type=metrics["per_type"],
            )
        )

    print(f"\nAll {len(results)} runs evaluated.")
    print(f"Cells: {len(set(r.sweep_name for r in results))}")

    report = generate_report(results, args.output)
    if not args.output:
        print("\n" + report)


if __name__ == "__main__":
    main()
