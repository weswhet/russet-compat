#!/usr/bin/env python3
"""Compare all seven Munki processor entrypoints in separate fixture repositories.

Requires macOS, Python 3.11.9, the pinned Munki 7.2.0.5787 tools and accompanying Python
libraries, and a development Python with PyObjC Cocoa/Quartz/SystemConfiguration/
LaunchServices.
No packages are installed, no catalogs are rebuilt, and no preferences are set.
"""
import argparse
import copy
import io
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

import differential_rust_processors as portable
from differential_rust_native import logical_archive

from compat_paths import COMPATIBILITY, ROOT, RUST_DEBUG_CLI

VERSION = "7.2.0.5787"
MUNKI_COMMIT = "8896fe831e870732aac760f76566762fc35d5d00"


def normalize_metadata(value):
    if isinstance(value, dict):
        result = {k: normalize_metadata(v) for k, v in value.items()}
        if isinstance(result.get("_metadata"), dict) and "creation_date" in result["_metadata"]:
            result["_metadata"]["creation_date"] = "$CREATION_DATE"
        return result
    if isinstance(value, list):
        return [normalize_metadata(v) for v in value]
    return value


def processor_log(text, root):
    # The isolated empty reference preference domain emits this startup message
    # before importing the selected processor. It is not processor output.
    prefix = "WARNING: Did not load any default preferences.\n"
    if text.startswith(prefix): text = text[len(prefix):]
    return portable.normalize(text, root)


def run_case(case, root, command):
    result, errors = portable.run_case(case, root, command)
    if result["status"] == 0 and case.get("logical_images"):
        # Read-write mounts update filesystem journals/headers independently.
        # Require exact copied bytes within each run, then compare volume contents
        # across runs using the same readonly inspection as the native gate.
        paths = case["logical_images"]
        original_hash = result["files"][paths[0]]["sha256"]
        if any(result["files"][path]["sha256"] != original_hash for path in paths):
            raise AssertionError("Importer changed writable image bytes while copying")
        contents = logical_archive(root / paths[0], "dmg")
        for path in paths:
            del result["files"][path]["sha256"]
            result["files"][path]["contents"] = contents
    if case.get("compare_logs"):
        result["processor_log"] = result.get("processor_logs", processor_log(errors, root))
    runs = [result]
    if case.get("repeat") and result["status"] == 0:
        # Catalog building is external to AutoPkg's deprecated catalog builder.
        # Populate only this implementation's isolated fixture from its import.
        info = portable.replace(result["environment"]["munki_info"], root)
        (root / "repo/catalogs/all").write_bytes(plistlib.dumps([info]))
        process_env = {**os.environ, "HOME": str(root.parent / (root.name + "-home")),
                       "CFFIXED_USER_HOME": str(root.parent / (root.name + "-home"))}
        repeated = subprocess.run(command + [case["processor"]],
                                  input=plistlib.dumps(portable.replace(case["environment"], root)),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  cwd=root, env=process_env, timeout=90)
        output = portable.normalize(plistlib.loads(repeated.stdout), root) if repeated.returncode == 0 else None
        repeated_result = {"status": repeated.returncode, "environment": output, "files": portable.snapshot(root)}
        if case.get("compare_logs"):
            repeated_result["processor_log"] = repeated_result.get("processor_logs", processor_log(repeated.stderr.decode(errors="replace"), root))
        runs.append(repeated_result)
        errors += repeated.stderr.decode(errors="replace")
    return normalize_metadata(runs), errors


def make_package(temp):
    payload = temp / "payload"
    app = payload / "Applications/Fixture.app/Contents"
    (app / "Resources").mkdir(parents=True)
    (app / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleName": "Fixture", "CFBundleIdentifier": "org.autopkg.munki-fixture",
        "CFBundleShortVersionString": "1.2", "CFBundleVersion": "42",
        "CFBundleIconFile": "Fixture.icns", "LSMinimumSystemVersion": "12.0",
    }))
    shutil.copyfile("/System/Applications/Calculator.app/Contents/Resources/AppIcon.icns", app / "Resources/Fixture.icns")
    subprocess.run(["/usr/bin/sips", "-s", "format", "png", str(app / "Resources/Fixture.icns"),
                    "--out", str(temp / "existing-icon.png")], check=True, capture_output=True)
    package = temp / "fixture.pkg"
    subprocess.run(["/usr/bin/pkgbuild", "--root", str(payload), "--identifier", "org.autopkg.munki-fixture",
                    "--version", "1.2", "--install-location", "/", str(package)], check=True, capture_output=True)
    return package


