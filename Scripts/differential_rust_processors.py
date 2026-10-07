#!/usr/bin/env python3
"""Compare portable processor fixtures with the real, isolated pinned Python code."""
import argparse
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from compat_paths import COMPATIBILITY, REFERENCE_COMMIT as REFERENCE, ROOT, RUST_DEBUG_CLI, reference_archive


def worker(code, name):
    sys.path.insert(0, code)
    # Imports can emit platform capability diagnostics before any processor runs.
    # Keep them visible on stderr, never interleaved with the plist protocol.
    with contextlib.redirect_stdout(sys.stderr):
        from autopkglib import ProcessorError
    try:
        env = plistlib.loads(sys.stdin.buffer.read())
        with contextlib.redirect_stdout(sys.stderr):
            cls = getattr(importlib.import_module("autopkglib." + name), name)
            processor = cls()
            processor.env = env
            log_path = os.environ.get("AUTOPKG_DIFFERENTIAL_LOG_FILE")
            if log_path:
                with open(log_path, "w", encoding="utf-8") as processor_log:
                    with contextlib.redirect_stdout(processor_log), contextlib.redirect_stderr(processor_log):
                        result = processor.process()
            else:
                result = processor.process()
        # Match Processor.write_output_plist: standalone plist output omits top-level None.
        plistlib.dump({k: v for k, v in result.items() if v is not None}, sys.stdout.buffer)
    except ProcessorError as error:
        print(f"ProcessorError: {error}", file=sys.stderr)
        return 10
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


