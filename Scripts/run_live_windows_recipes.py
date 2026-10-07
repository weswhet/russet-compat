#!/usr/bin/env python3
"""Run pinned Firefox Windows recipes on an isolated GitHub-hosted runner."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import time
import zipfile

from compat_paths import COMPATIBILITY, RUSSET

RECIPE_COMMIT = "0f7b61ab061c77710be4140c404910a11f89fc7b"
RECIPES = ("Mozilla/FirefoxWindows.download.recipe", "Mozilla/Firefox.nupkg.recipe")


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            result.update(data)
    return result.hexdigest()


def git(directory, *arguments):
    return subprocess.check_output(["git", "-C", str(directory), *arguments],
                                   text=True, timeout=30).strip()


def tools():
    sdk = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits/10/bin"
    candidates = list(sdk.glob("*/x64/signtool.exe"))
    candidates.sort(key=lambda path: tuple(int(part) for part in path.parent.parent.name.split(".")), reverse=True)
    signtool = candidates[0] if candidates else None
    choco = shutil.which("choco.exe") or shutil.which("choco")
    if not signtool or not choco:
        raise RuntimeError("The runner must provide Windows SDK x64 signtool.exe and Chocolatey")
    return signtool.resolve(), Path(choco).resolve()


def run_one(binary, recipes, relative, output, signtool, choco, timeout):
    directory = output / Path(relative).name
    work = directory / "work"
    for name in ("home", "cache", "tmp", "config", "overrides", "repos", "appdata", "localappdata"):
        (work / name).mkdir(parents=True, exist_ok=False)
    prefs = directory / "prefs.plist"
    preferences = {
        "CACHE_DIR": str(work / "cache"), "RECIPE_SEARCH_DIRS": [str(recipes)],
        "RECIPE_OVERRIDE_DIRS": [str(work / "overrides")], "RECIPE_REPO_DIR": str(work / "repos"),
        "RECIPE_REPOS": {}, "RECIPE_MAP_PATH": str(work / "map.json"),
        "FAIL_RECIPES_WITHOUT_TRUST_INFO": False,
        "signtool_path": str(signtool), "chocoexe_path": str(choco),
    }
    prefs.write_bytes(plistlib.dumps(preferences))
    environment = {key: value for key, value in os.environ.items() if not key.startswith("AUTOPKG_")}
    environment.update(HOME=str(work / "home"), USERPROFILE=str(work / "home"),
                       APPDATA=str(work / "appdata"), LOCALAPPDATA=str(work / "localappdata"),
                       XDG_CONFIG_HOME=str(work / "config"), TMP=str(work / "tmp"),
                       TEMP=str(work / "tmp"), TMPDIR=str(work / "tmp"),
                       AUTOPKG_RS_PREFERENCES_FILE=str(prefs), AUTOPKG_RS_CACHE_DIR=str(work / "cache"),
                       RUST_BACKTRACE="1")
    report = directory / "report.plist"
    command = [str(binary), "run", "--prefs", str(prefs), "-vv", "--report-plist", str(report), str(recipes / relative)]
    record = {"recipe": relative, "recipe_sha256": digest(recipes / relative),
              "command": command, "report": str(report.relative_to(output)),
              "log": str((directory / "run.log").relative_to(output))}
    started = time.monotonic()
    try:
        with (directory / "run.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=work, env=environment,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            try:
                record["exit_status"] = process.wait(timeout=timeout)
                record["status"] = "passed" if process.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                # Stop descendant curl/choco processes before collecting evidence.
                subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                               stdout=log, stderr=subprocess.STDOUT, timeout=30, check=False)
                process.wait(timeout=30)
                record.update(status="timeout", exit_status=process.returncode)
    except (OSError, subprocess.SubprocessError) as error:
        record.update(status="launch_error", error=str(error))
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    record["artifacts"] = []
    # Preserve all generated files in CI evidence, but inventory large downloads
    # separately so consumers can verify hashes without opening an archive.
    for path in sorted((work / "cache").rglob("*")):
        if path.is_file() and path.suffix.lower() in (".msi", ".exe", ".nupkg", ".nuspec", ".ps1"):
            artifact = {"path": str(path.relative_to(output)), "size": path.stat().st_size,
                        "sha256": digest(path)}
            if path.suffix.lower() == ".nupkg":
                try:
                    with zipfile.ZipFile(path) as archive:
                        artifact["members"] = sorted(archive.namelist())
                        artifact["zip_error"] = archive.testzip()
                except (OSError, zipfile.BadZipFile) as error:
                    artifact["zip_error"] = str(error)
            record["artifacts"].append(artifact)
    if report.is_file():
        record["report_sha256"] = digest(report)
    if record["status"] == "passed":
        msi = [item for item in record["artifacts"] if item["path"].lower().endswith(".msi")]
        packages = [item for item in record["artifacts"] if item["path"].lower().endswith(".nupkg")]
        errors = []
        if not msi:
            errors.append("Successful recipe did not leave a downloaded MSI")
        if relative.endswith(".nupkg.recipe") and not packages:
            errors.append("Successful packaging recipe did not produce a nupkg")
        if any(item.get("zip_error") for item in packages):
            errors.append("Generated nupkg failed ZIP integrity verification")
        for package in packages:
            members = [name.replace("\\", "/").lower() for name in package.get("members", [])]
            if not any(name.endswith(".nuspec") for name in members) or "tools/chocolateyinstall.ps1" not in members:
                errors.append("Generated nupkg lacks its nuspec or Chocolatey installation script")
        if errors:
            record.update(status="artifact_error", artifact_errors=errors)
    (directory / "result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust", required=True, type=Path)
    parser.add_argument("--recipes", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    if sys.platform != "win32" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        parser.error("This live test runs only on GitHub-hosted Windows runners")
    if args.timeout < 1:
        parser.error("timeout must be positive")
    binary, recipes, output = args.rust.resolve(), args.recipes.resolve(), args.output.resolve()
    contract = json.loads((COMPATIBILITY / "community-processors.json").read_text())
    if contract["reference_commit"] != RECIPE_COMMIT or git(recipes, "rev-parse", "HEAD") != RECIPE_COMMIT:
        parser.error("Recipe checkout does not match the pinned community source commit")
    if git(recipes, "status", "--porcelain"):
        parser.error("Recipe checkout must be unmodified")
    output.mkdir(parents=True, exist_ok=False)
    signtool, choco = tools()
    result = {
        "schema_version": 1, "recipe_repository": "https://github.com/autopkg/recipes",
        "recipe_commit": RECIPE_COMMIT, "russet_commit": git(RUSSET, "rev-parse", "HEAD"),
        "binary_sha256": digest(binary), "os": platform.platform(),
        "runner_image": os.environ.get("ImageVersion"),
        "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "signtool": {"path": str(signtool), "sha256": digest(signtool)},
        "chocolatey": {"path": str(choco), "sha256": digest(choco),
                       "version": subprocess.check_output([str(choco), "--version"], text=True, timeout=30).strip()},
        "recipe_files": {relative: digest(recipes / relative) for relative in RECIPES},
        "results": [],
    }
    summary = output / "summary.json"
    summary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for relative in RECIPES:
        record = run_one(binary, recipes, relative, output, signtool, choco, args.timeout)
        result["results"].append(record)
        summary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"{record['status']}: {relative}", flush=True)
    if git(recipes, "status", "--porcelain"):
        raise RuntimeError("Recipe checkout changed during validation")
    if digest(binary) != result["binary_sha256"]:
        raise RuntimeError("Tested binary changed during validation")
    return int(any(record["status"] != "passed" for record in result["results"]))


if __name__ == "__main__":
    sys.exit(main())
