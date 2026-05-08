#!/usr/bin/env python3
"""Generate an AI Bill of Materials (AIBOM) in CycloneDX ML-BOM v1.7 format.

Reads project metadata from pyproject.toml, captures installed dependencies
via pip freeze, and includes model and dataset references.

Outputs: data/aibom.json

Usage:
    python scripts/generate_aibom.py
    python scripts/generate_aibom.py --output custom/path.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Project root: two levels up from scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _git_commit_hash() -> str:
    """Get the current git commit hash, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _pip_freeze() -> list[dict[str, str]]:
    """Get installed packages from pip freeze."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze", "--local"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []

        components = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            if "==" in line:
                name, version = line.split("==", 1)
            elif ">=" in line:
                name, version = line.split(">=", 1)
            else:
                name, version = line, "unknown"
            components.append({"name": name.strip(), "version": version.strip()})
        return components
    except Exception:
        return []


def _read_pyproject() -> dict:
    """Read pyproject.toml and return parsed data."""
    toml_path = PROJECT_ROOT / "pyproject.toml"
    if not toml_path.exists():
        return {}

    # Python 3.11+ has tomllib; fall back to manual parsing
    try:
        import tomllib

        with open(toml_path, "rb") as f:
            return tomllib.load(f)
    except ImportError:
        pass

    try:
        import tomli

        with open(toml_path, "rb") as f:
            return tomli.load(f)
    except ImportError:
        pass

    # Minimal fallback: extract name and version from pyproject.toml text
    data: dict = {"project": {}}
    text = toml_path.read_text()
    for line in text.splitlines():
        if line.startswith("name ="):
            data["project"]["name"] = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("version ="):
            data["project"]["version"] = line.split("=", 1)[1].strip().strip('"')
    return data


def generate_aibom(output_path: Path | None = None) -> dict:
    """Generate and write a CycloneDX ML-BOM v1.7 compatible JSON document."""
    pyproject = _read_pyproject()
    project_meta = pyproject.get("project", {})
    project_name = project_meta.get("name", "chat-to-cop")
    project_version = project_meta.get("version", "0.1.0")
    commit_hash = _git_commit_hash()

    # Build CycloneDX BOM structure
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": {
                "type": "machine-learning-model",
                "name": project_name,
                "version": project_version,
                "description": project_meta.get("description", ""),
                "properties": [],
            },
            "properties": [
                {"name": "git:commit", "value": commit_hash},
                {"name": "cdx:reproducibility:environment:python", "value": sys.version},
            ],
        },
        "components": [],
        "modelCard": {
            "modelParameters": {
                "approach": {
                    "type": "supervised",
                    "description": (
                        "Structured extraction from military IRC chat using "
                        "instruction-tuned LLMs with Pydantic output validation"
                    ),
                },
                "task": "World-state extraction from military chat to Common Operating Picture",
            },
            "datasets": [
                {
                    "ref": "dash-3-gbc-chat",
                    "type": "dataset",
                    "name": "DASH 3 GBC Chat Logs",
                    "description": "IRC chat logs from DASH 3 Global Battle Command exercise (23 Sep 2025)",
                    "classification": "UNCLASSIFIED // IL2",
                },
                {
                    "ref": "dash-3-silver-labels",
                    "type": "dataset",
                    "name": "DASH 3 Silver Labels",
                    "description": "LLM-generated silver labels for evaluation (not ground truth)",
                    "classification": "UNCLASSIFIED // IL2",
                },
            ],
            "considerations": {
                "users": ["ACT3 wargame analysts", "Battle managers (DASH/MASH events)"],
                "useCases": [
                    "Real-time world-state extraction from military IRC chat",
                    "Automated CoP database population during exercises",
                ],
                "limitations": [
                    "Designed for exercise/wargame data only (IL2)",
                    "Accuracy depends on LLM model quality and military jargon coverage",
                    "Speaker model learning requires sufficient message history",
                    "Not validated for operational use",
                ],
                "ethicalConsiderations": [
                    {
                        "name": "DoD AI Ethics Principle 3 (Traceability)",
                        "description": (
                            "Every extraction includes model_name, prompt_hash, confidence, "
                            "and extraction_method for full audit trail"
                        ),
                    },
                    {
                        "name": "Human oversight",
                        "description": (
                            "Tiered write authority (AUTO/FLAGGED/HUMAN) ensures high-risk "
                            "updates require human review before CoP database writes"
                        ),
                    },
                ],
            },
        },
        "modelImplementation": {
            "models": [
                {
                    "name": "qwen2.5:7b",
                    "type": "primary",
                    "description": "Primary extraction model (instruction-tuned, 7B parameters)",
                    "quantization": "Q4_K_M (typical Ollama default)",
                },
                {
                    "name": "qwen2.5:3b",
                    "type": "fallback",
                    "description": "Fallback model for degraded mode (3B parameters)",
                    "quantization": "Q4_K_M (typical Ollama default)",
                },
                {
                    "name": "regex_fallback",
                    "type": "fallback",
                    "description": "Rule-based regex extraction when all LLMs unavailable",
                },
            ],
        },
    }

    # Add software dependency components from pip freeze
    for pkg in _pip_freeze():
        bom["components"].append(
            {
                "type": "library",
                "name": pkg["name"],
                "version": pkg["version"],
            }
        )

    # Write output
    if output_path is None:
        output_path = PROJECT_ROOT / "data" / "aibom.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bom, indent=2) + "\n")
    print(f"AIBOM written to {output_path}")

    return bom


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate AI Bill of Materials (AIBOM)")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (default: data/aibom.json)",
    )
    args = parser.parse_args()
    generate_aibom(args.output)


if __name__ == "__main__":
    main()
