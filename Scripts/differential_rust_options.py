#!/usr/bin/env python3
"""Compare every CLI option of the pinned AutoPkg with Russet.

Each case runs one or more commands in a fresh home with the fixtures from
`differential_rust_cli`, plus a second search folder, a second override
folder, a populated cache, and recipes that use a package and audit findings.
The suite fails when an option in `cli-parser-reference.json` has no case and
no exemption, so a new AutoPkg option can't go untested.
"""
import argparse
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile

import differential_rust_cli as cli
from compat_paths import COMPATIBILITY, ROOT, RUST_DEBUG_CLI

# Options that no case covers, with the reason.
EXEMPT = {
    ("search", "--path-only"): "search always calls api.github.com",
    ("search", "--use-token"): "search always calls api.github.com",
}

FIXTURE = "recipes/Fixture.recipe"
CHANGE_PARENT = {"edit": FIXTURE, "set": {"Description": "Changed parent"}}
MORE_OVERRIDE = ["make-override", "-d", "$ROOT/more", "More"]
TEAM_OVERRIDE = ["make-override", "--override-dir", "$ROOT/team", "Fixture"]

# Each case is a list of steps: an argument list, or an edit to a fixture.
CASES = {
    # audit
    "audit-search-dir": [["audit", "-d", "$ROOT/more", "More"]],
    "audit-override-dir": [TEAM_OVERRIDE, ["audit", "--override-dir", "$ROOT/team", "Fixture"]],
    "audit-recipe-list": [["audit", "-l", "$ROOT/recipes.txt"]],
    "audit-plist": [["audit", "--plist", "Http"]],
    "audit-json": [["audit", "-j", "Http"]],
    "audit-plist-and-json": [["audit", "-p", "-j", "Http"]],
    "audit-only-check": [["audit", "--json", "--only-check", "insecure_protocol,weak_hash", "Http"]],
    "audit-skip-check": [["audit", "--skip-check", "non_core_processor, modification_processor", "Http"]],
    "audit-only-and-skip": [["audit", "--only-check", "weak_hash", "--skip-check", "weak_hash", "Http"]],
    "audit-unknown-check": [["audit", "--only-check", "weak_hash,nonsense", "Http"]],
    "audit-list-checks": [["audit", "--list-checks"]],
    "audit-fail-on-warning": [["audit", "--fail-on", "warning", "Http"]],
    "audit-fail-on-error": [["audit", "--fail-on", "error", "Http"]],
    "audit-fail-on-unknown": [["audit", "--fail-on", "fatal", "Http"]],
    # clear-cache
    "clear-cache-verbose": [["clear-cache", "--dry-run", "-v", "all"]],
    "clear-cache-very-verbose": [["clear-cache", "-vv", "all"]],
    "clear-cache-recipe": [["clear-cache", "-v", "Fixture"]],
    "clear-cache-search-dir": [["clear-cache", "--dry-run", "-d", "$ROOT/more", "More"]],
    "clear-cache-override-dir": [TEAM_OVERRIDE, ["clear-cache", "--dry-run", "--override-dir", "$ROOT/team", "Fixture"]],
    # generate-recipe-map
    "generate-recipe-map": [["generate-recipe-map"]],
    "generate-recipe-map-search-dir": [["generate-recipe-map", "-d", "$ROOT/more"]],
    "generate-recipe-map-override-dir": [TEAM_OVERRIDE, ["generate-recipe-map", "--override-dir", "$ROOT/team"]],
    "generate-recipe-map-include-cwd": [["generate-recipe-map", "--include-cwd"]],
    # info
    "info-search-dir": [["info", "-d", "$ROOT/more", "More"]],
    "info-override-dir": [TEAM_OVERRIDE, ["info", "--override-dir", "$ROOT/team", "Fixture"]],
    "info-pull": [["info", "--pull", "Fixture"]],
    # install and run share their options
    **{f"{verb}-{label}": [[verb, *argv]] for verb in ("install", "run") for label, argv in {
        "pre-post": ["--preprocessor", "VariableSetter", "--postprocessor", "FileCreator", "Fixture"],
        "check": ["-c", "Fixture"],
        "key": ["-k", "NAME=Keyed", "--key", "EXTRA=1", "Echo"],
        "recipe-list": ["--recipe-list", "$ROOT/echo.txt"],
        "pkg": ["--pkg", "$ROOT/App.pkg", "Echo"],
        "report-plist": ["--report-plist", "$ROOT/report.plist", "Echo"],
        "verbose": ["--verbose", "Echo"],
        "quiet": ["-q", "Echo"],
        "search-dir": ["-d", "$ROOT/more", "More"],
        "override-dir": ["--override-dir", "$ROOT/overrides", "Echo"],
    }.items()},
    "run-parent-trust-failure": [["make-override", "Fixture"], CHANGE_PARENT, ["run", "Fixture"]],
    "run-ignore-parent-trust": [["make-override", "Fixture"], CHANGE_PARENT,
                                ["run", "--ignore-parent-trust-verification-errors", "Fixture"]],
    "install-ignore-parent-trust": [["make-override", "Fixture.install"], {"edit": "recipes/Fixture.install.recipe", "set": {"Description": "Changed"}},
                                    ["install", "--ignore-parent-trust-verification-errors", "Fixture"]],
    # list-recipes
    "list-recipes-paths": [["list-recipes", "-p"]],
    "list-recipes-show-all": [["make-override", "Fixture"], ["list-recipes", "--show-all", "--with-identifiers"]],
    "list-recipes-search-dir": [["list-recipes", "-d", "$ROOT/more", "--with-paths"]],
    "list-recipes-override-dir": [TEAM_OVERRIDE, ["list-recipes", "--override-dir", "$ROOT/team", "-a"]],
    # make-override
    "make-override-search-dir": [MORE_OVERRIDE],
    "make-override-override-dir": [TEAM_OVERRIDE],
    "make-override-name": [["make-override", "-n", "Renamed.recipe", "Fixture"]],
    "make-override-exists": [["make-override", "Fixture"], ["make-override", "Fixture"]],
    "make-override-force": [["make-override", "Fixture"], ["make-override", "--force", "Fixture"]],
    "make-override-pull": [["make-override", "-p", "Fixture"]],
    "make-override-deprecated": [["make-override", "Deprecated"]],
    "make-override-ignore-deprecation": [["make-override", "--ignore-deprecation", "Deprecated"]],
    "make-override-format-yaml": [["make-override", "--format", "yaml", "Fixture"]],
    "make-override-format-plist": [["make-override", "--format=plist", "Fixture"]],
    "make-override-format-unknown": [["make-override", "--format", "json", "Fixture"]],
    # new-recipe
    "new-recipe-identifier": [["new-recipe", "-i", "com.example.new", "$ROOT/New.recipe"]],
    "new-recipe-parent": [["new-recipe", "--identifier", "com.example.child", "-p", "org.test.cli", "$ROOT/New.recipe"]],
    # processor-info
    "processor-info-recipe": [["processor-info", "-r", "Fixture", "FileCreator"]],
    "processor-info-search-dir": [["processor-info", "--recipe", "More", "-d", "$ROOT/more", "FileCreator"]],
    "processor-info-override-dir": [TEAM_OVERRIDE, ["processor-info", "-r", "Fixture", "--override-dir", "$ROOT/team", "FileCreator"]],
    # update-trust-info and verify-trust-info
    "update-trust-info": [["make-override", "Fixture"], CHANGE_PARENT, ["update-trust-info", "Fixture"], ["verify-trust-info", "Fixture"]],
    "update-trust-info-search-dir": [MORE_OVERRIDE, {"edit": "more/More.recipe", "set": {"Description": "Changed"}},
                                     ["update-trust-info", "-d", "$ROOT/more", "More"], ["verify-trust-info", "-d", "$ROOT/more", "More"]],
    "update-trust-info-override-dir": [TEAM_OVERRIDE, CHANGE_PARENT, ["update-trust-info", "--override-dir", "$ROOT/team", "Fixture"],
                                       ["verify-trust-info", "--override-dir", "$ROOT/team", "Fixture"]],
    "verify-trust-info-changed": [["make-override", "Fixture"], CHANGE_PARENT, ["verify-trust-info", "Fixture"]],
    "verify-trust-info-verbose": [["make-override", "Fixture"], ["verify-trust-info", "-v", "Fixture"], CHANGE_PARENT,
                                  ["verify-trust-info", "--verbose", "Fixture"]],
    "verify-trust-info-very-verbose": [["make-override", "Fixture"], CHANGE_PARENT, ["verify-trust-info", "-vv", "Fixture"]],
    "verify-trust-info-recipe-list": [["make-override", "Fixture"], ["verify-trust-info", "-l", "$ROOT/recipes.txt"]],
    "verify-trust-info-search-dir": [MORE_OVERRIDE, ["verify-trust-info", "-d", "$ROOT/more", "More"]],
    "verify-trust-info-override-dir": [TEAM_OVERRIDE, ["verify-trust-info", "--override-dir", "$ROOT/team", "-v", "Fixture"]],
}


