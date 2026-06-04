# 11. Manual release flow, not semantic-release

Date: 2026-06-04

## Status

Accepted

## Context

ADR 0001 records the move to GitLab-canonical and GitHub-mirror. As a consequence of that move (covered in ADR 0011's deferral note in earlier conversations), the GitHub-side `release-please` workflow was removed: it opens release pull requests on GitHub that would be clobbered by the next GitLab to GitHub push mirror.

Three replacement options were considered for cutting versioned releases:

1. **`semantic-release` on GitLab CI.** Configurable, conventional-commit-driven, produces tags and GitLab releases automatically on every push to `main`. Adds a GitLab CI job, a `.releaserc.json`, a release-bot token with merge access to `main`, and a plugin tree to maintain.
2. **A scripted manual release.** A `make release VERSION=X.Y.Z` target backed by a small Python script that promotes the `CHANGELOG.md` `[Unreleased]` section, bumps `pyproject.toml`, commits, and tags. The maintainer reviews, then pushes.
3. **Pure manual release.** The maintainer edits `CHANGELOG.md` and `pyproject.toml` by hand and tags.

The release cadence for this project is expected to be low (a few releases per quarter at most). The cost of getting a release wrong is medium (the version, tag, and CHANGELOG entry all need to agree; the corresponding GitHub release notes and SBOM/SLSA artifacts fire on tag push). The benefit of automation comes mostly from eliminating typo-class mistakes and ensuring the artifact links are consistent.

## Decision

Use option 2: scripted manual release. `scripts/release.py` handles the mechanical edits; `make release VERSION=X.Y.Z` is the documented entry point. The script does not push; the maintainer reviews `git show vX.Y.Z` and pushes deliberately.

Specifically, the script:

- Verifies the working tree is clean, the branch is `main`, the version is semver, and the tag does not already exist.
- Bumps the `version = "..."` line in `pyproject.toml`.
- Promotes the `## [Unreleased]` heading in `CHANGELOG.md` to `## [vX.Y.Z] - YYYY-MM-DD` and adds a fresh empty `[Unreleased]` section.
- Maintains the compare-URL footnotes at the bottom of `CHANGELOG.md` so the GitHub-rendered "Unreleased" link points at `HEAD` and the new tag's link points at the prior tag.
- Commits with `chore(release): vX.Y.Z` and creates an annotated tag.
- Prints the push command but does not run it.

After the maintainer pushes, the tag flows through the GitLab to GitHub mirror; the GitHub release-publication workflows (`sbom.yml` and `slsa.yml`) fire on the tag publication and attach release artifacts.

## Consequences

Positive:

- One command for the version bump, the changelog promotion, the commit, and the tag. No mismatches between the four.
- The maintainer always reviews before pushing; the script's "does not push" behavior is the safety net for typo-class mistakes.
- Zero CI infrastructure to maintain for releases. No release bot token, no semantic-release plugin tree, no GitLab CI job that runs on every `main` push.
- The compare-URL footnotes stay consistent with GitHub's rendering even after several releases, which keeps the CHANGELOG navigable from the GitHub UI.

Negative:

- The script must be revisited when CHANGELOG format conventions change (for example, if we add a `Security` section per Keep a Changelog 1.1).
- A maintainer who forgets to push the tag after running `make release` produces a local-only release that does not propagate. The script's parting message reminds them to push, but human discipline is required.
- Multi-package or monorepo release flows would not be served by this script. The project is intentionally a single Python package plus its Terraform; if that changes, this ADR should be revisited.

Operational:

- The release runbook lives in `MAINTAINERS.md` under "Release ownership."
- A future migration to `semantic-release` (or a successor tool) is a documented escape hatch: this script is small enough that throwing it away is not painful when the project earns its automation.
