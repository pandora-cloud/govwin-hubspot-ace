#!/usr/bin/env python3
"""Cut a release.

Audience: maintainer. Run from the repo root via ``make release VERSION=2.2.0``
(preferred) or directly with ``scripts/release.py 2.2.0``.

This script does, in order:

1. Verifies the working tree is clean, that HEAD is on ``main``, and that the
   target tag does not already exist.
2. Bumps the version in ``pyproject.toml``.
3. Promotes the ``[Unreleased]`` section heading in ``CHANGELOG.md`` to
   ``[vX.Y.Z] - YYYY-MM-DD`` and adds a fresh empty ``[Unreleased]`` section
   above it.
4. Inserts a new ``[vX.Y.Z]: https://github.com/...`` compare-link entry at
   the bottom of ``CHANGELOG.md`` and points ``[Unreleased]: ...`` at the
   new tag.
5. Commits ``chore(release): vX.Y.Z`` and creates an annotated git tag
   ``vX.Y.Z``.
6. Prints the push command the maintainer needs to run.

It does NOT push. The maintainer reviews ``git log -1`` and ``git show vX.Y.Z``
before pushing, so a typo in the version or a stale CHANGELOG does not turn
into a force-rewindable mistake.
"""

from __future__ import annotations

import datetime
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
COMPARE_URL_BASE = "https://github.com/pandora-cloud/govwin-hubspot-ace/compare"
RELEASE_URL_BASE = "https://github.com/pandora-cloud/govwin-hubspot-ace/releases/tag"


def fail(msg: str) -> None:
    print(f"release: {msg}", file=sys.stderr)
    sys.exit(1)


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )
    return (result.stdout or "").strip() if capture else ""


def preflight(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        fail(f"VERSION must be semver MAJOR.MINOR.PATCH, got {version!r}")

    status = run("git", "status", "--porcelain", capture=True)
    if status:
        fail("working tree is not clean; commit or stash first")

    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD", capture=True)
    if branch != "main":
        fail(f"not on main (on {branch!r})")

    tag = f"v{version}"
    existing = run("git", "tag", "--list", tag, capture=True)
    if existing:
        fail(f"tag {tag} already exists")


def bump_pyproject(version: str) -> None:
    src = PYPROJECT.read_text()
    new, n = re.subn(
        r'^version = "[^"]*"',
        f'version = "{version}"',
        src,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        fail("could not find version line in pyproject.toml")
    PYPROJECT.write_text(new)


def update_changelog(version: str, today: str) -> None:
    src = CHANGELOG.read_text()
    tag = f"v{version}"

    # 1. Promote [Unreleased] heading to [vX.Y.Z] - YYYY-MM-DD and insert a
    #    fresh empty [Unreleased] block above it.
    new_section_heading = (
        f"## [Unreleased]\n\n"
        f"### Added\n\n"
        f"### Changed\n\n"
        f"### Fixed\n\n"
        f"## [{tag}] - {today}"
    )
    new, n = re.subn(
        r"^## \[Unreleased\]",
        new_section_heading,
        src,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        fail("could not find [Unreleased] heading in CHANGELOG.md")

    # 2. Insert a compare URL for the new tag at the bottom of the file, and
    #    repoint [Unreleased] at the new tag.
    prev_tag = previous_tag()
    new_compare_line = f"[{tag}]: {COMPARE_URL_BASE}/{prev_tag}...{tag}" if prev_tag else f"[{tag}]: {RELEASE_URL_BASE}/{tag}"
    new = re.sub(
        r"^\[Unreleased\]:.*$",
        f"[Unreleased]: {COMPARE_URL_BASE}/{tag}...HEAD",
        new,
        count=1,
        flags=re.MULTILINE,
    )
    # Place new compare line right after the [Unreleased] line.
    new = re.sub(
        r"^(\[Unreleased\]:.*)$",
        rf"\1\n{new_compare_line}",
        new,
        count=1,
        flags=re.MULTILINE,
    )

    CHANGELOG.write_text(new)


def previous_tag() -> str | None:
    out = run("git", "tag", "--sort=-v:refname", capture=True)
    tags = [t for t in out.splitlines() if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    return tags[0] if tags else None


def commit_and_tag(version: str) -> None:
    tag = f"v{version}"
    run("git", "add", str(PYPROJECT.relative_to(REPO_ROOT)), str(CHANGELOG.relative_to(REPO_ROOT)))
    run("git", "commit", "-m", f"chore(release): {tag}")
    run("git", "tag", "-a", tag, "-m", f"Release {tag}")


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: scripts/release.py X.Y.Z")
    version = sys.argv[1]
    preflight(version)
    bump_pyproject(version)
    today = datetime.date.today().isoformat()
    update_changelog(version, today)
    commit_and_tag(version)
    tag = f"v{version}"
    print()
    print(f"Release {tag} prepared locally.")
    print("Review:")
    print("    git log --stat -1")
    print(f"    git show {tag}")
    print("Push when ready:")
    print("    git push origin main --follow-tags")


if __name__ == "__main__":
    main()
