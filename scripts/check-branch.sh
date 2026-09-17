#!/usr/bin/env bash
#
# Refuse to build a commit directly on a protected branch.
#
# Work on this fork starts on a feature branch, created before the first file
# is touched. The reason is not ceremony: a pile of uncommitted work on 'main'
# has to be unpicked file by file when the direction changes, where the same
# work on a branch is dropped by dropping a commit. That happened on
# 2026-09-17 and cost an afternoon's clarity.
#
# Deliberately silent during a merge, rebase, cherry-pick or revert. Landing a
# finished branch on 'main' is the point of having the branch, so the sequencer
# operations that do it are never blocked -- only a commit you typed yourself.
#
# Usage:
#   scripts/check-branch.sh          # the pre-commit hook calls this
#
# One deliberate commit on 'main' goes through with 'git commit --no-verify'.
#
set -euo pipefail

PROTECTED=(main master)

cd "$(git rev-parse --show-toplevel)"
git_dir=$(git rev-parse --git-dir)

# Mid-sequencer: this commit is git's, not yours.
for marker in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD rebase-merge rebase-apply; do
  if [[ -e "$git_dir/$marker" ]]; then
    echo "==> branch check skipped: $marker in progress"
    exit 0
  fi
done

# Detached HEAD is not a branch anyone is accumulating work on.
branch=$(git symbolic-ref --short -q HEAD || true)
if [[ -z $branch ]]; then
  echo "==> branch check skipped: detached HEAD"
  exit 0
fi

for protected in "${PROTECTED[@]}"; do
  if [[ $branch == "$protected" ]]; then
    cat >&2 <<MSG
error: refusing to commit directly on '$branch'.

       Work here starts on a feature branch. Nothing is lost by moving now --
       the staged changes come with you:

           git switch -c feat/<topic>
           git commit ...

       If this commit genuinely belongs on '$branch', say so explicitly:

           git commit --no-verify
MSG
    exit 1
  fi
done

echo "==> branch ok: $branch"
