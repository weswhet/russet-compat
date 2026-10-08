#!/usr/bin/env python3
"""Run unchanged upstream plist and YAML recipes against Rust in isolated scratch state.

This is a live network sweep, not a sandbox. Run only reviewed recipes locally;
use disposable GitHub-hosted runners for the complete upstream inventory.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import plistlib
import signal
import shutil
import subprocess
import sys
import time
import uuid


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def git(root, *arguments):
    return subprocess.check_output(["git", "-C", str(root), *arguments], text=True).strip()


def inventory(root):
    records = []
    for path in sorted(path for path in root.rglob("*") if path.is_file() and path.name.endswith((".recipe", ".recipe.yaml", ".recipe.yml"))):
        record = {"path": path.relative_to(root).as_posix(),
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        try:
            if path.suffix in (".yaml", ".yml"):
                import yaml
                recipe = yaml.safe_load(path.read_text())
            else:
                recipe = plistlib.loads(path.read_bytes())
            record.update(identifier=recipe.get("Identifier"), parent=recipe.get("ParentRecipe"),
                          processors=[step.get("Processor") for step in recipe.get("Process", [])])
        except Exception as error:
            record["parse_error"] = str(error)
        records.append(record)
    return records


def classify(status, log):
    if status == "passed":
        return "completed"
    if status == "timeout":
        return "timeout"
    lowered = log.lower()
    # A macOS-only operation is reported before a processor boundary, since
    # either log line can appear in the same run.
    if "only supported on macos" in lowered or "requires macos" in lowered:
        return "unsupported_platform"
    if any(text in lowered for text in ("custom processor", "unknown processor", "not a built-in processor", "python processor")):
        return "unsupported_processor"
    if "parent" in lowered and any(text in lowered for text in ("not found", "could not", "unable to", "missing")):
        return "missing_parent"
    # These labels describe observed diagnostics, not whether Python would pass.
    if any(text in lowered for text in ("curl:", "http error", "status code", "certificate verify", "failed to download")):
        return "network_diagnostic"
    return "unclassified_failure"


def cleanup_mounts(work):
    """Detach only images whose backing file belongs to this recipe's scratch tree."""
    if sys.platform != "darwin":
        return []
    attempts = []
    try:
        info = plistlib.loads(subprocess.check_output(["/usr/bin/hdiutil", "info", "-plist"], timeout=20))
        for image in info.get("images", []):
            image_path = Path(image.get("image-path", "/")).resolve()
            if work.resolve() not in image_path.parents:
                continue
            devices = [entity["dev-entry"] for entity in image.get("system-entities", []) if "dev-entry" in entity]
            if devices:
                result = subprocess.run(["/usr/bin/hdiutil", "detach", "-force", devices[0]],
                                        capture_output=True, text=True, timeout=30)
                attempts.append({"image": str(image_path), "device": devices[0], "exit_status": result.returncode,
                                 "stderr": result.stderr})
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        attempts.append({"error": str(error)})
    return attempts


def prepare_reference(code, output):
    isolated = output / "reference-code"
    shutil.copytree(code, isolated)
    library = isolated / "autopkglib/__init__.py"
    source = library.read_text()
    replacements = {
        'BUNDLE_ID = "com.github.autopkg"': 'BUNDLE_ID = "org.autopkg.live-test.' + uuid.uuid4().hex + '"',
        'config_dir = appdirs.user_config_dir(APP_NAME, appauthor=False)':
            'config_dir = os.path.join(os.environ["XDG_CONFIG_HOME"], APP_NAME)',
    }
    for old, new in replacements.items():
        if source.count(old) != 1:
            raise ValueError("Pinned reference preference isolation pattern changed: " + old)
        source = source.replace(old, new)
    library.write_text(source)
    return isolated


# Built outputs larger than this aren't kept for checking on macOS.
MAX_OUTPUT_BYTES = 512 << 20