def extend(root):
    """Adds the fixtures the option cases need to a `cli.setup` home."""
    def recipe(path, identifier, process, **extra):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(plistlib.dumps({"Identifier": identifier, "Input": {"NAME": "Fixture"}, "Process": process, **extra}))
    echo = [{"Processor": "FileCreator", "Arguments": {"file_path": "%RECIPE_DIR%/../created.txt",
                                                        "file_content": "%NAME% %PKG% %EXTRA%"}}]
    recipe("recipes/Echo.recipe", "org.test.echo", echo, Input={"NAME": "Echo", "PKG": "", "EXTRA": ""})
    recipe("recipes/Echo.install.recipe", "org.test.echo.install", echo, Input={"NAME": "Echo", "PKG": "", "EXTRA": ""})
    recipe("more/More.recipe", "org.test.more", [{"Processor": "FileCreator", "Arguments": {
        "file_path": "%RECIPE_DIR%/../more.txt", "file_content": "more"}}])
    recipe("more/More.install.recipe", "org.test.more.install", [])
    recipe("Cwd.recipe", "org.test.cwd", [])
    recipe("recipes/Http.recipe", "org.test.http", [
        {"Processor": "URLDownloader", "Arguments": {"url": "http://example.invalid/App.dmg"}},
        {"Processor": "FileCreator", "Arguments": {"file_path": "../../etc/x", "file_content": "%NAME%"}},
        {"Processor": "com.example.shared/SharedThing"},
    ], Input={"NAME": "Http", "DOWNLOAD_URL": "http://example.invalid/", "API_PASSWORD": "hunter2",
              "SHA1": "da39a3ee5e6b4b0d3255bfef95601890afd80709"})
    (root / "team").mkdir()
    (root / "App.pkg").write_bytes(b"package")
    (root / "echo.txt").write_text("Echo\n")
    for path in ("cache/org.test.cli/downloads/App.dmg", "cache/org.test.more/downloads/More.zip", "cache/stray.txt"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"cached")


