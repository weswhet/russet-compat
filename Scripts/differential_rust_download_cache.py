#!/usr/bin/env python3
"""Compare downloader cache persistence and legacy xattrs on macOS and Linux.

Requires exact Python 3.11.9 with the reference dependencies and xattr package.
Windows is explicitly unsupported by this xattr gate. All HTTP traffic, caches,
homes, and source packages are isolated fixtures; existing caches are never read.
"""
import argparse
import copy
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tarfile
import tempfile
import threading

import differential_rust_http as http

BODY = "version=1.2.3\n"
ETAG = '"fixture-v1"'
MODIFIED = "Mon, 05 Oct 2026 12:00:00 GMT"
XATTR_HELPER = r'''
import json, pathlib, sys, xattr
root = pathlib.Path(sys.argv[1])
request = json.loads(sys.argv[2])
for relative, attributes in request.get("set", {}).items():
    for name, value in attributes.items():
        xattr.setxattr(str(root / relative), name, bytes.fromhex(value))
result = {}
for path in sorted(root.rglob("*")):
    if path.is_symlink():
        continue
    attrs = {name: xattr.getxattr(str(path), name).hex() for name in xattr.listxattr(str(path))
             if name in request["names"]}
    if attrs:
        result[path.relative_to(root).as_posix()] = attrs
print(json.dumps(result, sort_keys=True))
'''


def attributes(python, root, names, values=None):
    return json.loads(subprocess.check_output(
        [python, "-c", XATTR_HELPER, str(root), json.dumps({"names": names, "set": values or {}})], text=True))


def cases(names):
    etag, modified, unrelated = names
    legacy = {etag: ETAG.encode().hex(), modified: MODIFIED.encode().hex(), unrelated: b"preserved\x00\xff".hex()}
    result = []
    for processor in ("URLDownloader", "URLDownloaderPython"):
        common = {"processor": processor, "environment": {"RECIPE_CACHE_DIR": "$ROOT", "NAME": "CacheFixture"}}
        def add(name, **details):
            result.append({**copy.deepcopy(common), "name": processor + "-" + name, **details})
        add("fresh-cache-hashes", steps=[{"env": {"COMPUTE_HASHES": True}}, {}])
        for label, selected in [("both", legacy), ("etag", {etag: legacy[etag]}),
                                ("last-modified", {modified: legacy[modified]})]:
            add("legacy-" + label, files={"downloads/artifact.bin": BODY},
                attrs={"downloads/artifact.bin": selected}, steps=[{}])
        add("legacy-stale", files={"downloads/artifact.bin": BODY}, attrs={"downloads/artifact.bin": {
            **legacy, etag: b'"old"'.hex(), modified: b"Sun, 04 Oct 2026 12:00:00 GMT".hex()}}, steps=[{}])
        add("removed-sidecar", steps=[{}, {"remove": ["downloads/artifact.bin.info.json"]}])
        add("corrupt-sidecar-suppresses-xattrs", files={"downloads/artifact.bin": BODY,
            "downloads/artifact.bin.info.json": "not JSON"}, attrs={"downloads/artifact.bin": legacy}, steps=[{}])
        add("sidecar-precedes-xattrs", files={"downloads/artifact.bin": BODY,
            "downloads/artifact.bin.info.json": json.dumps({"http_headers": {"ETag": '"old"', "Content-Length": len(BODY)}})},
            attrs={"downloads/artifact.bin": legacy}, steps=[{}])
        add("missing-file-materializes", steps=[{"env": {"COMPUTE_HASHES": True}},
            {"remove": ["downloads/artifact.bin"]}])
        add("missing-file-reuses-hashes", steps=[{"env": {"COMPUTE_HASHES": True}},
            {"remove": ["downloads/artifact.bin"], "env": {"DOWNLOAD_MISSING_FILE": False}}])
        add("missing-file-no-stored-hashes", steps=[{}, {"remove": ["downloads/artifact.bin"],
            "env": {"DOWNLOAD_MISSING_FILE": False, "COMPUTE_HASHES": True}}])
        add("cached-file-hashes-ignore-stored-values", steps=[{}, {"env": {"COMPUTE_HASHES": True},
            "metadata_updates": {"file_sha1": "wrong", "file_sha256": "wrong", "file_md5": "wrong"}}])
        add("missing-file-stale-info-refetches", steps=[{}, {"remove": ["downloads/artifact.bin"],
            "env": {"DOWNLOAD_MISSING_FILE": False}, "metadata_updates": {"http_headers": {"ETag": '"old"'}}}])
        for label, source, files, attr_paths in [
            ("file", "source.pkg", {"source.pkg": "local package", "downloads/source.pkg": "stale",
              "downloads/source.pkg.info.json": "stale metadata"}, ["source.pkg"]),
            ("directory", "source.pkg", {"source.pkg/payload": "local payload",
              "downloads/source.pkg/old": "stale", "downloads/source.pkg.info.json": "stale metadata"},
             ["source.pkg", "source.pkg/payload"]),
            ("already-cached", "downloads/source.pkg", {"downloads/source.pkg": "local package",
              "downloads/source.pkg.info.json": "keep metadata"}, ["downloads/source.pkg"]),
        ]:
            env = {**common["environment"], "PKG": "$ROOT/" + source}
            add("local-pkg-" + label, environment=env, files=files,
                attrs={path: legacy for path in attr_paths}, steps=[{}], local=True)
    return result


