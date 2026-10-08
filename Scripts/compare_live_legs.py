#!/usr/bin/env python3
"""Compare same-day live recipe runs of Russet: macOS with Apple's tools,
macOS with Russet's native replacements (RUSSET_NATIVE=all), and Linux.

The macOS run with Apple's tools is the baseline. A recipe that passes there
and fails on a native leg is a regression, unless the failure is a network
diagnostic (upstream drift between runs) or, on Linux, an operation that
Russet supports only on macOS, such as installing a package.

With --macos-runs linux-failures, macOS ran only the recipes that failed on
Linux, so a recipe that passed on Linux shows `not_run` on both macOS legs.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

LEGS = ("apple", "native", "linux")
EXCUSED = {
    "native": {"network_diagnostic", "timeout"},
    "linux": {"network_diagnostic", "timeout", "unsupported_platform"},
}


def load(directory):
    """Results by recipe path from every summary.json below `directory`."""
    results = {}
    for summary in sorted(Path(directory).rglob("summary.json")):
        data = json.loads(summary.read_text())
        for result in data.get("results", []):
            results[result["path"]] = result
    return results


def outcome(result):
    if result is None:
        return "missing"
    return "passed" if result["status"] == "passed" else result["diagnostic_category"]


def last_error(result):
    for line in reversed((result or {}).get("log_tail", "").splitlines()):
        if "Error" in line or "error" in line:
            return line.strip()[:240]
    return ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for leg in LEGS:
        parser.add_argument("--" + leg, type=Path, required=True, help=f"Directory with the {leg} leg's evidence")
    parser.add_argument("--macos-runs", choices=("all", "linux-failures"), default="all",
                        help="Whether macOS ran every recipe or only the Linux failures")
    parser.add_argument("--output", type=Path, required=True, help="JSON comparison to write")
    parser.add_argument("--markdown", type=Path, required=True, help="Markdown summary to write")
    args = parser.parse_args()
    legs = {leg: load(getattr(args, leg)) for leg in LEGS}
    paths = sorted(set().union(*legs.values()))
    rows, regressions, unchecked = [], [], []
    for path in paths:
        row = {"path": path}
        for leg in LEGS:
            row[leg] = outcome(legs[leg].get(path))
        if args.macos_runs == "linux-failures" and row["linux"] in ("passed", "unsupported_platform"):
            for leg in ("apple", "native"):
                if row[leg] == "missing":
                    row[leg] = "not_run"
        if row["apple"] == "passed":
            for leg in ("native", "linux"):
                if row[leg] != "passed" and row[leg] not in EXCUSED[leg]:
                    row.setdefault("regressions", []).append(leg)
                    row[leg + "_error"] = last_error(legs[leg].get(path))
        if "regressions" in row:
            regressions.append(row)
        elif row["apple"] == "missing" and row["linux"] not in ("passed", *EXCUSED["linux"]):
            row["linux_error"] = last_error(legs["linux"].get(path))
            unchecked.append(row)
        rows.append(row)
    counts = {leg: dict(Counter(row[leg] for row in rows)) for leg in LEGS}
    args.output.write_text(json.dumps({"counts": counts, "regressions": regressions, "unchecked": unchecked, "recipes": rows}, indent=2) + "\n")

    lines = ["# Live recipes: macOS (Apple tools), macOS (native), and Linux", "",
             f"{len(paths)} recipes. The macOS run with Apple's tools is the baseline."
             + (" macOS ran only the recipes that failed on Linux." if args.macos_runs == "linux-failures" else ""), "",
             "| Outcome | macOS, Apple tools | macOS, native | Linux |", "| --- | ---: | ---: | ---: |"]
    for name in sorted(set().union(*(c.keys() for c in counts.values()))):
        lines.append(f"| {name} | " + " | ".join(str(counts[leg].get(name, 0)) for leg in LEGS) + " |")
    lines += ["", f"## Regressions ({len(regressions)})", ""]
    if regressions:
        lines += ["| Recipe | macOS, native | Linux | Last error |", "| --- | --- | --- | --- |"]
        for row in regressions:
            error = row.get("linux_error") or row.get("native_error") or ""
            lines.append(f"| `{row['path']}` | {row['native']} | {row['linux']} | {error.replace('|', '/')} |")
    else:
        lines.append("None: every recipe that passed with Apple's tools passed on both native legs, "
                     "apart from network diagnostics and macOS-only operations on Linux.")
    if unchecked:
        lines += ["", f"## Linux failures with no macOS result ({len(unchecked)})", "",
                  "| Recipe | Linux | Last error |", "| --- | --- | --- |"]
        lines += [f"| `{row['path']}` | {row['linux']} | {row['linux_error'].replace('|', '/')} |" for row in unchecked]
    excused = [row for row in rows if row["apple"] == "passed" and "regressions" not in row
               and (row["native"] != "passed" or row["linux"] != "passed")]
    if excused:
        lines += ["", f"## Excused differences ({len(excused)})", "",
                  "| Recipe | macOS, native | Linux |", "| --- | --- | --- |"]
        lines += [f"| `{row['path']}` | {row['native']} | {row['linux']} |" for row in excused]
    args.markdown.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if regressions or unchecked else 0


if __name__ == "__main__":
    sys.exit(main())
