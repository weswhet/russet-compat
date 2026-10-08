"""Locations shared by the comparison suites.

The suites compare a Russet checkout with Python AutoPkg at a pinned upstream
commit. Neither is part of this repository: `Scripts/fetch_reference.sh` puts
the reference in `reference/`, and the workflows check out Russet into
`russet/`. Set `RUSSET_ROOT` or `AUTOPKG_REFERENCE_SOURCE` to use other
locations.

This module only uses the standard library and checks nothing at import time,
because reference worker processes import the suites with the pinned Python.
"""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RUSSET = Path(os.environ.get("RUSSET_ROOT") or ROOT / "russet").resolve()
REFERENCE_SOURCE = Path(os.environ.get("AUTOPKG_REFERENCE_SOURCE") or ROOT / "reference").resolve()
REFERENCE_COMMIT = "c36e58f8d3d8ddb70b6c2d848d2ceca7f767ce5c"

# Russet owns the frozen contracts and fixtures; this repository owns the
# pinned community processor sources that the community suites import.
COMPATIBILITY = RUSSET / "compatibility"
RUST_DEBUG_CLI = RUSSET / "rust/target/debug/russet"
COMMUNITY_SOURCE = ROOT / "community-source"

# A bare repository that holds only the pinned commit, so the suites can read
# exact blobs and create fresh archives, plus the extracted `Code` tree.
REFERENCE_GIT = REFERENCE_SOURCE / "git"
REFERENCE_CODE = REFERENCE_SOURCE / "Code"


def _git(*arguments):
    if not (REFERENCE_SOURCE / "COMMIT").is_file():
        raise SystemExit(f"Python AutoPkg reference missing at {REFERENCE_SOURCE}; run Scripts/fetch_reference.sh")
    recorded = (REFERENCE_SOURCE / "COMMIT").read_text(encoding="utf-8").strip()
    if recorded != REFERENCE_COMMIT:
        raise SystemExit(f"{REFERENCE_SOURCE} holds {recorded}, not {REFERENCE_COMMIT}; run Scripts/fetch_reference.sh")
    return subprocess.check_output(["git", f"--git-dir={REFERENCE_GIT}", *arguments])


def reference_text(path):
    """Return a file from the pinned commit exactly as committed."""
    return _git("show", f"{REFERENCE_COMMIT}:{path}").decode()


def reference_archive(*paths):
    """Return a tar archive of paths at the pinned commit, as `git archive` writes it."""
    return _git("archive", REFERENCE_COMMIT, *(paths or ("Code",)))
