#!/usr/bin/env python3
"""Validate portable (flash drive) deployment files.

Checks that all required files exist, the compose YAML parses correctly
with the expected services, the Python package imports cleanly, and the
start scripts reference docker.

Used by the test-portable CI job in .gitlab-ci.yml.
"""

import subprocess
import sys
from pathlib import Path

REQUIRED_FILES = [
    "deploy/portable/docker-compose.yml",
    "deploy/portable/start.sh",
    "deploy/portable/start.bat",
    "deploy/portable/README",
    "Dockerfile",
    "scripts/mock_irc_server.py",
    "scripts/package-flash-drive.sh",
]

REQUIRED_SERVICES = {"ollama", "mock-irc", "pipeline", "api"}

errors = 0


def fail(msg):
    global errors
    print(f"FAIL: {msg}")
    errors += 1


def ok(msg):
    print(f"  OK: {msg}")


# --- Check required files ---
print("=== Check required portable files exist ===")
for f in REQUIRED_FILES:
    if Path(f).is_file():
        ok(f)
    else:
        fail(f"missing {f}")

# --- Validate docker-compose.yml ---
print("\n=== Validate docker-compose.yml ===")
try:
    import yaml
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "pyyaml"])
    import yaml

compose_path = Path("deploy/portable/docker-compose.yml")
if compose_path.is_file():
    with open(compose_path) as fh:
        doc = yaml.safe_load(fh)
    services = doc.get("services", {})
    found = set(services.keys())
    missing = REQUIRED_SERVICES - found
    if missing:
        fail(f"missing services: {missing}")
    for name, svc in services.items():
        if "image" not in svc:
            fail(f"service {name} has no image")
    print(f"  Compose valid: {len(services)} services ({', '.join(sorted(found))})")

# --- Verify package imports ---
print("\n=== Verify package installs and imports ===")
try:
    from chat_to_cop.models.cop_update import CoPUpdate  # noqa: F401
    from chat_to_cop.api import app  # noqa: F401

    ok("package import")
except ImportError as e:
    fail(f"package import: {e}")

# --- Verify start scripts ---
print("\n=== Verify start scripts ===")
start_sh = Path("deploy/portable/start.sh")
start_bat = Path("deploy/portable/start.bat")

if start_sh.is_file():
    content = start_sh.read_text()
    if content.startswith("#!"):
        ok("start.sh has shebang")
    else:
        print("  WARNING: start.sh missing shebang")
    if "docker" in content:
        ok("start.sh references docker")
    else:
        fail("start.sh doesn't reference docker")

if start_bat.is_file():
    content = start_bat.read_text()
    if "docker" in content:
        ok("start.bat references docker")
    else:
        fail("start.bat doesn't reference docker")

# --- Result ---
print()
if errors:
    print(f"FAILED: {errors} error(s)")
    sys.exit(1)
print("All portable validation checks passed.")
