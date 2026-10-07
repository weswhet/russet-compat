#!/usr/bin/env python3
"""Compare native macOS processors against pinned Python in isolated fixtures.

Generated package/image containers have nondeterministic creation timestamps and
filesystem UUIDs. Compare their extracted logical contents; preserve byte hashes
for every ordinary file, including the immutable input containers.
"""
import argparse
import io
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

import differential_rust_processors as harness


def command(*args):
    return subprocess.check_output([str(a) for a in args], stderr=subprocess.PIPE, timeout=120)


def logical_archive(path, kind):
    with tempfile.TemporaryDirectory(prefix="autopkg-logical-archive-") as temporary:
        root = Path(temporary) / "contents"
        if kind == "pkg":
            command("/usr/sbin/pkgutil", "--expand", path, root)
            return harness.snapshot(root)
        root.mkdir()
        attached = False
        try:
            command("/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", root, path)
            attached = True
            # The filesystem journal is created by mounting, not recipe content.
            return {k: v for k, v in harness.snapshot(root).items()
                    if k.split("/", 1)[0] not in {".fseventsd", ".Trashes", ".Spotlight-V100"}}
        finally:
            if attached:
                command("/usr/bin/hdiutil", "detach", root)


def run(case, root, invocation):
    result, error = harness.run_case(case, root, invocation)
    if case.get("normalize_mounts"):
        # hdiutil -mountrandom creates a different mount directory on each run.
        # Normalize only this documented temporary prefix, retaining inner paths.
        def mounted(value):
            if isinstance(value, str):
                return re.sub(r"/private/tmp/(?:autopkg-mount-[^/\s]+/)?dmg\.[^/\s]+", "$MOUNT", value)
            if isinstance(value, dict): return {key: mounted(item) for key, item in value.items()}
            if isinstance(value, list): return [mounted(item) for item in value]
            return value
        result = mounted(result)
    if result["status"] == 0:
        for name, kind in case.get("logical_archives", {}).items():
            result["files"][name] = {"kind": kind, "contents": logical_archive(root / name, kind)}
    return result, error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--rust", type=Path, default=harness.RUST_DEBUG_CLI)
    parser.add_argument("--coverage-output", type=Path)
    parser.add_argument("--case", help="Run matching fixture names")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Native processor fixtures require macOS")
    command(args.python, "-c", "import appdirs, yaml, certifi, lxml, xattr, CoreFoundation, Foundation")
    failures = 0
    evidence = []
    with tempfile.TemporaryDirectory(prefix="autopkg-native-differential-") as temporary:
        root = Path(temporary)
        source = root / "immutable"
        source.mkdir()
        app = source / "image/Fixture.app/Contents"
        app.mkdir(parents=True)
        (app / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "org.autopkg.fixture", "CFBundleShortVersionString": "2.3",
            "CFBundleVersion": "23", "CFBundleName": "Fixture"}))
        payload = source / "payload"
        payload.mkdir()
        (payload / "Unicode-é.txt").write_text("fixture payload\n")
        command("/usr/bin/pkgbuild", "--root", payload, "--identifier", "org.autopkg.fixture",
                "--version", "2.3", "--install-location", "/Applications/Fixture", source / "input.pkg")
        command("/usr/sbin/pkgutil", "--expand", source / "input.pkg", source / "expanded")
        shutil.copy2(source / "input.pkg", source / "image/Install Fixture.pkg")
        shutil.copy2(source / "input.pkg", source / "image/Other Fixture.pkg")
        command("/usr/bin/hdiutil", "create", "-srcfolder", source / "image", "-fs", "HFS+",
                "-format", "UDZO", "-volname", "AutoPkg Fixture", source / "input.dmg")
        bundle = source / "bundle.pkg/Contents"
        bundle.mkdir(parents=True)
        (bundle / "Info.plist").write_bytes(plistlib.dumps({"IFPkgFlagDefaultLocation": "/Applications/Fixture"}))
        command("/usr/bin/ditto", "-c", "-z", payload, bundle / "Archive.pax.gz")
        cases = [
            dict(name="flat package packing", processor="FlatPkgPacker", environment={
                "source_flatpkg_dir": "$ROOT/expanded", "destination_pkg": "$ROOT/output.pkg"},
                copy_files={"expanded": str(source / "expanded")}, logical_archives={"output.pkg": "pkg"}),
            dict(name="flat package unpacking", processor="FlatPkgUnpacker", environment={
                "flat_pkg_path": "$ROOT/input.pkg", "destination_path": "$ROOT/output"},
                copy_files={"input.pkg": str(source / "input.pkg")}),
            dict(name="flat package mounted glob", processor="FlatPkgUnpacker", environment={
                "flat_pkg_path": "$ROOT/input.dmg/Install*.pkg", "destination_path": "$ROOT/output"},
                copy_files={"input.dmg": str(source / "input.dmg")}, normalize_mounts=True),
            dict(name="flat package mounted missing", processor="FlatPkgUnpacker", status=10, environment={
                "flat_pkg_path": "$ROOT/input.dmg/Missing*.pkg", "destination_path": "$ROOT/output"},
                copy_files={"input.dmg": str(source / "input.dmg")}, normalize_mounts=True),
            dict(name="flat package mounted ambiguous", processor="FlatPkgUnpacker", status=10, environment={
                "flat_pkg_path": "$ROOT/input.dmg/*.pkg", "destination_path": "$ROOT/output"},
                copy_files={"input.dmg": str(source / "input.dmg")}, normalize_mounts=True),
            dict(name="flat package skip payload", processor="FlatPkgUnpacker", environment={
                "flat_pkg_path": "$ROOT/input.pkg", "destination_path": "$ROOT/output", "skip_payload": True},
                copy_files={"input.pkg": str(source / "input.pkg")}),
            dict(name="bundle package extraction", processor="PkgExtractor", environment={
                "pkg_path": "$ROOT/input.pkg", "extract_root": "$ROOT/output"},
                copy_files={"input.pkg": str(source / "bundle.pkg")}),
            dict(name="package payload unpacking", processor="PkgPayloadUnpacker", environment={
                "pkg_payload_path": "$ROOT/Payload", "destination_path": "$ROOT/output"},
                copy_files={"Payload": str(source / "expanded/Payload")}),
            dict(name="app image version", processor="AppDmgVersioner", environment={"dmg_path": "$ROOT/input.dmg"},
                copy_files={"input.dmg": str(source / "input.dmg")}),
            dict(name="image creation defaults", processor="DmgCreator", environment={
                "dmg_root": "$ROOT/image", "dmg_path": "$ROOT/output.dmg"},
                copy_files={"image": str(source / "image")}, logical_archives={"output.dmg": "dmg"}),
        ]
        cases.extend([
            dict(name="native ZIP archive", processor="Unarchiver", environment={
                "archive_path": "$ROOT/input.zip", "destination_path": "$ROOT/output", "USE_PYTHON_NATIVE_EXTRACTOR": True, "RECIPE_CACHE_DIR": "$ROOT", "NAME": "Fixture"},
                zip_archives={"input.zip": {"hello.txt": "fixture"}}),
            dict(name="copy from mounted image", processor="Copier", environment={
                "source_path": "$ROOT/input.dmg/Fixture.app/Contents/Info.plist", "destination_path": "$ROOT/Info.plist"},
                copy_files={"input.dmg": str(source / "input.dmg")}, normalize_mounts=True),
            dict(name="find in mounted image", processor="FileFinder", environment={
                "pattern": "$ROOT/input.dmg/*.app"},
                copy_files={"input.dmg": str(source / "input.dmg")}, normalize_mounts=True),
            dict(name="package copy", processor="PkgCopier", environment={
                "source_pkg": "$ROOT/input.pkg", "pkg_path": "$ROOT/output.pkg"},
                copy_files={"input.pkg": str(source / "input.pkg")}),
            dict(name="cached package creator", processor="PkgCreator", environment={
                "pkg_request": {"pkgroot": "$ROOT/payload", "pkgdir": "$ROOT", "pkgname": "input", "pkgtype": "flat", "id": "org.autopkg.fixture", "version": "2.3", "infofile": "", "scripts": ""},
                "RECIPE_CACHE_DIR": "$ROOT"},
                copy_files={"input.pkg": str(source / "input.pkg"), "payload": str(payload)}),
            dict(name="cached app package creator", processor="AppPkgCreator", environment={
                "app_path": "$ROOT/Fixture.app", "pkg_path": "$ROOT/input.pkg", "RECIPE_CACHE_DIR": "$ROOT"},
                copy_files={"input.pkg": str(source / "input.pkg"), "Fixture.app": str(source / "image/Fixture.app")}),
            dict(name="package info restart warning", processor="PkgInfoCreator", environment={
                "template_path": "$ROOT/template.plist", "pkgtype": "flat", "version": "2.3", "pkgroot": "$ROOT/payload", "infofile": "$ROOT/PackageInfo"},
                files={"template.plist": {"IFPkgFlagRestartAction": "UnexpectedAction"}},
                copy_files={"payload": str(payload)}),
            dict(name="installer skipped package", processor="Installer", environment={
                "pkg_path": "$ROOT/input.pkg", "new_package_request": False}),
            dict(name="image installation skipped", processor="InstallFromDMG", environment={
                "dmg_path": "$ROOT/input.dmg", "items_to_copy": [], "download_changed": False}),
            dict(name="Apple code signature debug", processor="CodeSignatureVerifier", environment={
                "input_path": "/usr/bin/true", "requirement": "anchor apple", "CODE_SIGNATURE_VERIFICATION_DEBUG": True}),
            dict(name="Apple code signature defaults", processor="CodeSignatureVerifier", environment={
                "input_path": "/usr/bin/true", "requirement": "anchor apple", "deep_verification": False, "strict_verification": None}),
        ])
        cases.extend([
            dict(name="invalid flat package", processor="FlatPkgUnpacker", status=10,
                 environment={"flat_pkg_path":"$ROOT/broken.pkg", "destination_path":"$ROOT/output"}, files={"broken.pkg":"not a package"}),
            dict(name="invalid package payload", processor="PkgPayloadUnpacker", status=10,
                 environment={"pkg_payload_path":"$ROOT/Payload", "destination_path":"$ROOT/output"}, files={"Payload":"not an archive"}),
            dict(name="invalid mounted image", processor="AppDmgVersioner", status=10,
                 environment={"dmg_path":"$ROOT/broken.dmg"}, files={"broken.dmg":"not a disk image"}),
            dict(name="invalid app package input", processor="AppPkgCreator", status=10,
                 environment={"RECIPE_CACHE_DIR":"$ROOT"}),
        ])
        cases = [dict(case, name=case["name"] + f" verbosity {verbosity}",
                      environment=dict(case["environment"], verbose=verbosity), compare_logs=True)
                 for case in cases for verbosity in (0, 1, 4)]
        if args.case:
            cases = [case for case in cases if re.search(args.case, case["name"])]
        reference = root / "reference"
        reference.mkdir()
        archive = harness.reference_archive()
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle_archive:
            bundle_archive.extractall(reference)
        python = [str(Path(args.python).absolute()), str(Path(harness.__file__).resolve()), "--worker", str(reference / "Code")]
        rust = [str(args.rust.resolve()), "processor-run"]
        for index, case in enumerate(cases):
            expected, py_error = run(case, root / f"python-{index}", python)
            actual, rs_error = run(case, root / f"rust-{index}", rust)
            passed = expected == actual and expected["status"] == case.get("status", 0)
            evidence.append({"name": case["name"], "processor": case["processor"], "passed": passed})
            print(f"{'PASS' if passed else 'FAIL'} {case['name']}", flush=True)
            if not passed:
                failures += 1
                print(json.dumps({"Python": expected, "Rust": actual}, indent=2, default=str))
                print(f"Python stderr: {py_error}\nRust stderr: {rs_error}")
    if args.coverage_output:
        args.coverage_output.write_text(json.dumps({"reference_commit": harness.REFERENCE, "cases": evidence}, indent=2) + "\n")
    print(f"{len(cases) - failures}/{len(cases)} native processor cases passed.")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
