#!/usr/bin/env bash
# Release swedish-jobs-mcp to PyPI.
#
# Usage:  ./release.sh
#
# Before running: raise the version in pyproject.toml AND server.json
# (all three places), and commit.
#
# The token is read silently and kept only in memory. It is never printed or
# written to a file. If PYPI_SWEDISH_JOBS_MCP_TOKEN is already set in your
# shell, that value is used instead of asking.
set -euo pipefail

PKG="swedish-jobs-mcp"
cd "$(dirname "$0")"

say()     { printf '\n==> %s\n' "$1"; }
fail()    { printf '\nERROR: %s\n' "$1" >&2; exit 1; }
confirm() { local a; read -r -p "$1 [y/N] " a; [[ "$a" == "y" || "$a" == "Y" ]]; }

TOKEN="${PYPI_SWEDISH_JOBS_MCP_TOKEN:-}"
trap 'TOKEN=""' EXIT

command -v uv  >/dev/null || fail "uv is not installed. See https://docs.astral.sh/uv/"
command -v git >/dev/null || fail "git is not installed."

# --- 1. Version numbers -------------------------------------------------
say "1/7 Checking version numbers"
VERSION="$(sed -nE 's/^version = "([^"]+)".*/\1/p' pyproject.toml | head -n1)"
[[ -n "$VERSION" ]] || fail "Could not read the version from pyproject.toml."
echo "Version in pyproject.toml: $VERSION"

BAD="$(grep -E '"version"' server.json | grep -v "\"$VERSION\"" || true)"
if [[ -n "$BAD" ]]; then
  echo "$BAD"
  fail "server.json has a version that is not $VERSION (see above)."
fi

CODE="$(curl -s -o /dev/null -w '%{http_code}' "https://pypi.org/pypi/$PKG/$VERSION/json" || true)"
if [[ "$CODE" == "200" ]]; then
  fail "$PKG $VERSION is already on PyPI. Raise the version and try again."
elif [[ "$CODE" != "404" ]]; then
  echo "Warning: could not check PyPI (got '$CODE'). Are you offline?"
fi

# --- 2. Git state -------------------------------------------------------
say "2/7 Checking git"
MARKERS="$(git grep -nE '^(<<<<<<<|>>>>>>>)( |$)' -- . ':!release.sh' || true)"
if [[ -n "$MARKERS" ]]; then
  echo "$MARKERS"
  fail "Merge conflict markers found (see above)."
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  git status --short
  fail "You have uncommitted changes. Commit them first."
fi

git fetch -q origin 2>/dev/null || echo "Warning: could not reach GitHub."
read -r AHEAD BEHIND < <(git rev-list --left-right --count "HEAD...@{u}" 2>/dev/null || echo "0 0")
[[ "$BEHIND" == "0" ]] || fail "GitHub has $BEHIND commit(s) you do not have. Run: git pull --no-rebase"
echo "Git is clean. Local commits not on GitHub yet: $AHEAD"

# --- 3-4. Dependencies, lint, tests -------------------------------------
say "3/7 Installing dependencies"
uv sync --all-extras --all-groups

say "4/7 Lint and offline tests"
uv run ruff check .
uv run pytest tests/test_server.py tests/test_e2e.py -q

# --- 5. Token -----------------------------------------------------------
say "5/7 PyPI token"
if [[ -z "$TOKEN" ]]; then
  read -r -s -p "Paste the PyPI token (hidden), then press Enter: " TOKEN
  echo
fi
[[ "$TOKEN" == pypi-* ]] || fail "The token must start with pypi-"
echo "Token looks right: ${#TOKEN} characters, starts with ${TOKEN:0:5}"

# --- 6. Build and publish -----------------------------------------------
say "6/7 Building"
rm -rf dist
uv build
ls -1 dist

echo
confirm "Publish $PKG $VERSION to PyPI now? A published version cannot be overwritten." \
  || fail "Cancelled. Nothing was published."

UV_PUBLISH_TOKEN="$TOKEN" uv publish

# --- 7. After ------------------------------------------------------------
say "7/7 Published"
echo "https://pypi.org/project/$PKG/$VERSION/"

if confirm "Tag v$VERSION and push to GitHub?"; then
  git tag -a "v$VERSION" -m "Release $VERSION"
  git push origin HEAD "v$VERSION"
fi

echo
echo "Done. The token was only kept in memory."