def snapshot(root, outcome):
    """Records the files that the shared harness doesn't collect."""
    files = {}
    for folder in ("team", "cache", "more"):
        for path in sorted((root / folder).rglob("*")):
            if path.is_dir() or "receipts" in path.parts or path.name == "autopkg_results.plist":
                continue
            relative = path.relative_to(root).as_posix()
            if path.suffix == ".recipe":
                text = path.read_bytes().replace(str(root).encode(), b"$ROOT")
                files[relative] = plistlib.loads(text) if text.startswith(b"<?xml") else text.decode()
            else:
                files[relative] = path.stat().st_size
    outcome["files"] = files
    return outcome


def run_case(command, steps, root, executable=None):
    env = cli.setup(root)
    extend(root)
    outcomes = []
    for step in steps:
        if isinstance(step, dict):
            path = root / step["edit"]
            path.write_bytes(plistlib.dumps({**plistlib.loads(path.read_bytes()), **step["set"]}))
            continue
        argv = step[:1] + ["--prefs", "$ROOT/prefs.plist"] + step[1:]
        result = cli.invoke(command, argv, root, executable, case="option", prepared_env=env)
        if "created.txt" in result:
            result["created.txt"] = result["created.txt"].replace(str(root), "$ROOT")
        outcomes.append(snapshot(root, result))
        for name in ("report.plist", "created.txt", "New.recipe"):
            (root / name).unlink(missing_ok=True)
    return outcomes