def make_images(temp, package):
    """Synthetic inputs exercise metadata inspection, never installation."""
    source = temp / "image-source"
    shutil.copytree(temp / "payload/Applications/Fixture.app", source / "Fixture.app")
    shutil.copyfile(package, source / "fixture.pkg")
    stage = temp / "stage-source/Install macOS Fixture.app/Contents"
    (stage / "Resources").mkdir(parents=True)
    (stage / "SharedSupport").mkdir()
    (stage / "Resources/startosinstall").write_text("#!/bin/sh\nexit 99\n")
    (stage / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleName": "Install macOS Fixture", "CFBundleShortVersionString": "17.0",
        "LSMinimumSystemVersion": "12.3",
    }))
    (stage / "SharedSupport/InstallInfo.plist").write_bytes(plistlib.dumps({
        "System Image Info": {"version": "12.6"},
    }))
    images = {}
    for token, directory, filename in [("$DMG", source, "fixture.dmg"),
                                        ("$WRITABLE", source, "writable.dmg"),
                                        ("$STAGE", temp / "stage-source", "stage.dmg")]:
        image = temp / filename
        subprocess.run(["/usr/bin/hdiutil", "create", "-srcfolder", str(directory),
                        "-format", "UDRW" if token == "$WRITABLE" else "UDZO", str(image)],
                       check=True, capture_output=True, timeout=90)
        images[token] = str(image)
    return images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust", type=Path, default=RUST_DEBUG_CLI)
    parser.add_argument("--python", default=sys.executable, help="Python 3.11.9 with reference dependencies")
    parser.add_argument("--fixtures", type=Path, default=COMPATIBILITY / "munki-fixtures.json")
    parser.add_argument("--coverage-output", type=Path)
    parser.add_argument("--case", help="Run case names matching a regular expression")
    args = parser.parse_args()
    # Fixture workers change cwd; preserve the virtualenv path without resolving
    # its interpreter symlink to the base Python installation.
    args.python = str(Path(shutil.which(args.python) or args.python).absolute())
    if sys.platform != "darwin":
        parser.error("This differential gate requires native macOS and pinned Munki tools")
    python_version = subprocess.check_output([args.python, "-c", "import platform; print(platform.python_version())"], text=True).strip()
    if python_version != "3.11.9":
        parser.error(f"Expected release-reference Python 3.11.9, found {python_version}; pass --python PATH")
    version = subprocess.check_output(["/usr/local/munki/makepkginfo", "--version"], text=True).strip()
    if version != VERSION:
        parser.error(f"Expected makepkginfo {VERSION}, found {version}")
    library_version = plistlib.loads(Path("/usr/local/munki/munkilib/version.plist").read_bytes())
    if library_version.get("GitRevision") != MUNKI_COMMIT:
        parser.error("Installed Munki Python icon libraries do not match the pinned source commit")
    subprocess.run([args.python, "-c", "import sys; sys.path.insert(0, '/usr/local/munki'); import Quartz, SystemConfiguration; from munkilib.admin import munkiimportlib"], check=True)
    subprocess.run([sys.executable, str(ROOT / "Scripts/capture_rust_contract.py"), "--check"], check=True)
    rust = args.rust.resolve()
    cases = json.loads(args.fixtures.read_text())
    if args.fixtures.resolve() == (COMPATIBILITY / "munki-fixtures.json").resolve():
        options = json.loads((COMPATIBILITY / "munki-makepkginfo-options.json").read_text())
        required_aliases = {flag for option in options["options"] for flag in option["flags"]}
        required_aliases.update(options["implicit_framework_options"])
        tested_aliases = {case["option_alias"] for case in cases if "option_alias" in case}
        if tested_aliases != required_aliases:
            parser.error(f"Option fixtures do not match the pinned aliases: {sorted(tested_aliases ^ required_aliases)}")
    log_cases = {
        "catalog-builder-deprecated-noop", "default-catalog-empty-input", "pkginfo-merge-shallow",
        "optional-receipt-edit", "optional-receipt-no-match", "optional-receipt-empty-path",
        "installs-app-derived-version", "info-creator-default", "importer-extract-icon",
        "importer-repeat-catalog-deduplication",
    }
    originals = list(cases)
    for original in originals:
        if original["name"] not in log_cases: continue
        for verbosity in (0, 1, 2):
            case = copy.deepcopy(original)
            case["name"] += f"-verbose-{verbosity}"
            case["environment"]["verbose"] = verbosity
            case["compare_logs"] = True
            cases.append(case)
        if original["name"] == "installs-app-derived-version":
            for minimum in ("0.0", "99.0"):
                case = copy.deepcopy(original)
                case["name"] += "-previous-minimum-" + minimum
                case["environment"].update(verbose=1, minimum_os_version=minimum)
                case["compare_logs"] = True
                cases.append(case)
    if args.case: cases = [case for case in cases if re.search(args.case, case["name"])]
    manifests = json.loads((COMPATIBILITY / "reference.json").read_text())["processors"]
    coverage = {case["processor"]: {"passed": [], "failed": [], "success": 0, "failure": 0, "defaults_exercised": []} for case in cases}
    option_coverage = {}
    log_evidence = {}
    failures = 0
    with tempfile.TemporaryDirectory(prefix="autopkg-munki-entrypoints-") as directory:
        temp = Path(directory)
        package = make_package(temp)
        sources = {"$PKG": str(package), "$ICON": str(temp / "existing-icon.png")}
        if any(source in ("$DMG", "$WRITABLE", "$STAGE") for case in cases for source in case.get("copy_files", {}).values()):
            sources.update(make_images(temp, package))
        reference = temp / "reference"
        reference.mkdir()
        archive = portable.reference_archive()
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(reference)
        python = [args.python, str(ROOT / "Scripts/differential_rust_processors.py"), "--worker", str(reference / "Code")]
        for index, original in enumerate(cases):
            case = copy.deepcopy(original)
            for name, source in case.get("copy_files", {}).items():
                case["copy_files"][name] = sources.get(source, source)
            expected, py_error = run_case(case, temp / f"python-{index}", python)
            actual, rust_error = run_case(case, temp / f"rust-{index}", [str(rust), "processor-run"])
            if case.get("compare_logs"):
                log_evidence[case["name"]] = {"python": [run["processor_log"] for run in expected],
                                              "rust": [run["processor_log"] for run in actual]}
            passed = actual == expected and all(r["status"] == case.get("status", 0) for r in expected)
            if case.get("stderr_contains"):
                passed = passed and case["stderr_contains"] in py_error and case["stderr_contains"] in rust_error
            entry = coverage[case["processor"]]
            entry["passed" if passed else "failed"].append(case["name"])
            if "option_alias" in case:
                option_coverage[case["option_alias"]] = {
                    "property": case["option_property"], "case": case["name"],
                    "passed": passed, "reference_status": expected[0]["status"],
                    "rust_status": actual[0]["status"],
                }
            if passed:
                entry["success" if expected[0]["status"] == 0 else "failure"] += 1
                if expected[0]["status"] == 0:
                    defaults = {k for k, v in manifests[case["processor"]]["input_variables"].items() if "default" in v}
                    entry["defaults_exercised"] = sorted(set(entry["defaults_exercised"]) | (defaults - case["environment"].keys()))
            print(f"{'PASS' if passed else 'FAIL'} {case['name']}", flush=True)
            if not passed:
                failures += 1
                print(f"Python: {expected!r}\nRust: {actual!r}\nPython stderr: {py_error}\nRust stderr: {rust_error}")
    if args.coverage_output:
        args.coverage_output.write_text(json.dumps({"reference_commit": portable.REFERENCE,
            "reference_python": python_version, "munki_version": version, "munki_python_libraries": library_version,
            "normalizations": ["isolated fixture root", "_metadata.creation_date", "processor logs: exact leading warning from isolated empty reference preference domain",
                "explicit writable-image cases: filesystem journal/header bytes replaced by logical contents; copied bytes must equal input bytes"],
            "processors": coverage, "options": option_coverage, "processor_log_evidence": log_evidence}, indent=2) + "\n")
    print(f"{len(cases) - failures}/{len(cases)} Munki processor cases passed.")
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