def replace(value, root):
    if isinstance(value, str):
        return value.replace("$ROOT", str(root))
    if isinstance(value, dict):
        return {k: replace(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [replace(v, root) for v in value]
    return value


def normalize(value, root):
    if isinstance(value, str):
        # Windows may expand an 8.3 temporary directory in native filesystem APIs.
        # Only normalize the identified isolated root, retaining suffix separators.
        aliases = {str(root), str(root.resolve())}
        for alias in sorted(aliases, key=len, reverse=True):
            value = value.replace(alias, "$ROOT")
        return value
    if isinstance(value, dict):
        return {k: normalize(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, root) for v in value]
    return value


def fixture_digest(cases):
    """Bind captured results to fixture semantics, independent of line endings."""
    return hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def snapshot(root):
    result = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        item = {"mode": stat.S_IMODE(info.st_mode)}
        if path.is_symlink():
            item.update(kind="symlink", target=normalize(os.readlink(path), root))
        elif path.is_dir():
            item.update(kind="directory")
        else:
            data = path.read_bytes()
            try:
                if not data.startswith(b"bplist") and ET.fromstring(data).tag != "plist":
                    raise ValueError("XML is not a property list")
                item.update(kind="plist", value=normalize(plistlib.loads(data), root))
            except Exception:
                item.update(kind="file", sha256=hashlib.sha256(data).hexdigest())
        result[path.relative_to(root).as_posix()] = item
    return result


def run_case(case, root, command):
    root.mkdir()
    for name, source in case.get("copy_files", {}).items():
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if Path(source).is_dir():
            shutil.copytree(source, destination, symlinks=True)
        else:
            shutil.copyfile(source, destination)
            destination.chmod(stat.S_IMODE(Path(source).stat().st_mode))
    for name, value in case.get("files", {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, dict):
            path.write_bytes(plistlib.dumps(value))
        else:
            path.write_text(value, encoding="utf-8")
    for name in case.get("directories", []):
        (root / name).mkdir(parents=True, exist_ok=True)
    for name, target in case.get("symlinks", {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(replace(target, root))
    for name, mode in case.get("modes", {}).items():
        (root / name).chmod(int(str(mode), 8))
    for name, members in case.get("zip_archives", {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            for member, content in members.items():
                item = zipfile.ZipInfo(member, date_time=(2000, 1, 1, 0, 0, 0))
                item.external_attr = (0o100644 if not member.endswith("/") else 0o40755) << 16
                archive.writestr(item, plistlib.dumps(content) if isinstance(content, dict) else content)
    home = root.parent / (root.name + "-home")
    home.mkdir()
    process_env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "CFFIXED_USER_HOME": str(home)}
    log_path = root.parent / (root.name + "-processor.log")
    if case.get("compare_logs"):
        process_env["AUTOPKG_DIFFERENTIAL_LOG_FILE"] = str(log_path)
    env = replace(case["environment"], root)
    def contains_null(value):
        if value is None:
            return True
        if isinstance(value, dict):
            return any(contains_null(v) for v in value.values())
        if isinstance(value, list):
            return any(contains_null(v) for v in value)
        return False
    input_format = plistlib.FMT_BINARY if contains_null(env) else plistlib.FMT_XML
    result = subprocess.run(command + [case["processor"]], input=plistlib.dumps(env, fmt=input_format), stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=root, env=process_env, timeout=60)
    output = None
    if result.returncode == 0:
        output = normalize(plistlib.loads(result.stdout), root)
    outcome = {"status": result.returncode, "environment": output, "files": snapshot(root)}
    if case.get("compare_logs"):
        if log_path.exists():
            logs = log_path.read_text(encoding="utf-8")
        else:
            logs = result.stderr.decode(errors="replace").replace("\r\n", "\n")
            if result.returncode:
                # The standalone CLI appends its terminal error after operation output.
                if "ProcessorError: " in logs:
                    logs = logs.split("ProcessorError: ", 1)[0]
                else:
                    lines = logs.splitlines(keepends=True)
                    logs = "".join(lines[:-1])
        # Python exposes this otherwise opaque default sentinel's process address.
        logs = re.sub(r"(<autopkglib\.ChocolateyPackager\.VariableSentinel object at )0x[0-9a-fA-F]+(>)", r"\1$ADDRESS\2", logs)
        outcome["processor_logs"] = normalize(logs, root)
    return outcome, result.stderr.decode(errors="replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust", type=Path, default=RUST_DEBUG_CLI)
    parser.add_argument("--python", default=sys.executable, help="Python with appdirs and PyYAML installed")
    parser.add_argument("--fixtures", type=Path, default=COMPATIBILITY / "portable-fixtures.json")
    parser.add_argument("--coverage-output", type=Path, help="Write per-processor executed case coverage")
    parser.add_argument("--capture-reference", action="store_true", help="Write normalized reference results without running Rust")
    parser.add_argument("--frozen-reference", type=Path,
                        help="Compare Rust with captured macOS results without importing Python processors")
    parser.add_argument("--verbose", type=int, choices=[0, 1, 2, 4],
                        help="Compare processor logs at this verbosity as well as results")
    args = parser.parse_args()
    if args.capture_reference and args.frozen_reference:
        parser.error("--capture-reference and --frozen-reference are mutually exclusive")
    if os.path.sep in args.python:
        args.python = str(Path(args.python).absolute())
    subprocess.run([sys.executable, str(ROOT / "Scripts/capture_rust_contract.py"), "--check"], check=True)
    rust = args.rust.resolve()
    if not args.capture_reference and not rust.is_file():
        parser.error(f"Rust executable missing: {rust}; build it first")
    cases = json.loads(args.fixtures.read_text(encoding="utf-8"))
    if args.verbose is not None:
        if args.capture_reference or args.frozen_reference:
            parser.error("Verbose comparison requires the live Python reference")
        for case in cases:
            case["environment"]["verbose"] = args.verbose
            case["compare_logs"] = True
    digest = fixture_digest(cases)
    frozen = None
    if args.frozen_reference:
        frozen = json.loads(args.frozen_reference.read_text(encoding="utf-8"))
        if frozen.get("reference_commit") != REFERENCE or frozen.get("fixture_sha256") != digest:
            parser.error("Frozen reference does not match the pinned commit and current fixtures")
        if frozen.get("platform") != "darwin":
            parser.error("Frozen native predicate reference must be captured on macOS")
        if any(case["processor"] != "StopProcessingIf" for case in cases):
            parser.error("Frozen reference mode is restricted to predicate fixtures")
    skipped = [c["name"] for c in cases if sys.platform not in c.get("platforms", [sys.platform])]
    cases = [c for c in cases if c["name"] not in skipped]
    for name in skipped:
        print(f"SKIP {name}: unsupported fixture platform {sys.platform}")
    failures = 0
    captured = {}
    manifests = json.loads((COMPATIBILITY / "reference.json").read_text(encoding="utf-8"))["processors"]
    coverage = {name: {"passed": [], "failed": [], "success": 0, "failure": 0, "defaults_exercised": []} for name in manifests}
    with tempfile.TemporaryDirectory(prefix="autopkg-differential-") as temp:
        temp = Path(temp)
        if frozen is None:
            reference = temp / "reference"
            reference.mkdir()
            archive = reference_archive()
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                bundle.extractall(reference)
            python = [args.python, str(Path(__file__).resolve()), "--worker", str(reference / "Code")]
            # Fail setup separately rather than mistaking import failures for processor failures.
            subprocess.run([args.python, "-c", "import appdirs, yaml, certifi, lxml, xattr; import CoreFoundation, Foundation" if sys.platform == "darwin" else "import appdirs, yaml, certifi, lxml"], check=True)
        for index, case in enumerate(cases):
            expected_status = case.get("status_by_platform", {}).get(sys.platform, case.get("status", 0))
            if frozen is None:
                expected, py_error = run_case(case, temp / f"python-{index}", python)
            else:
                expected, py_error = frozen["cases"][case["name"]], "captured macOS NSPredicate oracle"
            captured[case["name"]] = expected
            if args.capture_reference:
                if expected["status"] != expected_status:
                    raise RuntimeError(f"Reference case {case['name']} failed: {py_error}")
                continue
            actual, rust_error = run_case(case, temp / f"rust-{index}", [str(rust), "processor-run"])
            passed = actual == expected and expected["status"] == expected_status
            for key, value in case.get("expected_values", {}).items():
                passed = passed and expected["environment"] is not None and expected["environment"].get(key) == value
            entry = coverage[case["processor"]]
            entry["passed" if passed else "failed"].append(case["name"])
            if passed:
                entry["success" if expected["status"] == 0 else "failure"] += 1
                if expected["status"] == 0:
                    default_keys = {k for k, v in manifests[case["processor"]]["input_variables"].items() if "default" in v}
                    entry["defaults_exercised"] = sorted(set(entry["defaults_exercised"]) | (default_keys - case["environment"].keys()))
            print(f"{'PASS' if passed else 'FAIL'} {case['name']}")
            if not passed:
                failures += 1
                print(f"  Python: {expected!r}\n  Rust: {actual!r}\n  Python stderr: {py_error}\n  Rust stderr: {rust_error}")
    if args.capture_reference:
        output = args.fixtures.with_name(args.fixtures.stem.replace("-fixtures", "") + "-reference-results.json")
        output.write_text(json.dumps({"reference_commit": REFERENCE, "fixture_sha256": digest,
                                      "platform": sys.platform, "cases": captured}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Captured {len(cases)} reference outcomes in {output}")
        return 0
    if args.coverage_output:
        args.coverage_output.write_text(json.dumps({"reference_commit": REFERENCE, "processors": coverage}, indent=2) + "\n", encoding="utf-8")
    print(f"{len(cases) - failures}/{len(cases)} processor cases passed; this is not the complete release gate.")
    return bool(failures)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        sys.exit(worker(sys.argv[2], sys.argv[3]))
    sys.exit(main())
