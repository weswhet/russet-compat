#!/usr/bin/env python3
"""Isolated reference comparisons for native community builders.

The optional framework build runs only on a GitHub-hosted macOS job. It never
installs Python or packages globally: pip runs from the artifact being built.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

from compat_paths import COMMUNITY_SOURCE, COMPATIBILITY, REFERENCE_CODE

SOURCES = {"AutoPkgSourceFinder": "AutoPkg/AutoPkgSourceFinder.py",
           "MakeCatalogsProcessor": "Munki/MakeCatalogsProcessor.py"}


def worker(name):
    sys.path.insert(0, str(REFERENCE_CODE))
    with contextlib.redirect_stdout(sys.stderr):
        spec = importlib.util.spec_from_file_location(name, COMMUNITY_SOURCE / SOURCES[name])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if name == "MakeCatalogsProcessor":
            original = module.subprocess.Popen
            def redirected(args, **kwargs):
                return original([os.environ["REFERENCE_MAKECATALOGS"]] + args[1:], **kwargs)
            module.subprocess.Popen = redirected
            module.get_pref = lambda key: None
        processor = getattr(module, name)()
        processor.env = plistlib.loads(sys.stdin.buffer.read())
        try:
            result = processor.process()
        except Exception as error:
            print(str(error), file=sys.stderr)
            return 10
    plistlib.dump(result, sys.stdout.buffer)
    return 0


def normalized(value, root):
    if isinstance(value, str):
        return value.replace(str(root), "<ROOT>")
    if isinstance(value, list):
        return [normalized(item, root) for item in value]
    if isinstance(value, dict):
        return {key: normalized(item, root) for key, item in value.items()}
    return value


def snapshot(repo):
    result = {}
    for path in sorted(repo.rglob("*")):
        if path.is_file():
            raw = path.read_bytes()
            try:
                result[str(path.relative_to(repo))] = plistlib.loads(raw)
            except Exception:
                result[str(path.relative_to(repo))] = raw
    return result


def setup(root, case):
    if case.startswith("source-"):
        source = root / "source"
        source.mkdir()
        if case == "source-match":
            (source / "autopkg-autopkg-abc123").mkdir()
        return "AutoPkgSourceFinder", {"input_path": str(source)}
    repo = root / "repo"
    for name in ["pkgs", "pkgsinfo", "catalogs", "icons"]:
        (repo / name).mkdir(parents=True)
    item = {"name": "Example", "version": "1", "installer_type": "nopkg",
            "catalogs": ["testing"], "notes": "private", "_metadata": {"private": True}}
    if case == "catalog-missing-payload":
        item.pop("installer_type")
        item["installer_item_location"] = "missing.pkg"
    if case == "catalog-case-payload":
        item.pop("installer_type")
        item["installer_item_location"] = "example.pkg"
        (repo / "pkgs/Example.pkg").write_bytes(b"package")
    if case == "catalog-no-catalogs":
        item.pop("catalogs")
    if case == "catalog-empty-catalogs":
        item["catalogs"] = []
    if case == "catalog-empty-notes":
        item["notes"] = ""
    (repo / "pkgsinfo/example.plist").write_bytes(plistlib.dumps(item))
    (repo / "pkgsinfo/.ignored").write_bytes(b"invalid hidden plist")
    (repo / "catalogs/stale").write_bytes(plistlib.dumps([]))
    (repo / "icons/example.png").write_bytes(b"icon")
    return "MakeCatalogsProcessor", {"MUNKI_REPO": str(repo), "force_rebuild": case != "catalog-skip"}


def live_framework(rust, root):
    if sys.platform != "darwin" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("--live-framework requires a GitHub Actions macOS runner")
    root.mkdir(parents=True)
    requirements = root / "requirements.txt"
    requirements.write_text("certifi==2025.10.5\n")
    inputs = {"RECIPE_CACHE_DIR": str(root / "cache"), "requirements_path": str(requirements),
              "python_version": "3.11.9", "os_version": "11", "upgrade_pip": True,
              "relocatable_python_sha": "8ee72fe3a5dbef733365370ebf44f25022b895ef"}
    run = subprocess.run([str(rust), "processor-run", "GenerateRelocatablePython"],
                         input=plistlib.dumps(inputs), capture_output=True, timeout=1800)
    if run.returncode:
        raise RuntimeError(run.stderr.decode(errors="replace"))
    framework = Path(plistlib.loads(run.stdout)["python_path"])
    relocated = root / "relocated/Python.framework"
    relocated.parent.mkdir()
    shutil.move(str(framework), relocated)
    python = relocated / "Versions/3.11/bin/python3.11"
    subprocess.run([str(python), "-s", "-c", "import ssl,certifi,urllib.request; urllib.request.urlopen('https://example.com', timeout=15).close()"], check=True, timeout=30)
    subprocess.run([str(relocated / "Versions/3.11/bin/pip3"), "--version"], check=True, timeout=30)
    print("PASS framework build, relocated HTTPS, and relocated pip executable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--makecatalogs", type=Path)
    parser.add_argument("--live-framework", action="store_true")
    args = parser.parse_args()
    rust = args.rust.resolve()
    contract = json.loads((COMPATIBILITY / "community-processors.json").read_text())
    for name, relative in SOURCES.items():
        source = COMMUNITY_SOURCE / relative
        if hashlib.sha256(source.read_bytes()).hexdigest() != contract["sources"][name]["sha256"]:
            parser.error(f"Pinned source changed: {name}")
    cases = ["source-match", "source-no-match"]
    if args.makecatalogs:
        cases += ["catalog-build", "catalog-skip", "catalog-missing-payload", "catalog-case-payload",
                  "catalog-no-catalogs", "catalog-empty-catalogs", "catalog-empty-notes"]
    else:
        print("SKIP native Munki comparisons: provide --makecatalogs for Munki 7.2.0.5787")
    failures = []
    with tempfile.TemporaryDirectory(prefix="russet-builders-") as scratch:
        scratch = Path(scratch)
        for case in cases:
            records = []
            for kind in ["python", "rust"]:
                root = scratch / case / kind
                root.mkdir(parents=True)
                name, inputs = setup(root, case)
                command = ([args.python, str(Path(__file__).resolve()), "--worker", name] if kind == "python"
                           else [str(rust), "processor-run", name])
                process = subprocess.run(command, input=plistlib.dumps(inputs), capture_output=True,
                                         timeout=60, env=dict(os.environ, HOME=str(root), CFFIXED_USER_HOME=str(root),
                                                              REFERENCE_MAKECATALOGS=str(args.makecatalogs or "")))
                records.append((process.returncode,
                                normalized(plistlib.loads(process.stdout), root) if process.returncode == 0 else None,
                                snapshot(root / "repo")))
                if process.returncode:
                    print(process.stderr.decode(errors="replace"))
            if records[0] != records[1] or records[0][0] != 0:
                failures.append(case)
                print("FAIL", case, records)
            else:
                print("PASS", case)
        if args.live_framework:
            live_framework(rust, scratch / "framework")
    print(f"{len(cases) - len(failures)}/{len(cases)} builder comparisons passed")
    return bool(failures)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        sys.exit(worker(sys.argv[2]))
    sys.exit(main())
