#!/usr/bin/env python3
"""Paramiko SSH helper for AG orchestration on non-root CI runners.

The openssh `ssh` binary aborts with "No user exists for uid N" when the runner
container has no /etc/passwd entry for the (non-root) uid it runs as -- common on
the DLE k8s runners. Paramiko is pure Python and has no such getpwuid dependency,
so it connects where the ssh binary can't (this is why ag-helpers' ag-qsub-smoke
works). This wraps the two hops the orchestrators need:

  ag_ssh.py head <command>            run an allowlisted command on the AG head
  ag_ssh.py node <node_ip> <command>  run a command on a compute node, tunneled
                                      through the head's allowlisted `nc`

Local stdin is forwarded to the remote command; remote stdout/stderr stream back;
the remote exit status becomes this process's exit code.

Environment:
  AG_SSH_KEY   path to the private key (e.g. a GitLab File-type variable)
  AG_USERNAME  AG login username
  AG_HOST      optional, defaults to act3.analyticsgateway.com
"""
import io
import os
import sys
import time

import paramiko

AG_HOST = os.environ.get("AG_HOST", "act3.analyticsgateway.com")
AG_USER = os.environ["AG_USERNAME"]
CONNECT_TIMEOUT = int(os.environ.get("AG_SSH_CONNECT_TIMEOUT", "20"))


def load_key():
    """Load the AG private key, tolerating a missing trailing newline."""
    with open(os.environ["AG_SSH_KEY"], "r") as fh:
        text = fh.read()
    if not text.endswith("\n"):
        text += "\n"
    last_err = None
    for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            return cls.from_private_key(io.StringIO(text))
        except paramiko.SSHException as err:
            last_err = err
    raise SystemExit("ag_ssh: could not parse AG_SSH_KEY (%s)" % last_err)


def connect(host, pkey, sock=None):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host,
        username=AG_USER,
        pkey=pkey,
        sock=sock,
        timeout=CONNECT_TIMEOUT,
        banner_timeout=CONNECT_TIMEOUT,
        auth_timeout=CONNECT_TIMEOUT,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def run(client, command):
    """Exec command, forward stdin, stream output, return the remote exit code."""
    chan = client.get_transport().open_session()
    chan.exec_command(command)

    # Forward any piped stdin to the remote command, then close the write side.
    if not sys.stdin.isatty():
        data = sys.stdin.buffer.read()
        if data:
            chan.sendall(data)
    chan.shutdown_write()

    while True:
        moved = False
        while chan.recv_ready():
            sys.stdout.buffer.write(chan.recv(32768))
            moved = True
        while chan.recv_stderr_ready():
            sys.stderr.buffer.write(chan.recv_stderr(32768))
            moved = True
        sys.stdout.buffer.flush()
        sys.stderr.buffer.flush()
        if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
            break
        if not moved:
            time.sleep(0.2)

    return chan.recv_exit_status()


def main():
    args = sys.argv[1:]
    if len(args) < 2 or args[0] not in ("head", "node"):
        raise SystemExit("usage: ag_ssh.py head <cmd> | node <node_ip> <cmd>")

    pkey = load_key()
    head = connect(AG_HOST, pkey)

    if args[0] == "head":
        sys.exit(run(head, args[1]))

    # node: tunnel to <node_ip>:22 through the head's allowlisted `nc`.
    node_ip = args[1]
    tunnel = head.get_transport().open_session()
    tunnel.exec_command("nc %s 22" % node_ip)
    node = connect(node_ip, pkey, sock=tunnel)
    sys.exit(run(node, args[2]))


if __name__ == "__main__":
    main()