def run(case, command, root, origin, server, python, names):
    root.mkdir()
    home = root.parent / (root.name + "-home")
    home.mkdir()
    process_env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "CFFIXED_USER_HOME": str(home),
                   "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    for relative, content in case.get("files", {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    if case.get("local"):
        for path in root.rglob("*"):
            os.utime(path, ns=(1600000000000000000, 1600000000000000000))
    attributes(python, root, names, case.get("attrs"))
    environment = http.harness.replace(case["environment"], root)
    environment["url"] = origin + "/artifact.bin"
    server.requests.clear()
    results, errors = [], []
    for step in case["steps"]:
        for relative in step.get("remove", []):
            (root / relative).unlink()
        if "metadata_updates" in step:
            sidecar = root / "downloads/artifact.bin.info.json"
            value = json.loads(sidecar.read_text())
            value.update(step["metadata_updates"])
            sidecar.write_text(json.dumps(value, indent=4))
        environment.update(step.get("env", {}))
        response = subprocess.run(command + [case["processor"]], input=plistlib.dumps(environment),
                                  capture_output=True, cwd=root, env=process_env, timeout=30)
        value = plistlib.loads(response.stdout) if response.returncode == 0 else None
        errors.append(response.stderr.decode(errors="replace"))
        files = http.harness.snapshot(root)
        if case.get("local"):
            for path, info in files.items():
                if info["kind"] == "file" and not path.endswith(".info.json"):
                    info["modified_ns"] = (root / path).stat().st_mtime_ns
        results.append({"status": response.returncode, "environment": http.harness.normalize(value, root),
                        "files": files, "xattrs": attributes(python, root, names)})
    return {"runs": results, "requests": list(server.requests)}, "\n".join(errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--rust", type=Path, default=http.harness.RUST_DEBUG_CLI)
    parser.add_argument("--coverage-output", type=Path)
    parser.add_argument("--case-contains", help="Run only case names containing this string")
    args = parser.parse_args()
    if sys.platform not in ("darwin", "linux"):
        parser.error("This gate requires macOS or Linux extended attributes; Windows is not supported")
    python = str(Path(args.python).absolute())
    version = subprocess.check_output([python, "-c", "import platform, xattr; print(platform.python_version())"], text=True).strip()
    if version != "3.11.9":
        parser.error(f"Expected Python 3.11.9, found {version}")
    prefix = "user." if sys.platform == "linux" else ""
    names = [prefix + name for name in ("com.github.autopkg.etag", "com.github.autopkg.last-modified", "org.autopkg.cache-fixture")]
    fixtures = cases(names)
    if args.case_contains:
        fixtures = [case for case in fixtures if args.case_contains in case["name"]]
        if not fixtures:
            parser.error("No cache fixture matches --case-contains")
    results = []
    with tempfile.TemporaryDirectory(prefix="autopkg-download-cache-") as temporary:
        root = Path(temporary).resolve()
        # Fail explicitly if the backing filesystem cannot store these attributes.
        (root / "probe").write_bytes(b"probe")
        assert attributes(python, root, names, {"probe": {names[2]: "ff00"}})["probe"][names[2]] == "ff00"
        archive = http.harness.reference_archive()
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(root / "reference")
        reference = [python, str(Path(http.harness.__file__).resolve()), "--worker", str(root / "reference/Code")]
        native = [str(args.rust.resolve()), "processor-run"]
        server = http.ThreadingHTTPServer(("127.0.0.1", 0), http.Handler)
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            for index, case in enumerate(fixtures):
                expected, py_error = run(case, reference, root / f"python-{index}", origin, server, python, names)
                actual, rs_error = run(case, native, root / f"rust-{index}", origin, server, python, names)
                passed = actual == expected and all(item["status"] == 0 for item in expected["runs"])
                results.append({"name": case["name"], "passed": passed})
                print(f"{'PASS' if passed else 'FAIL'} {case['name']}", flush=True)
                if not passed:
                    print(json.dumps({"python": expected, "rust": actual}, indent=2, default=str))
                    print(f"Python stderr: {py_error}\nRust stderr: {rs_error}")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    if args.coverage_output:
        args.coverage_output.write_text(json.dumps({"reference": http.harness.REFERENCE, "python": version,
            "platform": sys.platform, "xattrs": names, "cases": results}, indent=2) + "\n")
    passed = sum(case["passed"] for case in results)
    print(f"{passed}/{len(results)} download cache cases passed.")
    return passed != len(results)


if __name__ == "__main__":
    raise SystemExit(main())
