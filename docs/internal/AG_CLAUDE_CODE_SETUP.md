# Claude Code on Analytics Gateway — moved

> **This guide has moved to its own repository:**
> **<https://gitlab.dle.afrl.af.mil/analytics-gateway/ag-helpers>**

The content that used to live in this file is now split across two
docs in `analytics-gateway/ag-helpers`:

- [`docs/SETUP.md`](https://gitlab.dle.afrl.af.mil/analytics-gateway/ag-helpers/-/blob/main/docs/SETUP.md) —
  the SSH / job-control pipeline: `ag-start`, `ag-stop`, `ssh ag-node`
  directly to a compute node, IP auto-publishing via a personal
  GitLab snippet, and the `qsub`-based launcher. **Supersedes the old
  SSH section of this file, which relied on a bashrc trigger that had
  real race conditions and an `ens3`-only IP lookup that failed on the
  sidecar.**
- [`docs/CLAUDE_CODE.md`](https://gitlab.dle.afrl.af.mil/analytics-gateway/ag-helpers/-/blob/main/docs/CLAUDE_CODE.md) —
  Claude Code on Bedrock, the Mattermost MCP server, the Google
  Workspace MCP server (via `gwmcp`), the VS Code Remote Tunnel, and
  the `python3 http.server` file-download trick.

`ag-helpers` is user-contributed and unaffiliated with AG /
InfiniteTactics. It is hosted under the `analytics-gateway` top-level
group on DLE GitLab at Internal visibility, so anyone with DLE GitLab
access can clone it.

## Why it moved

This content was never specific to `chat-to-cop` — it was general AG
setup notes that happened to be written down here first. It belongs
in a repo that colleagues can clone and run directly, with scripts
under version control instead of copy-pasted from a markdown file.
The SSH section in particular had bugs that were only discovered by
running it (empty IP on the sidecar, HTTP 400 races between
bashrc-triggered and qsub-triggered publishes, stale snippet data
clobbering `~/.ssh/config` when `qsub` failed). The new repo has the
corrected code, a proper troubleshooting section, and a short-circuit
installer (`ag-side/install.sh`).
