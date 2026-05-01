#!/usr/bin/env python3
"""narwhal_connect.py -- SSH to Narwhal HPC and run a command.

Thin wrapper around paramiko GSSAPI that bridges MIT Kerberos for Windows
(HPCMP iLauncher) to SSH. Git Bash's native OpenSSH cannot read the API:
credential cache, and PuTTY 0.76 hangs on kex with OpenSSH 10.2.

Prerequisites:
    pip install paramiko gssapi
    Active Kerberos ticket: kinit YOUR_USERNAME@HPCMP.HPC.MIL (via iLauncher)

Usage:
    python scripts/narwhal_connect.py "squeue -u $USER"
    python scripts/narwhal_connect.py "sbatch \\$WORKDIR/scripts/narwhal_30b_test.sh"
    python scripts/narwhal_connect.py "cat \\$WORKDIR/output/c2c-30b_12345.out"
"""

from __future__ import annotations

import os
import sys

# Force UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Suppress CryptographyDeprecationWarning before paramiko import
import warnings

warnings.filterwarnings("ignore")

# Point to HPCMP Kerberos config
KRB5_CONFIG = os.environ.get("KRB5_CONFIG", "")
os.environ["KRB5_CONFIG"] = KRB5_CONFIG

import paramiko  # noqa: E402

NARWHAL_HOST = os.environ.get("NARWHAL_HOST", "narwhal.navydsrc.hpc.mil")
NARWHAL_USER = os.environ.get("NARWHAL_USER", os.environ.get("USER", os.environ.get("USERNAME", "")))


def ssh_exec(command: str, *, timeout: int = 600) -> int:
    """Execute a command on Narwhal via GSSAPI SSH. Returns exit code."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            NARWHAL_HOST,
            username=NARWHAL_USER,
            gss_auth=True,
            gss_kex=True,
            gss_deleg_creds=True,
            timeout=30,
        )
    except Exception as e:
        print(f"SSH connection failed: {e}", file=sys.stderr)
        print("Ensure you have a valid Kerberos ticket (iLauncher / kinit)", file=sys.stderr)
        return 1

    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        for line in stdout:
            print(line, end="")
        err = stderr.read().decode("utf-8", errors="replace")
        if err:
            print(err, end="", file=sys.stderr)
        return stdout.channel.recv_exit_status()
    finally:
        client.close()


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <command>", file=sys.stderr)
        print("", file=sys.stderr)
        print("Examples:", file=sys.stderr)
        print(f'  python {sys.argv[0]} "squeue -u $USER"', file=sys.stderr)
        print(f'  python {sys.argv[0]} "sbatch $WORKDIR/scripts/narwhal_30b_test.sh"', file=sys.stderr)
        print(f'  python {sys.argv[0]} "tail -50 $WORKDIR/output/c2c-30b_12345.out"', file=sys.stderr)
        sys.exit(1)

    command = " ".join(sys.argv[1:])
    exit_code = ssh_exec(command)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