def flags(argument):
    """Returns the option flags an argument uses, expanding short clusters."""
    if argument.startswith("--"):
        return [argument.split("=")[0]]
    if argument.startswith("-") and len(argument) > 1:
        return ["-" + letter for letter in argument[1:]]
    return []


def uncovered():
    """Lists each reference option that no case uses and no exemption names."""
    parsers = json.loads((COMPATIBILITY / "cli-parser-reference.json").read_text())["parsers"]
    used = {}
    fixtures = [case["arguments"] for case in json.loads((COMPATIBILITY / "cli-fixtures.json").read_text())]
    for argv in fixtures + [step for steps in CASES.values() for step in steps if isinstance(step, list)]:
        for argument in argv[1:]:
            used.setdefault(argv[0], set()).update(flags(argument))
    missing = []
    for verb, parser in parsers.items():
        for option in parser["options"]:
            names = option["flags"]
            if {"-h", "--help", "--prefs"} & set(names) or (verb, names[-1]) in EXEMPT:
                continue
            seen = used.get(verb, set())
            # optparse accepts any unambiguous prefix of a long option.
            if not any(name in seen or any(name.startswith(u) and u.startswith("--") for u in seen) for name in names):
                missing.append(f"{verb} {names[-1]}")
    return missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--rust", type=Path, default=RUST_DEBUG_CLI)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--case", help="Run case names matching this regular expression")
    args = parser.parse_args()
    missing = uncovered()
    for name in missing:
        print("UNCOVERED", name)
    for (verb, option), reason in EXEMPT.items():
        print("EXEMPT", verb, option, f"({reason})")
    python = str(Path(args.python).absolute()) if "/" in args.python else args.python
    version = subprocess.check_output([python, "-c", "import platform; print(platform.python_version())"], text=True).strip()
    if version != cli.REFERENCE_PYTHON_VERSION:
        parser.error(f"Use --python with the reference interpreter, Python {cli.REFERENCE_PYTHON_VERSION}; got {version}.")
    cli.REFERENCE_PYTHON = python
    cases = {name: steps for name, steps in CASES.items() if not args.case or re.search(args.case, name)}
    failures, outcomes = [], {}
    with tempfile.TemporaryDirectory(prefix="autopkg-option-differential-") as temporary:
        temp = Path(temporary).resolve()
        command = cli.reference_command(temp, python)
        executable = str(args.rust.resolve())
        for index, (name, steps) in enumerate(cases.items()):
            expected = run_case(command, steps, temp / f"python-{index}")
            actual = run_case(["autopkg"], steps, temp / f"native-{index}", executable)
            outcomes[name] = {"python": expected, "rust": actual}
            if len(expected) == len(actual) and all(cli.equivalent(name, e, a) for e, a in zip(expected, actual)):
                print("PASS", name)
            else:
                failures.append(name)
                print("FAIL", name, json.dumps(outcomes[name]))
    if args.results:
        args.results.write_text(json.dumps(outcomes, indent=2) + "\n")
    print(f"{len(outcomes) - len(failures)}/{len(outcomes)} option cases passed; {len(missing)} options uncovered")
    return bool(failures or missing)


if __name__ == "__main__":
    sys.exit(main())
