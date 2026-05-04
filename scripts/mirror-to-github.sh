#!/bin/bash
# mirror-to-github.sh -- Push a sanitized, history-free snapshot to GitHub.
#
# Creates an orphan commit from the current main branch, scrubs internal
# IPs, URLs, emails, usernames, and paths, then force-pushes to the
# GitHub remote. The DLE GitLab repo is never modified.
#
# Usage:
#   bash scripts/mirror-to-github.sh              # dry-run (default)
#   bash scripts/mirror-to-github.sh --push       # actually push
#   bash scripts/mirror-to-github.sh --diff       # show what would change

set -euo pipefail

REMOTE="github"
BRANCH="main"
TEMP_BRANCH="__mirror-sanitize-$$"
DRY_RUN=true
SHOW_DIFF=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --push) DRY_RUN=false; shift ;;
        --diff) SHOW_DIFF=true; shift ;;
        *) echo "Usage: $0 [--push|--diff]"; exit 1 ;;
    esac
done

# Verify we're in the repo root
if [ ! -f pyproject.toml ]; then
    echo "ERROR: Run from the chat-to-cop repo root."
    exit 1
fi

# Verify the remote exists
if ! git remote get-url "$REMOTE" &>/dev/null; then
    echo "ERROR: Remote '$REMOTE' not configured."
    echo "  git remote add github https://github.com/act3-ace/chat-to-cop.git"
    exit 1
fi

# Verify we're on main
CURRENT=$(git branch --show-current)
if [ "$CURRENT" != "$BRANCH" ]; then
    echo "ERROR: Must be on '$BRANCH' branch (currently on '$CURRENT')."
    exit 1
fi

# Verify no uncommitted changes to tracked files (untracked files are OK)
if [ -n "$(git diff HEAD --name-only)" ]; then
    echo "ERROR: Working tree has uncommitted changes. Commit or stash first."
    exit 1
fi

echo "=== Building sanitized mirror snapshot ==="

# Create orphan branch from current HEAD
git checkout --orphan "$TEMP_BRANCH" 2>/dev/null
git reset HEAD -- . >/dev/null 2>&1 || true
git checkout "$BRANCH" -- . 2>/dev/null

# ---------------------------------------------------------------------------
# Sanitize: replace sensitive values across all tracked files
# ---------------------------------------------------------------------------

echo "Sanitizing internal IPs..."
# DASH/MASH event IPs (10.5.185.x range)
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's/10\.5\.185\.[0-9]\{1,3\}/10.0.0.1/g' 2>/dev/null || true

echo "Sanitizing DLE URLs..."
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's|pydio\.dle\.afrl\.af\.mil|pydio.example.mil|g' 2>/dev/null || true
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's|confluence\.dle\.afrl\.af\.mil|confluence.example.mil|g' 2>/dev/null || true
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's|gitlab\.dle\.afrl\.af\.mil|gitlab.example.mil|g' 2>/dev/null || true

echo "Sanitizing AG URLs..."
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's|act3\.analyticsgateway\.com|ag.example.mil|g' 2>/dev/null || true
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's|litellm-bedrock\.act3\.analyticsgateway\.com|litellm.ag.example.mil|g' 2>/dev/null || true

echo "Sanitizing emails and usernames..."
# Named emails first, then catch remaining first.last.N@us.af.mil patterns
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's/hamilton\.clouse\.1@us\.af\.mil/maintainer@example.mil/g' 2>/dev/null || true
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's/[a-z.]*[0-9]*@us\.af\.mil/team@example.mil/g' 2>/dev/null || true
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's|/home/hsclouse|/home/user|g' 2>/dev/null || true
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's|C:\\Users\\hsclouse|C:\\Users\\user|g' 2>/dev/null || true
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's|C:/Users/hsclouse|C:/Users/user|g' 2>/dev/null || true

echo "Sanitizing HPC references..."
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's/hsclouse@narwhal\.navydsrc\.hpc\.mil/user@hpc.example.mil/g' 2>/dev/null || true
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's|/p/work1/hsclouse|/p/work1/user|g' 2>/dev/null || true
find . -name '.git' -prune -o -type f -print0 | \
    xargs -0 sed -i 's/hsclouse@HPCMP\.HPC\.MIL/user@HPCMP.HPC.MIL/g' 2>/dev/null || true

echo "Sanitizing deploy script usernames..."
find ./deploy -type f -print0 2>/dev/null | \
    xargs -0 sed -i 's/hsclouse/deployer/g' 2>/dev/null || true

echo "Sanitizing remaining usernames in docs..."
find ./docs -type f -print0 2>/dev/null | \
    xargs -0 sed -i 's/hsclouse/user/g' 2>/dev/null || true

# Remove files that should not appear in the public mirror
rm -f CLAUDE.md docs/CLAUDE.md 2>/dev/null || true
rm -f scripts/mirror-to-github.sh 2>/dev/null || true
rm -rf docs/internal/ 2>/dev/null || true

# ---------------------------------------------------------------------------
# Show diff or commit
# ---------------------------------------------------------------------------

if [ "$SHOW_DIFF" = true ]; then
    echo ""
    echo "=== Changes that would be applied ==="
    git diff --stat "$BRANCH"
    echo ""
    git diff "$BRANCH" -- . | head -200
    echo ""
    echo "(truncated -- run 'git diff $BRANCH' for full diff)"
    git checkout "$BRANCH" -- . 2>/dev/null
    git checkout "$BRANCH" 2>/dev/null
    git branch -D "$TEMP_BRANCH" 2>/dev/null || true
    exit 0
fi

# Stage everything
git add -A

# Commit with a clean author (no EDIPI)
COMMIT_MSG="Snapshot from $(git -C . log -1 --format='%h' "$BRANCH") ($(date -u +%Y-%m-%d))"
GIT_AUTHOR_NAME="ACT3" \
GIT_AUTHOR_EMAIL="act3@example.com" \
GIT_COMMITTER_NAME="ACT3" \
GIT_COMMITTER_EMAIL="act3@example.com" \
    git commit -m "$COMMIT_MSG" --allow-empty 2>/dev/null

if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "=== Dry run complete ==="
    echo "Would force-push to $REMOTE/$BRANCH"
    echo "Commit: $(git log -1 --oneline)"
    echo ""
    echo "Run with --push to actually push, or --diff to see changes."
    git checkout "$BRANCH" 2>/dev/null
    git branch -D "$TEMP_BRANCH" 2>/dev/null || true
    exit 0
fi

# Force-push to GitHub
echo ""
echo "=== Pushing to $REMOTE/$BRANCH ==="
git push --force "$REMOTE" "$TEMP_BRANCH:$BRANCH"

# Clean up
git checkout "$BRANCH" 2>/dev/null
git branch -d "$TEMP_BRANCH" 2>/dev/null || true

echo ""
echo "=== Mirror updated ==="
echo "GitHub now has a single sanitized commit with no history."
echo "DLE GitLab is unchanged."
