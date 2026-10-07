#!/usr/bin/env python3
"""Compare three native community providers with their pinned Python sources."""
import argparse
import contextlib
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from compat_paths import COMMUNITY_SOURCE, COMPATIBILITY, REFERENCE_CODE

SOURCES = {
    "MozillaURLProvider": "Mozilla/MozillaURLProvider.py",
    "BarebonesURLProvider": "Barebones/BarebonesURLProvider.py",
    "MSOfficeMacURLandUpdateInfoProvider": "MSOfficeUpdates/MSOfficeMacURLandUpdateInfoProvider.py",
}


def worker(name):
    sys.path.insert(0, str(REFERENCE_CODE))
    with contextlib.redirect_stdout(sys.stderr):
        from autopkglib import ProcessorError
        spec = importlib.util.spec_from_file_location(name, COMMUNITY_SOURCE / SOURCES[name])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    try:
        processor = getattr(module, name)()
        processor.env = plistlib.loads(sys.stdin.buffer.read())
        with contextlib.redirect_stdout(sys.stderr):
            result = processor.process()
        plistlib.dump({k: v for k, v in result.items() if v is not None}, sys.stdout.buffer)
        return 0
    except ProcessorError as error:
        print(f"ProcessorError: {error}", file=sys.stderr)
        return 10
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.requests.append((self.path, self.headers.get("User-Agent", ""), self.headers.get("X-Ignored")))
        status, body = self.server.response
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def fixtures():
    result = []

    def add(name, processor, env, payload=None, status=0, http_status=200):
        if isinstance(payload, (dict, list)):
            payload = (json.dumps(payload).encode() if processor != "BarebonesURLProvider"
                       and not processor.startswith("MSOffice") else plistlib.dumps(payload))
        result.append(dict(name=name, processor=processor, environment=env,
                           body=payload or b"", status=status, http_status=http_status))

    moz = "MozillaURLProvider"
    versions = {"LATEST_FIREFOX_VERSION": "151.0", "FIREFOX_ESR": "140.4.0esr",
                "LATEST_FIREFOX_DEVEL_VERSION": "152.0b9", "FIREFOX_NIGHTLY": "153.0a1",
                "LATEST_THUNDERBIRD_VERSION": "150.0.1", "LATEST_THUNDERBIRD_DEVEL_VERSION": "151.0b2"}
    for product, release in [("firefox", "latest"), ("firefox", "latest-esr"),
                             ("firefox", "esr-latest"), ("firefox", "latest-beta"),
                             ("firefox-beta", "latest"), ("firefox-nightly", "latest"),
                             ("thunderbird", "latest"), ("thunderbird-beta", "latest")]:
        add(f"mozilla-{product}-{release}", moz, {"product_name": product, "release": release}, versions)
    add("mozilla-defaults", moz, {"product_name": "firefox"}, versions)
    add("mozilla-explicit-msi", moz, {"product_name": "firefox", "release": "79.0-msi", "platform": "win64", "locale": "en_US"})
    add("mozilla-custom-template", moz, {"product_name": "firefox", "release": "79.0", "base_url": "https://fixture.invalid/{{literal}}/{product_release}/{platform}/{locale}"})
    add("mozilla-required-input", moz, {}, status=10)
    add("mozilla-ignores-common-options", moz, {"product_name": "firefox", "request_headers": {"X-Ignored": "ignored"}, "curl_opts": ["--fail"]}, versions, http_status=404)
    bare = "BarebonesURLProvider"
    entries = [{"SUFeedEntryShortVersionString": v, "SUFeedEntryDownloadURL": u,
                "SUFeedEntryMinimumSystemVersion": "12.0"}
               for v, u in [("9.0", "https://fixture.invalid/old.dmg"),
                            ("10.0", "https://fixture.invalid/first.dmg"),
                            ("10.0.0", "https://fixture.invalid/last.dmg")]]
    for product in ["bbedit", "yojimbo"]:
        add("barebones-" + product, bare, {"product_name": product}, {"SUFeedEntries": entries})
    add("barebones-empty", bare, {"product_name": "bbedit"}, {"SUFeedEntries": []}, status=10)
    add("barebones-invalid-product", bare, {"product_name": "unknown"}, status=10)
    add("barebones-missing-input", bare, {}, status=10)
    ms = "MSOfficeMacURLandUpdateInfoProvider"
    item = {"Location": " https://fixture.invalid/App_Updater.pkg\n", "Title": " Update ",
            "Update Version": "16.99", "Minimum OS": "10.1", "Trigger Condition": ["and", "Registered File"]}
    delta = dict(item, FullUpdaterLocation="https://fixture.invalid/Full.pkg",
                 Triggers={"Registered File": {"VersionsRelative": ["> 1", ">= 16.90"]}})
    for product in ["Excel2019", "OneNote2019", "Outlook2019", "PowerPoint2019", "Word2019",
                    "SkypeForBusiness", "AutoUpdate03", "AutoUpdate04", "DefenderATP", "Teams", "Teams2",
                    "CompanyPortal", "OneDrive", "RemoteDesktop"]:
        add("microsoft-" + product, ms, {"product": product}, [delta, item])
    add("microsoft-standalone", ms, {"product": "Excel2019", "version": "latest-standalone"}, [delta, item])
    add("microsoft-delta", ms, {"product": "Excel2019", "version": "latest-delta", "NAME": "Excel"}, [item, delta])
    add("microsoft-delta-name", ms, {"product": "Excel2019", "version": "latest-delta", "NAME": "Excel", "munki_required_update_name": "Other"}, [delta])
    for channel in ["InsiderSlow", "InsiderFast", "12345678-1234-1234-1234-123456789abc"]:
        add("microsoft-channel-" + channel, ms, {"product": "Word2019", "channel": channel}, [item])
    add("microsoft-high-minimum", ms, {"product": "Word2019"}, [dict(item, **{"Minimum OS": "15.0"})])
    add("microsoft-missing-minimum", ms, {"product": "Word2019"}, [{k: v for k, v in item.items() if k != "Minimum OS"}])
    add("microsoft-no-update", ms, {"product": "Word2019"}, [], status=10)
    add("microsoft-no-array", ms, {"product": "Word2019"}, {}, status=10)
    add("microsoft-unexpected-trigger", ms, {"product": "Word2019"}, [dict(item, **{"Trigger Condition": ["or"]})], status=10)
    add("microsoft-invalid-version", ms, {"product": "Word2019", "version": "old"}, status=10)
    add("microsoft-invalid-channel", ms, {"product": "Word2019", "channel": "wrong"}, status=10)
    add("microsoft-missing-product", ms, {}, status=10)
    add("microsoft-standalone-url", ms, {"product": "Word2019", "version": "latest-standalone"}, [dict(item, Location="https://fixture.invalid/App.pkg")], status=10)
    add("microsoft-delta-no-triggers", ms, {"product": "Word2019", "version": "latest-delta"}, [{k: v for k, v in delta.items() if k != "Triggers"}], status=10)
    add("microsoft-delta-no-minimum", ms, {"product": "Word2019", "version": "latest-delta"}, [dict(delta, Triggers={"Registered File": {"VersionsRelative": ["> 1"]}})], status=10)
    for product in ["Excel2016", "OneNote2016", "Outlook2016", "PowerPoint2016", "Word2016"]:
        add("microsoft-deprecated-" + product, ms, {"product": product, "RECIPE_PATH": "/fixture/Office.download.recipe"})
    releases = [dict(Platform=p, Architecture=a, ProductVersion=v,
                     Artifacts=[{"ArtifactName": "zip", "Location": "https://fixture.invalid/ignore.zip"},
                                {"ArtifactName": "pkg", "Location": u}])
                for p, a, v, u in [("windows", "universal", "999", "wrong"),
                                    ("macos", "arm64", "999", "wrong"),
                                    ("MacOS", "Universal", "9.0", "old"),
                                    ("macos", "universal", "10.0", " https://fixture.invalid/Edge.pkg\n")]]
    for channel, api in [("Production", "Stable"), ("InsiderSlow", "Beta"), ("InsiderFast", "Dev")]:
        # Edge uses JSON rather than the MAU plist feed.
        add("edge-" + channel, ms, {"product": "Edge", "channel": channel},
            json.dumps([{"Product": api, "Releases": releases}]).encode())
    add("edge-defaults", ms, {"product": "Edge"}, json.dumps([{"Product": "Stable", "Releases": releases}]).encode())
    add("edge-missing-channel", ms, {"product": "Edge"}, b"[]", status=10)
    add("edge-no-compatible-release", ms, {"product": "Edge"}, b'[{"Product":"Stable","Releases":[]}]', status=10)
    add("edge-invalid-channel", ms, {"product": "Edge", "channel": "invalid"}, status=10)
    add("edge-delta", ms, {"product": "Edge", "version": "latest-delta"}, status=10)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust", required=True, type=Path)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    python = str(Path(shutil.which(args.python) or args.python).resolve())
    curl = shutil.which("curl")
    if not curl:
        parser.error("curl is required")
    contract = json.loads((COMPATIBILITY / "community-processors.json").read_text())
    for name, relative in SOURCES.items():
        source = COMMUNITY_SOURCE / relative
        if hashlib.sha256(source.read_bytes()).hexdigest() != contract["sources"][name]["sha256"]:
            parser.error(f"Pinned community source digest changed for {name}")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    failures = []
    try:
        with tempfile.TemporaryDirectory(prefix="russet-modern-") as scratch:
            root = Path(scratch)
            wrapper = root / "curl-fixture"
            wrapper.write_text(f"#!{python}\nimport os,sys\nfrom urllib.parse import urlsplit\na=sys.argv[1:]\nu=urlsplit(a[-1])\na[-1]='http://127.0.0.1:{server.server_port}'+u.path+('?' + u.query if u.query else '')\nos.execv({curl!r},[{curl!r}]+a)\n")
            wrapper.chmod(0o755)
            for case in fixtures():
                records = []
                for kind, command in [("python", [python, str(Path(__file__).resolve()), "--worker", case["processor"]]),
                                      ("rust", [str(args.rust.resolve()), "processor-run", case["processor"]])]:
                    home = root / case["name"] / kind
                    home.mkdir(parents=True)
                    env = dict(case["environment"], CURL_PATH=str(wrapper), verbose=1)
                    server.response = (case["http_status"], case["body"])
                    server.requests.clear()
                    process = subprocess.run(command, input=plistlib.dumps(env), stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, timeout=30, cwd=home,
                                             env=dict(os.environ, HOME=str(home), CFFIXED_USER_HOME=str(home)))
                    prefix = case["processor"] + ": "
                    logs = [line for line in process.stderr.decode(errors="replace").splitlines() if line.startswith(prefix)]
                    # Standalone errors use different prefixes; compare their message directly.
                    errors = [line.removeprefix("ProcessorError: ") for line in process.stderr.decode(errors="replace").splitlines() if line.startswith("ProcessorError: ")]
                    records.append(dict(status=process.returncode,
                                        environment=plistlib.loads(process.stdout) if process.returncode == 0 else None,
                                        logs=logs, requests=list(server.requests), errors=errors))
                if records[0] != records[1] or records[0]["status"] != case["status"]:
                    failures.append(case["name"])
                    print("FAIL", case["name"], json.dumps(records, default=str, indent=2))
                else:
                    print("PASS", case["name"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print(f"{len(fixtures()) - len(failures)}/{len(fixtures())} community modern comparisons passed")
    return bool(failures)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        sys.exit(worker(sys.argv[2]))
    sys.exit(main())
