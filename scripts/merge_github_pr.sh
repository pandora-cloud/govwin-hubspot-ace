#!/usr/bin/env bash
# Audience: maintainer. Replay a GitHub pull request's commits onto GitLab main.
#
# Usage: ./scripts/merge_github_pr.sh <PR-number>
# Or:    make merge-pr PR=<PR-number>
#
# The OSS workflow for external contributors is "fork on GitHub, open PR on
# GitHub." This project's canonical source, however, is GitLab; GitHub is the
# public mirror, push-only from GitLab. So merging a GitHub PR cleanly is the
# maintainer's responsibility: fetch the PR's commits, rebase them onto GitLab
# main, fast-forward local main, and push. The next GitLab to GitHub mirror
# push lands the commits on GitHub and the PR auto-closes as merged.
#
# This script does the mechanical part; it does NOT push. Review locally,
# then push deliberately.
#
# Preflight:
#   - working tree clean
#   - on main
#   - gh CLI authenticated for pandora-cloud/govwin-hubspot-ace
#   - GitLab origin reachable
set -euo pipefail

GITHUB_REPO=${GITHUB_REPO:-pandora-cloud/govwin-hubspot-ace}

if [ $# -ne 1 ]; then
  echo "usage: $0 <PR-number>" >&2
  exit 1
fi
PR=$1

if [[ ! "$PR" =~ ^[0-9]+$ ]]; then
  echo "PR must be a positive integer; got '$PR'" >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "working tree is not clean; commit or stash first" >&2
  exit 1
fi

branch=$(git rev-parse --abbrev-ref HEAD)
if [ "$branch" != "main" ]; then
  echo "not on main (on $branch); run: git checkout main" >&2
  exit 1
fi

echo "Fetching latest GitLab main..."
git fetch origin main

echo "Fetching PR #$PR from $GITHUB_REPO..."
pr_branch="pr-$PR"
# Delete any stale local branch from a prior aborted merge.
if git rev-parse --verify "$pr_branch" >/dev/null 2>&1; then
  git branch -D "$pr_branch"
fi
gh pr checkout "$PR" --repo "$GITHUB_REPO" --branch "$pr_branch"

echo "Rebasing $pr_branch onto origin/main..."
git rebase origin/main

echo "Returning to main and fast-forwarding to $pr_branch..."
git checkout main
git merge --ff-only "$pr_branch"

echo
echo "PR #$PR replayed onto local main."
echo "Review:"
echo "    git log origin/main..HEAD --stat"
echo
echo "Run the test suite if the PR touched code:"
echo "    make test"
echo
echo "Push when satisfied:"
echo "    git push origin main"
echo
echo "Once the mirror pushes (within ~5 minutes), PR #$PR auto-closes as merged on GitHub."
echo "Clean up the local PR branch when you no longer need it:"
echo "    git branch -D $pr_branch"