def collect_outputs(work, destination):
    """Copies packages and disk images the recipe built, as opposed to ones it
    downloaded, so another job can check them with Apple's tools."""
    kept = []
    cache = work / "cache"
    for path in sorted(cache.rglob("*")):
        relative = path.relative_to(cache)
        if "downloads" in relative.parts or path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() not in (".pkg", ".dmg") or path.stat().st_size > MAX_OUTPUT_BYTES:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        kept.append({"path": relative.as_posix(), "size": path.stat().st_size,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return kept


def run_one(binary, recipes, case, output, timeout, keep_work, reference_code=None, github_token_file=None, verbose=2, outputs=None,
            search_dirs=()):
    directory = output / (hashlib.sha256(case["path"].encode()).hexdigest()[:12])
    directory.mkdir()
    work = directory / "work"
    for name in ("cache", "overrides", "repos", "tmp", "config/Autopkg", "munki/catalogs", "munki/pkgs", "munki/pkgsinfo", "munki/icons"):
        (work / name).mkdir(parents=True, exist_ok=True)
    preferences = {"CACHE_DIR": str(work / "cache"), "RECIPE_SEARCH_DIRS": [str(recipes)] + [str(path) for path in search_dirs],
                   "RECIPE_OVERRIDE_DIRS": [str(work / "overrides")], "RECIPE_REPO_DIR": str(work / "repos"),
                   "RECIPE_REPOS": {}, "RECIPE_MAP_PATH": str(work / "map.json"),
                   "MUNKI_REPO": str(work / "munki"), "MUNKI_REPO_PLUGIN": "FileRepo",
                   "FAIL_RECIPES_WITHOUT_TRUST_INFO": False}
    prefs = directory / "prefs.plist"
    prefs.write_bytes(plistlib.dumps(preferences))
    (work / "config/Autopkg/config.plist").write_bytes(plistlib.dumps(preferences))
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("AUTOPKG_")}
    environment.update(AUTOPKG_RS_PREFERENCES_FILE=str(prefs), AUTOPKG_RS_CACHE_DIR=str(work / "cache"),
                       TMPDIR=str(work / "tmp"), TMP=str(work / "tmp"), TEMP=str(work / "tmp"),
                       RUST_BACKTRACE="1", XDG_CONFIG_HOME=str(work / "config"), PYTHONDONTWRITEBYTECODE="1")
    command = [str(binary), "run", "--prefs", str(prefs), "--report-plist", str(directory / "report.plist"),
               "--key", "MUNKI_REPO=" + str(work / "munki"), "--key", "MUNKI_REPO_PLUGIN=FileRepo",
               str(recipes / case["path"])]
    if verbose:
        command.insert(2, "-" + "v" * verbose)
    if github_token_file:
        command[2:2] = ["--key", "GITHUB_TOKEN_PATH=" + str(github_token_file)]
    if reference_code:
        bootstrap = "import runpy,sys; code=sys.argv.pop(1); sys.path.insert(0,code); sys.argv[0]=code+'/autopkg'; runpy.run_path(sys.argv[0],run_name='__main__')"
        command = [str(binary), "-c", bootstrap, str(reference_code)] + command[1:]
    started = time.monotonic()
    result = dict(case, command=command, log=str(directory / "run.log"), report=str(directory / "report.plist"))
    try:
        with (directory / "run.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                result["exit_status"] = process.wait(timeout=timeout)
                result["status"] = "passed" if process.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                # Kill the entire group, including curl/archive subprocesses.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                result.update(status="timeout", exit_status=process.returncode)
    except OSError as error:
        result.update(status="launch_error", exit_status=None, error=str(error))
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    log = (directory / "run.log").read_text(encoding="utf-8", errors="replace")
    result["diagnostic_category"] = classify(result["status"], log)
    result["log_tail"] = log[-12000:]
    result["report_present"] = (directory / "report.plist").exists()
    if result["report_present"]:
        try:
            result["report_contents"] = plistlib.loads((directory / "report.plist").read_bytes())
        except Exception as error:
            result["report_parse_error"] = str(error)
    result["source_unchanged"] = hashlib.sha256((recipes / case["path"]).read_bytes()).hexdigest() == case["sha256"]
    result["mount_cleanup"] = cleanup_mounts(work)
    if outputs is not None and result["status"] == "passed":
        result["built_outputs"] = collect_outputs(work, outputs / directory.name)
    if not keep_work:
        try:
            shutil.rmtree(work)
        except OSError as error:
            result["cleanup_error"] = str(error)
    write_json(directory / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust", type=Path, required=True)
    parser.add_argument("--recipes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--select", action="append", default=[], help="Exact relative recipe path; repeatable")
    parser.add_argument("--search-dir", action="append", type=Path, default=[],
                        help="Folder in the recipe checkout to also search for parent recipes; repeatable")
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--verbose", type=int, choices=range(5), default=2)
    parser.add_argument("--github-token-file", type=Path, help="GitHub token file outside the evidence directory")
    parser.add_argument("--reference-python", type=Path)
    parser.add_argument("--reference-code", type=Path, help="Pinned upstream Code directory; failures rerun separately")
    parser.add_argument("--collect-outputs", type=Path, help="New directory for built packages and disk images")
    args = parser.parse_args()
    if args.timeout <= 0 or args.shards < 1 or not 0 <= args.shard < args.shards:
        parser.error("Timeout and shards must be positive, and shard must be in range")
    if bool(args.reference_python) != bool(args.reference_code):
        parser.error("Reference Python and Code must be provided together")
    if args.reference_python and os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        parser.error("Python failure reruns are limited to GitHub-hosted runners")
    recipes, binary, output = args.recipes.resolve(), args.rust.resolve(), args.output.absolute()
    if not binary.is_file() or not recipes.is_dir():
        parser.error("Binary and recipe checkout must exist")
    search_dirs = [path.resolve() for path in args.search_dir]
    if not all(path.is_dir() and recipes in path.parents for path in search_dirs):
        parser.error("Search folders must be inside the recipe checkout")
    token_file = args.github_token_file.resolve() if args.github_token_file else None
    if token_file and (not token_file.is_file() or token_file == output.resolve() or output.resolve() in token_file.parents):
        parser.error("GitHub token file must exist outside the evidence directory")
    if output.exists():
        parser.error("Output must be a new directory")
    commit = git(recipes, "rev-parse", "HEAD")
    if git(recipes, "status", "--porcelain"):
        parser.error("Recipe checkout must be clean")
    cases = inventory(recipes)
    missing = set(args.select) - {case["path"] for case in cases}
    if missing:
        parser.error("Unknown recipe paths: " + ", ".join(sorted(missing)))
    eligible = [case for case in cases if not args.select or case["path"] in args.select]
    selected = [case for index, case in enumerate(eligible) if index % args.shards == args.shard]
    output.mkdir(parents=True)
    outputs = args.collect_outputs.absolute() if args.collect_outputs else None
    if outputs:
        outputs.mkdir(parents=True)
    reference_code = prepare_reference(args.reference_code.resolve(), output) if args.reference_code else None
    reference_output = output / "reference-results"
    reference_output.mkdir()
    write_json(output / "inventory.json", {"recipe_commit": commit, "recipes": cases})
    summary = {"recipe_commit": commit, "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
               "shard": args.shard, "shards": args.shards, "timeout_seconds": args.timeout,
               "platform": sys.platform, "russet_native": os.environ.get("RUSSET_NATIVE", ""),
               "authenticated_github": bool(token_file), "inventory_count": len(cases), "selected_count": len(selected), "results": [],
               "reference_comparison": "failed built-in runs only; independent downloads, no output equivalence claim" if reference_code else "not performed", "complete": False}
    write_json(output / "summary.json", summary)
    for index, case in enumerate(selected):
        result = run_one(binary, recipes, case, output, args.timeout, args.keep_work, github_token_file=token_file,
                         verbose=args.verbose, outputs=outputs, search_dirs=search_dirs)
        if reference_code and result["status"] != "passed" and result["diagnostic_category"] != "unsupported_processor":
            reference = run_one(args.reference_python.resolve(), recipes, case, reference_output,
                                args.timeout, args.keep_work, reference_code, github_token_file=token_file, verbose=args.verbose,
                                search_dirs=search_dirs)
            result["reference"] = reference
            result["comparison"] = "rust_failed_reference_passed" if reference["status"] == "passed" else "both_failed_requires_review"
        elif result["diagnostic_category"] == "unsupported_processor":
            result["comparison"] = "documented_custom_processor_boundary"
        else:
            result["comparison"] = "not_compared"
        write_json(Path(result["log"]).parent / "result.json", result)
        summary["results"].append(result)
        summary["counts"] = dict(Counter(item["status"] for item in summary["results"]))
        write_json(output / "summary.json", summary)
        print(f"[{index + 1}/{len(selected)}] {case['path']}: {result['status']} ({result['duration_seconds']}s)", flush=True)
    summary["complete"] = True
    summary["checkout_unchanged"] = not bool(git(recipes, "status", "--porcelain"))
    write_json(output / "summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, indent=2))
    return 0 if selected and all(item["status"] == "passed" and item["source_unchanged"] for item in summary["results"]) and summary["checkout_unchanged"] else 1


if __name__ == "__main__":
    sys.exit(main())
