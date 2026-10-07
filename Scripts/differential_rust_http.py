#!/usr/bin/env python3
"""Compare native HTTP processors with pinned Python using an isolated local server."""
import argparse
import base64
import functools
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import re
from pathlib import Path
import plistlib
import subprocess
import sys
import tarfile
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit, quote

import differential_rust_processors as harness


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def date_time_string(self, *_):
        return "Tue, 06 Oct 2026 12:00:00 GMT"

    def do_HEAD(self):
        self.respond(False)

    def do_GET(self):
        self.respond(True)

    def respond(self, body):
        path = self.path.split("?", 1)[0]
        headers = {key.lower(): value for key, value in self.headers.items()
                   if key.lower() in {"authorization", "if-none-match", "if-modified-since", "x-fixture"}}
        self.server.requests.append((self.command, self.path, headers))
        content = b"version=1.2.3\n"
        status = 200
        extra = {}
        if path == "/regex":
            content = parse_qs(urlsplit(self.path).query, keep_blank_values=True)["body"][0].encode()
        elif path == "/regex-large-alphabet":
            # More distinct non-ASCII scalars than the former private-use remap
            # could represent. Keep this out of the URL/header length limit.
            content = ("".join(chr(n) for n in range(128, 0x23000)
                               if not 0xD800 <= n <= 0xDFFF) + "MARKéAéa").encode()
        elif path == "/redirect":
            status, content, extra = 302, b"", {"Location": "/artifact.bin"}
        elif path == "/auth" and self.headers.get("Authorization") != "Basic " + base64.b64encode(b"fixture:secret").decode():
            status, content, extra = 401, b"unauthorized", {"WWW-Authenticate": 'Basic realm="fixture"'}
        elif path == "/missing":
            status, content = 404, b"not found"
        elif path == "/artifact.bin":
            extra = {"ETag": '"fixture-v1"', "Last-Modified": "Mon, 05 Oct 2026 12:00:00 GMT",
                     "Content-Disposition": 'attachment; filename="release.bin"'}
            if self.headers.get("If-None-Match") == '"fixture-v1"':
                status, content = 304, b""
        elif path.startswith("/repos/"):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            def release(tag, prerelease=False):
                return {"tag_name": tag, "name": tag, "body": "Release notes", "prerelease": prerelease,
                        "assets": [{"name": "Fixture.zip", "browser_download_url": origin + "/Fixture.zip",
                                    "url": origin + "/assets/1", "created_at": "2026-01-01T00:00:00Z"}]}
            releases = [release("v3.0-beta", True), release("v2.0"), release("v1.0")]
            if "/empty/" in path:
                releases = []
            if "/pagination/" in path and "page=1&" in self.path:
                releases = [release("v3.0-beta", True)]
            if "/null-notes/" in path:
                for item in releases:
                    item["body"] = None
            if "/retry-token/" in path and self.headers.get("Authorization"):
                status = 401
            content = json.dumps(release("v2.0") if path.endswith("/latest") else releases).encode()
        elif path.startswith("/appcast"):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            content = (f'<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel>'
                       f'<item><enclosure url="{origin}/Fixture 2.zip" sparkle:version="20" sparkle:shortVersionString="2.0" />'
                       f'<sparkle:minimumSystemVersion>12.0</sparkle:minimumSystemVersion></item>'
                       f'<item><enclosure url="{origin}/Fixture.zip" sparkle:version="10" sparkle:shortVersionString="1.0" /></item>'
                       f'</channel></rss>').encode()
            if path == "/appcast-empty":
                content = b"<rss><channel /></rss>"
            elif path == "/appcast-invalid":
                content = b"not xml"
        self.send_response(status)
        if path == "/truncated-chunked":
            self.send_header("Transfer-Encoding", "chunked")
        else:
            self.send_header("Content-Length", str(len(content) + (100 if path == "/truncated" else 0)))
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write((f"{len(content):x}\r\n".encode() + content + b"\r\n") if path == "/truncated-chunked" else content)


@functools.lru_cache(maxsize=None)
def reference_certifi(python):
    path = Path(subprocess.check_output(
        [python, "-c", "import certifi; print(certifi.where())"], text=True).strip())
    expected = "2089fc5a25836401faee99f722460b01b393999746a0fb817943666d6ecc0458"
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Reference certifi does not match the pinned release bundle")
    return str(path)


