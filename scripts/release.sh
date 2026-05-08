#!/usr/bin/env bash
# Create a tagged release.
#
# Usage:
#   ./scripts/release.sh 0.1.0        # creates tag v0.1.0
#   ./scripts/release.sh 0.2.0-rc1    # creates tag v0.2.0-rc1
#
# This script:
# 1. Updates the version in pyproject.toml and src/chat_to_cop/__init__.py
# 2. Moves [Unreleased] entries in CHANGELOG.md under the new version header
# 3. Commits the version bump
# 4. Creates an annotated git tag
# 5. Pushes the commit and tag (which triggers CI build + release)

set -euo pipefail

VERSION="${1:?Usage: $0 <version> (e.g., 0.1.0)}"
TAG="v${VERSION}"

# Sanity checks
if [[ $(git status --porcelain) ]]; then
    echo "ERROR: Working directory is not clean. Commit or stash changes first."
    exit 1
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "ERROR: Tag $TAG already exists."
    exit 1
fi

echo "Releasing $TAG..."

# Update version in pyproject.toml
sed -i "s/^version = \".*\"/version = \"${VERSION}\"/" pyproject.toml

# Update version in __init__.py
sed -i "s/__version__ = \".*\"/__version__ = \"${VERSION}\"/" src/chat_to_cop/__init__.py

# Update CHANGELOG: replace [Unreleased] with version + date
DATE=$(date +%Y-%m-%d)
sed -i "s/## \[Unreleased\]/## [Unreleased]\n\n## [${VERSION}] - ${DATE}/" CHANGELOG.md

# Commit and tag
git add pyproject.toml src/chat_to_cop/__init__.py CHANGELOG.md
git commit -m "release: ${TAG}

Co-Authored-By: Claude <noreply@anthropic.com>"

git tag -a "$TAG" -m "Release ${TAG}"

echo ""
echo "Release $TAG prepared locally."
echo "To publish: git push origin main $TAG"
echo ""
echo "This will trigger the CI pipeline to:"
echo "  1. Run lint + tests"
echo "  2. Build and push container image as $TAG"
echo "  3. Create a GitLab release"
