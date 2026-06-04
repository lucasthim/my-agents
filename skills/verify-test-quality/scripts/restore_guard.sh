#!/bin/sh
# restore_guard.sh — prove a path is git-pristine. No dependencies beyond git.
# Usage: restore_guard.sh <path>
# Exits 0 (silently) only if <path> has NO staged, unstaged, or untracked changes.
# Otherwise exits non-zero with a loud message. Used by the mutation protocol to
# refuse to mutate uncommitted source and to verify a restore truly succeeded.

set -eu

if [ "$#" -ne 1 ]; then
    echo "RESTORE GUARD: usage: $0 <path>" >&2
    exit 2
fi

path="$1"

if [ ! -e "$path" ]; then
    echo "RESTORE GUARD FAILED: path does not exist: $path" >&2
    exit 3
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "RESTORE GUARD FAILED: not inside a git work tree (cwd=$(pwd))" >&2
    exit 4
fi

# Tracked changes (staged or unstaged) AND untracked status must both be empty.
if git diff --quiet -- "$path" && [ -z "$(git status --porcelain -- "$path")" ]; then
    exit 0
fi

echo "RESTORE GUARD FAILED: '$path' is NOT pristine — uncommitted or stray changes detected." >&2
echo "  Do NOT mutate or proceed. Restore the original and re-run this guard." >&2
git status --porcelain -- "$path" >&2 || true
exit 1