def normalize_implicit_certifi(logs, command, reference_worker):
    """Normalize only the shipped macOS fallback resource's diagnostic path."""
    override = os.environ.get("SSL_CERT_FILE")
    if sys.platform != "darwin" or (override and Path(override).is_file()):
        return logs
    prefix = "URLDownloaderPython: SSL_CERT_FILE="
    normalized = []
    for line in logs.splitlines(keepends=True):
        if line.startswith(prefix):
            value = line[len(prefix):].rstrip("\n")
            if reference_worker:
                matches = value == reference_certifi(command[0])
            else:
                path = Path(value)
                matches = (path.parent.resolve() == Path(tempfile.gettempdir()).resolve()
                           and re.fullmatch(r"autopkg-certifi-[A-Za-z0-9_]+\.pem", path.name))
            if matches:
                line = prefix + "$PINNED_CERTIFI" + ("\n" if line.endswith("\n") else "")
        normalized.append(line)
    return "".join(normalized)


def run(case, command, directory, url, server):
    directory.mkdir()
    home = directory.parent / (directory.name + "-home")
    home.mkdir()
    process_env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "CFFIXED_USER_HOME": str(home)}
    for name, value in case.get("files", {}).items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    environment = harness.replace(case["environment"], directory)
    environment = harness.replace(environment, directory)
    environment = json.loads(json.dumps(environment).replace("$SERVER", url))
    if "path" in case:
        environment[case.get("url_key", "url")] = url + case["path"]
    server.requests.clear()
    results = []
    reference_worker = "--worker" in command
    capture_failures = case.get("compare_logs") and case.get("status", 0) != 0
    log_file = home / "processor-output.txt"
    if reference_worker and capture_failures:
        process_env["AUTOPKG_DIFFERENTIAL_LOG_FILE"] = str(log_file)
        case["_reference_logs"] = []
    for run_index in range(case.get("repeat", 1)):
        result = subprocess.run(command + [case["processor"]], input=plistlib.dumps(environment),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=directory, timeout=30, env=process_env)
        value = plistlib.loads(result.stdout) if result.returncode == 0 else None
        files = harness.snapshot(directory)
        # NamedTemporaryFile names are nondeterministic; preserve their count/content/mode.
        files = {(f"downloads/$TRANSFER_TEMP_{index}" if name.startswith("downloads/tmp") else name): value for index, (name, value) in enumerate(files.items())}
        record = {"status": result.returncode, "environment": harness.normalize(value, directory),
                  "files": files}
        if case.get("compare_logs"):
            diagnostic = result.stderr.decode(errors="replace").replace("\r\n", "\n")
            prefix = case["processor"] + ": "
            if reference_worker and capture_failures:
                logs = log_file.read_text() if log_file.exists() else ""
            else:
                start = diagnostic.find(prefix)
                logs = diagnostic[start:] if start >= 0 else ""
            logs = normalize_implicit_certifi(logs, command, reference_worker)
            logs = re.sub(r"(\$ROOT(?:[/\\][^\'\"\s,]+)*[/\\])tmp[A-Za-z0-9_]+",
                          r"\1$TRANSFER_TEMP", harness.normalize(logs, directory))
            if reference_worker and capture_failures:
                case["_reference_logs"].append(logs)
            elif capture_failures and result.returncode != 0:
                expected_log = case["_reference_logs"][run_index]
                # A failing native standalone run appends its terminal exception
                # diagnostic to the same stream. Preserve every processor log;
                # discard only a matching reference prefix's diagnostic suffix.
                if logs.startswith(expected_log) and prefix not in logs[len(expected_log):]:
                    logs = expected_log
            record["processor_log"] = logs
        results.append(record)
    return {"runs": results, "requests": list(server.requests)}, result.stderr.decode(errors="replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--rust", type=Path, default=harness.RUST_DEBUG_CLI)
    parser.add_argument("--verbose", type=int, choices=[0, 1, 2, 4], default=0,
                        help="Compare successful processor diagnostics explicitly at this verbosity")
    args = parser.parse_args()
    cases = []
    for processor in ["URLDownloader", "URLDownloaderPython"]:
        for name, path, extra, repeat, status in [
            ("redirect", "/redirect", {}, 1, 0),
            ("conditional-cache", "/artifact.bin", {}, 2, 0),
            ("hashes", "/artifact.bin", {"COMPUTE_HASHES": True}, 2, 0),
            ("prefetch", "/redirect", {"prefetch_filename": True}, 1, 0),
            ("explicit-filename", "/artifact.bin", {"filename": "chosen.bin"}, 1, 0),
            ("explicit-directory", "/artifact.bin", {"download_dir": "$ROOT/custom"}, 1, 0),
            ("filesize-only", "/artifact.bin", {"CHECK_FILESIZE_ONLY": True}, 2, 0),
            ("empty-header-tests", "/artifact.bin", {"HEADERS_TO_TEST": []}, 2, 0),
            ("local-package", "/missing", {"PKG": "$ROOT/local.pkg"}, 1, 0),
            ("basic-auth", "/auth", {"request_headers": {"Authorization": "Basic Zml4dHVyZTpzZWNyZXQ="}}, 1, 0),
            ("unauthorized", "/auth", {}, 1, 10),
            ("missing", "/missing", {}, 1, 10),
        ]:
            cases.append(dict(name=f"{processor}-{name}", processor=processor, path=path, repeat=repeat,
                              status=(1 if processor == "URLDownloaderPython" and status == 10 else status), environment={"RECIPE_CACHE_DIR": "$ROOT", "NAME": "Fixture", **extra},
                              files={"local.pkg": "local fixture"} if name == "local-package" else {}))
        cases.append(dict(name=f"{processor}-corrupt-sidecar", processor=processor, path="/artifact.bin", status=0,
                          environment={"RECIPE_CACHE_DIR": "$ROOT", "NAME": "Fixture"},
                          files={"downloads/artifact.bin.info.json": "not json"}))
        cases.append(dict(name=f"{processor}-truncated-body", processor=processor, path="/truncated",
                          status=(0 if processor == "URLDownloaderPython" else 10),
                          environment={"RECIPE_CACHE_DIR": "$ROOT", "NAME": "Fixture"}))
        cases.append(dict(name=f"{processor}-truncated-chunked", processor=processor, path="/truncated-chunked",
                          status=(1 if processor == "URLDownloaderPython" else 10),
                          environment={"RECIPE_CACHE_DIR": "$ROOT", "NAME": "Fixture"}))
    cases.extend([
        dict(name="text-http-unauthorized", processor="URLTextSearcher", path="/auth", status=10,
             environment={"re_pattern": "version=([0-9.]+)", "curl_opts": ["--fail"]}),
        dict(name="text-http-missing", processor="URLTextSearcher", path="/missing", status=10,
             environment={"re_pattern": "version=([0-9.]+)", "curl_opts": ["--fail"]}),
        dict(name="text-named-capture", processor="URLTextSearcher", path="/redirect", status=0,
             environment={"re_pattern": r"version=(?P<version>[\d.]+)", "request_headers": {"X-Fixture": "yes"}}),
        dict(name="text-invalid-regex", processor="URLTextSearcher", path="/artifact.bin", status=1,
             environment={"re_pattern": "("}),
        dict(name="text-flags-custom-output", processor="URLTextSearcher", path="/artifact.bin", status=0,
             environment={"re_pattern": r"^VERSION=([\d.]+)$", "re_flags": ["IGNORECASE", "MULTILINE"], "result_output_var_name": "release"}),
        dict(name="text-no-match", processor="URLTextSearcher", path="/artifact.bin", status=10,
             environment={"re_pattern": "absent"}),
    ])
    for name, pattern, body, flags, status in [
        ("ascii-word", r"(?P<word>\w+)", "éabcı", ["ASCII"], 0),
        ("ascii-digit", r"(\d+)", "１２3٤", ["ASCII"], 0),
        ("ascii-space", r"(\s+)", "\u00a0 \t\u2003", ["ASCII"], 0),
        ("ascii-boundary", r"\b(abc)\b", "éabcé", ["ASCII"], 0),
        ("ascii-fold-excludes-unicode", r"([a-z]+)", "İıſKABC", ["ASCII", "IGNORECASE"], 0),
        ("unicode-fold-includes-specials", r"([a-z]+)", "İıſKABC", ["IGNORECASE"], 0),
        ("unicode-i-literal", r"(i+)", "İıIi", ["IGNORECASE"], 0),
        ("unicode-negated-i", r"([^i]+)", "İıIix", ["IGNORECASE"], 0),
        ("ascii-unicode-range", r"([é-ê]+)", "èéêë", ["ASCII"], 0),
        ("ascii-private-use-offset", r"(?P<x>[\uE000-\uE002]+)", "😀\ue000\ue001", ["ASCII"], 0),
        ("ascii-backref", r"(éa)\1", "😀éaéa", ["ASCII"], 0),
        ("ascii-ignorecase-backref", r"(éa)\1", "éaéA", ["ASCII", "IGNORECASE"], 0),
        ("ascii-scoped-whole", r"(?a:(\w+))", "éabc", [], 0),
        ("mixed-scopes", r"(?a:(?P<ascii>\w+))(?u:(?P<unicode>\w+))", "abcéı", [], 0),
        ("mixed-nested", r"(?a:(?P<a>\w+)(?u:(?P<u>\w+))(?P<tail>\w+))", "abécd", [], 0),
        ("mixed-scope-i", r"(?ai:(?P<a>[a-z]+))(?ui:(?P<u>[a-z]+))", "ABCİıſK", [], 0),
        ("mixed-i-disabled", r"(?i:(?a:abc)(?-i:XYZ))(?u:é)", "AbCXYZé", [], 0),
        ("mixed-negated-class", r"(?ai:([^i]+))(?u:İ)", "ıxİ", [], 0),
        ("mixed-boundaries", r"(?a:\b(abc)\b)(?u:é)", "éabcé", [], 0),
        ("mixed-class-shorthand", r"(?a:([\w\d]+))(?u:é)", "abc12é", [], 0),
        ("mixed-class-complement", r"(?a:([\W]+))(?u:a)", "éıa", [], 0),
        ("mixed-hex-case", r"(?ai:(\u00e9+))(?u:é)", "Ééé", [], 0),
        ("mixed-sensitive-backref", r"(?a:(éa)\1)(?u:é)", "éaéaé", [], 0),
        ("mixed-ascii-caseless-backref", r"(?ai:(a)\1)(?u:x)", "aAx", [], 0),
        ("mixed-ascii-caseless-named", r"(?ai:(?P<letter>a)(?P=letter))(?u:x)", "aAx", [], 0),
        ("mixed-ascii-backref-no-unicode-fold", r"(?ai:(é)\1)(?u:x)", "éÉx", [], 10),
        ("unicode-backref-dotted-i", r"(?i:(i)\1)", "iİ", [], 0),
        ("unicode-backref-dotless-i-excluded", r"(?i:(i)\1)", "iı", [], 10),
        ("unicode-backref-long-s-excluded", r"(?i:(s)\1)", "sſ", [], 10),
        ("unicode-backref-kelvin", r"(?i:(k)\1)(?P<tail>x)", "kKx", [], 0),
        ("unicode-backref-reverse-width", r"(?i:(K)\1)(?P<tail>x)", "Kkx", [], 0),
        ("unicode-backref-nested-capture", r"(?i:(?P<group>iK)(?P=group))(?P<tail>x)", "iKİkx", [], 0),
        ("unicode-backref-empty", r"(?i:()\1)(?P<tail>x)", "x", [], 0),
        ("unicode-backref-backtracking", r"(?i:(i|ik)\1)(?P<tail>x)", "ikİKx", [], 0),
        ("backref-ascii-use-site", r"(?u:(k))(?a:(?i:\1))", "kK", [], 10),
        ("backref-unicode-use-site", r"(?a:(k))(?u:(?i:\1))", "kK", [], 0),
        ("backref-sigma-simple-lower", r"(?i:(Σ)\1)", "Σς", [], 10),
        ("backref-micro-simple-lower", r"(?i:(µ)\1)", "µΜ", [], 10),
        ("ascii-inline", r"(?ai)([a-z]+)", "İABC", [], 0),
        ("empty-nonboundary", r"\B", "", [], 10),
        ("locale-invalid-string", r"x", "x", ["LOCALE"], 1),
        ("ascii-unicode-conflict", r"x", "x", ["ASCII", "UNICODE"], 1),
    ]:
        cases.append(dict(name=f"text-{name}", processor="URLTextSearcher",
                          path="/regex?body=" + quote(body, safe=""), status=status,
                          environment={"re_pattern": pattern, "re_flags": flags}))
    for name, pattern, flags in [
        ("large-ascii-capture", r"MARK(?P<value>éAéa)", ["ASCII"]),
        ("large-ascii-backref", r"MARK(?P<value>éa)(?P=value)", ["ASCII", "IGNORECASE"]),
        ("large-ascii-boundary", r"\b(MARK)\b", ["ASCII"]),
        ("large-mixed-mode", r"(?a:MARK)(?u:(é))(?ai:aéa)", []),
        ("large-ascii-range", r"([\U00022FFD-\U00022FFF]+)", ["ASCII"]),
    ]:
        cases.append(dict(name=f"text-{name}", processor="URLTextSearcher",
                          path="/regex-large-alphabet", status=0,
                          environment={"re_pattern": pattern, "re_flags": flags}))
    for name, extra, status in [
        ("default", {}, 0), ("prereleases", {"include_prereleases": True}, 0),
        ("latest", {"latest_only": True}, 0), ("highest-tag", {"sort_by_highest_tag_names": True}, 0),
        ("asset-regex", {"asset_regex": r"Fixture\.zip$"}, 0),
        ("empty", {"github_repo": "fixture/empty"}, 10),
        ("pagination", {"github_repo": "fixture/pagination", "GITHUB_RELEASES_PER_PAGE": 1}, 0),
        ("null-notes", {"github_repo": "fixture/null-notes"}, 0),
        ("invalid-token-retry", {"github_repo": "fixture/retry-token", "GITHUB_TOKEN_PATH": "$ROOT/token"}, 0),
        ("recipe-token-ignored", {"github_repo": "fixture/retry-token", "GITHUB_TOKEN": "fixture-recipe-token"}, 0),
        ("invalid-asset-regex", {"asset_regex": "[", "latest_only": True}, 10),
        ("no-matching-asset", {"asset_regex": "absent", "latest_only": True}, 10),
    ]:
        cases.append(dict(name="github-" + name, processor="GitHubReleasesInfoProvider", status=status,
                          environment={"github_repo": "fixture/app", "GITHUB_URL": "$SERVER", "GITHUB_TOKEN": "",
                                       "GITHUB_TOKEN_PATH": "$ROOT/absent-token", **extra},
                          files={"token": "fixture-invalid"} if name == "invalid-token-retry" else {}))
    for name, path, extra, status in [
        ("default", "/appcast", {}, 0), ("unencoded", "/appcast", {"urlencode_path_component": False}, 0),
        ("query-headers", "/appcast", {"appcast_query_pairs": {"channel": "stable"}, "appcast_request_headers": {"X-Fixture": "yes"}}, 0),
        ("empty", "/appcast-empty", {}, 10), ("invalid", "/appcast-invalid", {}, 10),
    ]:
        cases.append(dict(name="sparkle-" + name, processor="SparkleUpdateInfoProvider", status=status,
                          path=path, url_key="appcast_url", environment=extra))
    failures = 0
    python_path = str(Path(args.python).absolute())
    dependencies = "import appdirs, yaml, certifi, lxml"
    if sys.platform == "darwin":
        dependencies += ", xattr, CoreFoundation, Foundation"
    subprocess.run([python_path, "-c", dependencies], check=True)
    with tempfile.TemporaryDirectory(prefix="autopkg-http-differential-") as temporary:
        root = Path(temporary)
        archive = harness.reference_archive()
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(root / "reference")
        python = [python_path, str(Path(harness.__file__).resolve()), "--worker", str(root / "reference/Code")]
        rust = [str(args.rust.resolve()), "processor-run"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            for index, case in enumerate(cases):
                if args.verbose:
                    case["environment"]["verbose"] = args.verbose
                    case["compare_logs"] = True
                expected, py_error = run(case, python, root / f"python-{index}", url, server)
                actual, rs_error = run(case, rust, root / f"rust-{index}", url, server)
                passed = expected == actual and all(r["status"] == case["status"] for r in expected["runs"])
                print(f"{'PASS' if passed else 'FAIL'} {case['name']}")
                if not passed:
                    failures += 1
                    print(json.dumps({"Python": expected, "Rust": actual}, indent=2, default=str))
                    print(f"Python stderr: {py_error}\nRust stderr: {rs_error}")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print(f"{len(cases) - failures}/{len(cases)} HTTP cases passed.")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
