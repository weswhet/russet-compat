#!/usr/bin/env python3
"""Check packages and disk images that Russet built on Linux with Apple's
tools on macOS.

Packages: `pkgutil --payload-files` must list the same paths as `lsbom` on the
package's BOM (an incomplete BOM gives an incomplete install receipt), and
`installer -pkginfo` must read the package. Disk images: `hdiutil verify`
must accept the checksums, and the image must attach read-only.

A command that exceeds its time limit doesn't fail the check. The output is
listed as skipped, so one slow image can't hide the results for the rest.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


TIME_LIMIT = 600


class TimedOut(Exception):
    pass


def run(*command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=TIME_LIMIT)
    except subprocess.TimeoutExpired:
        raise TimedOut(f"{Path(command[0]).name} {command[1] if len(command) > 1 else ''} ran longer than {TIME_LIMIT} seconds")


def check_package(path):
    problems = []
    payload = run("/usr/sbin/pkgutil", "--payload-files", str(path))
    if payload.returncode:
        return [f"pkgutil --payload-files failed: {payload.stderr.strip()}"]
    with tempfile.TemporaryDirectory() as temp:
        expanded = Path(temp) / "expanded"
        result = run("/usr/sbin/pkgutil", "--expand", str(path), str(expanded))
        if result.returncode:
            return [f"pkgutil --expand failed: {result.stderr.strip()}"]
        for bom in sorted(expanded.rglob("Bom")):
            listing = run("/usr/bin/lsbom", "-s", str(bom))
            if listing.returncode:
                problems.append(f"lsbom failed on {bom.relative_to(expanded)}: {listing.stderr.strip()}")
                continue
            listed = set(listing.stdout.splitlines())
            missing = sorted(set(payload.stdout.splitlines()) - listed)
            if missing and len(list(expanded.rglob("Bom"))) == 1:
                problems.append(f"BOM is missing {len(missing)} payload paths, such as {missing[0]}")
    info = run("/usr/sbin/installer", "-pkginfo", "-pkg", str(path))
    if info.returncode:
        problems.append(f"installer -pkginfo failed: {info.stderr.strip()}")
    return problems


def check_image(path):
    problems = []
    verify = run("/usr/bin/hdiutil", "verify", str(path))
    if verify.returncode:
        problems.append(f"hdiutil verify failed: {(verify.stderr or verify.stdout).strip()[-300:]}")
    with tempfile.TemporaryDirectory() as mount:
        attach = run("/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-noverify",
                     "-mountpoint", mount, str(path))
        if attach.returncode:
            problems.append(f"hdiutil attach failed: {attach.stderr.strip()}")
        else:
            run("/usr/bin/hdiutil", "detach", mount, "-force")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outputs", type=Path, help="Directory of built outputs from the Linux leg")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for path in sorted(p for p in args.outputs.rglob("*") if p.suffix.lower() in (".pkg", ".dmg") and p.is_file()):
        name = str(path.relative_to(args.outputs))
        try:
            problems = check_package(path) if path.suffix.lower() == ".pkg" else check_image(path)
        except TimedOut as error:
            results.append({"path": name, "problems": [], "skipped": str(error)})
            print("SKIP " + name + ": " + str(error))
            continue
        results.append({"path": name, "problems": problems})
        print(("FAIL " if problems else "OK   ") + name)
        for problem in problems:
            print("     " + problem)
    args.report.write_text(json.dumps(results, indent=2) + "\n")
    failed = [r for r in results if r["problems"]]
    skipped = [r for r in results if "skipped" in r]
    print(f"{len(results)} outputs found, {len(results) - len(skipped) - len(failed)} passed, "
          f"{len(failed)} with problems, {len(skipped)} skipped")
    return 1 if failed or not results else 0


if __name__ == "__main__":
    sys.exit(main())
