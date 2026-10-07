#!/usr/bin/env python3
"""Compare six promoted legacy processors with pinned Python using isolated fixtures.

No installation occurs. Native fixtures build disposable packages and DMGs.
Distribution XML is compared structurally because XML serializers differ; package
containers are expanded before comparison because xar timestamps are volatile.
Uncaught Python runtime failures retain status 1; declared ProcessorError failures
retain status 10. Neither class is normalized away.
"""
import argparse
import contextlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import xml.etree.ElementTree as ET

import differential_rust_processors as harness

from compat_paths import COMMUNITY_SOURCE as SOURCES, REFERENCE_CODE
PROCESSORS = {
    "AdobeAcrobatProUpdateInfoProvider": "AdobeAcrobatPro",
    "AdobeFlashURLProvider": "AdobeFlashPlayer",
    "AdobeReaderURLProvider": "AdobeReader",
    "AdobeReaderRepackager": "AdobeReader",
    "PuppetlabsProductsURLProvider": "Puppetlabs",
    "SassafrasK2ClientCustomizer": "SassafrasK2Client",
}


def worker(name):
    sys.path.insert(0, str(REFERENCE_CODE))
    with contextlib.redirect_stdout(sys.stderr):
        from autopkglib import ProcessorError
    try:
        spec = importlib.util.spec_from_file_location(name, SOURCES / PROCESSORS[name] / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stdout(sys.stderr):
            spec.loader.exec_module(module)
            processor = getattr(module, name)()
            processor.env = plistlib.loads(sys.stdin.buffer.read())
            log_path = os.environ.get("AUTOPKG_DIFFERENTIAL_LOG_FILE")
            if log_path:
                with open(log_path, "w", encoding="utf-8") as log:
                    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                        result = processor.process()
            else:
                result = processor.process()
        plistlib.dump({k: v for k, v in result.items() if v is not None}, sys.stdout.buffer)
        return 0
    except ProcessorError as error:
        print(f"ProcessorError: {error}", file=sys.stderr)
        return 10
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


def command(*args):
    return subprocess.check_output([str(a) for a in args], stderr=subprocess.PIPE, timeout=120)


def xml_structure(path):
    def node(element):
        return [element.tag, sorted(element.attrib.items()), element.text or "", element.tail or "",
                [node(child) for child in element]]
    return node(ET.parse(path).getroot())


def filesystem(root):
    result = harness.snapshot(root)
    for name in result:
        path = root / name
        if path.name == "Distribution":
            result[name] = {"kind": "xml", "mode": result[name]["mode"], "value": xml_structure(path)}
    return result


def outcome(case, root, invocation):
    result, error = harness.run_case(case, root, invocation)
    result["files"] = filesystem(root)
    if "processor_logs" in result:
        # These resources are identical bytes but Python reads a repository file
        # whereas Rust embeds them. Normalize that identified resource origin.
        log = result["processor_logs"].replace(str(SOURCES / "AdobeReader"), "$ADOBE_RESOURCE")
        log = log.replace("embedded:AdobeReader", "$ADOBE_RESOURCE")
        # Both mounts choose random temporary paths; retain the mounted suffix.
        log = re.sub(r"/private/tmp/(?:autopkg-mount-[^/\s]+/)?dmg\.[^/\s]+", "$MOUNT", log)
        result["processor_logs"] = log
    if case["processor"] == "AdobeReaderRepackager":
        images = plistlib.loads(command("/usr/bin/hdiutil", "info", "-plist")).get("images", [])
        result["leaked_mounts"] = [image["image-path"] for image in images
                                  if str(root.resolve()) in str(Path(image.get("image-path", "")).resolve())]
    if case.get("repackaged") and result["status"] == 0:
        package = root / case["repackaged"]
        with tempfile.TemporaryDirectory(prefix="russet-logical-package-") as temporary:
            expanded = Path(temporary) / "expanded"
            command("/usr/sbin/pkgutil", "--expand", package, expanded)
            result["files"][case["repackaged"]] = {"kind": "package", "contents": filesystem(expanded)}
    return result, error


def native_cases(root):
    cases = []
    for name in ("AdobeReaderXI", "AcroRdrDC_22001", "AdobeReaderBadDistribution"):
        expanded = root / (name + "-expanded")
        scripts = expanded / "application_mini_7z.pkg/Scripts"
        scripts.mkdir(parents=True)
        (scripts / "preinstall").write_text("#!/bin/sh\nexit 0\n")
        (scripts / "preinstall").chmod(0o755)
        (expanded / "Distribution").write_text('<installer-gui-script><domains enable_anywhere="false"/><options customize="never"/></installer-gui-script>')
        (expanded / "application_mini_7z.pkg/PackageInfo").write_text('<pkg-info format-version="2" identifier="org.russet.fixture" version="1"/>')
        if name == "AdobeReaderBadDistribution":
            (expanded / "Distribution").write_text("<wrong/>")
        image_root = root / (name + "-image")
        image_root.mkdir()
        command("/usr/sbin/pkgutil", "--flatten", expanded, image_root / (name + ".pkg"))
        image = root / (name + ".dmg")
        command("/usr/bin/hdiutil", "create", "-srcfolder", image_root, "-fs", "HFS+", "-format", "UDZO", image)
        cases.append(dict(name="repackage " + name, processor="AdobeReaderRepackager",
                          environment={"dmg_path": "$ROOT/input.dmg", "RECIPE_CACHE_DIR": "$ROOT/cache"},
                          directories=["cache"], copy_files={"input.dmg": str(image)}, repackaged="cache/" + name + ".pkg",
                          status=10 if name == "AdobeReaderBadDistribution" else 0))
    cases.append(dict(name="repackager invalid image", processor="AdobeReaderRepackager", status=10,
                      environment={"dmg_path": "$ROOT/broken.dmg", "RECIPE_CACHE_DIR": "$ROOT/cache"}, files={"broken.dmg": "not a disk image"}))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust", required=True, type=Path)
    parser.add_argument("--python", required=True)
    parser.add_argument("--coverage-output", type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This suite includes native DMG/package fixtures and requires macOS")
    python = str(Path(args.python).absolute()) if os.path.sep in args.python else args.python
    rust = [str(args.rust.resolve()), "processor-run"]
    reference = [python, str(Path(__file__).resolve()), "--worker"]
    state = {"responses": {}, "requests": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(self.path)
            data = state["responses"].get(self.path)
            if data is None:
                self.send_error(404)
                return
            if isinstance(data, str):
                data = data.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    evidence = []
    try:
        with tempfile.TemporaryDirectory(prefix="russet-community-legacy-") as temporary:
            root = Path(temporary)
            wrapper = root / "curl-fixture"
            wrapper.write_text(f"#!{python}\nimport subprocess,sys,urllib.parse\na=sys.argv[1:]\nfor i,v in enumerate(a):\n if v.startswith(('http://','https://')):\n  u=urllib.parse.urlsplit(v)\n  a[i]='http://127.0.0.1:{server.server_port}'+u.path+('?' + u.query if u.query else '')\nsys.exit(subprocess.call(['/usr/bin/curl']+a))\n")
            wrapper.chmod(0o755)
            flash_path = "/get/flashplayer/update/current/xml/version_en_mac_pl.xml"
            products_path = "/reader/products?os=Mac%20OS%2010.14.0&api_key=dc-get-adobereader-cdn"
            download_path = "/reader/downloadUrl?name=Reader%20DC%20for%20Mac&os=Mac%20OS%2010.14.0&api_key=dc-get-adobereader-cdn"
            base = "/arm-manifests/mac"
            current = {"PatchURL": "/patch.dmg", "BuildNumber": "11.0.3", "PreviousURLTemplate": "/previous.plist"}
            previous = {"PatchURL": "/previous.dmg", "BuildNumber": "11.0.2"}
            acrobat_responses = {base + "/11/manifest_url_template.txt": "/11/11.0.99/{PROD}_{PROD_ARCH}_{OS_VER_MIN}.plist", base + "/11/11.0.99/com_adobe_Acrobat_Pro_univ_9.plist": plistlib.dumps(current), base + "/previous.plist": plistlib.dumps(previous)}
            cases = [
                dict(name="Flash version override", processor="AdobeFlashURLProvider", environment={"version": "32,0,0,465"}),
                dict(name="Flash URL override", processor="AdobeFlashURLProvider", environment={"url": "https://example.invalid/override.dmg"}),
                dict(name="Flash XML latest", processor="AdobeFlashURLProvider", environment={}, responses={flash_path: '<XML><update version="32,0,0,465"/></XML>'}),
                dict(name="Flash unexpected XML", processor="AdobeFlashURLProvider", environment={}, status=10, responses={flash_path: '<other/>'}),
                dict(name="Reader API parameters", processor="AdobeReaderURLProvider", environment={"os_version": "10.14.0"}, responses={products_path: json.dumps({"products": {"reader": [{"displayName": "Reader DC for Mac", "version": "22.001.20112"}]}}), download_path: json.dumps({"downloadURL": "https://example.invalid/reader.dmg", "saveName": "reader.dmg"})}),
                dict(name="Reader malformed products", processor="AdobeReaderURLProvider", environment={}, responses={products_path: '{}'}, status=1),
                dict(name="Puppet latest stable", processor="PuppetlabsProductsURLProvider", environment={"product_name": "Puppet"}, responses={"/mac": 'href="puppet-3.9.dmg" href="puppet-3.10.dmg" href="puppet-4.0-rc.dmg"'}),
                dict(name="Puppet requested version", processor="PuppetlabsProductsURLProvider", environment={"product_name": "puppet", "get_version": "3.9"}, responses={"/mac": 'href="puppet-3.9.dmg" href="puppet-3.10.dmg"'}),
                dict(name="Puppet agent", processor="PuppetlabsProductsURLProvider", environment={"product_name": "agent"}, responses={"/mac/10.10/PC1/x86_64": 'href="puppet-agent-1.2.5-1.osx10.10.dmg"'}),
                dict(name="Puppet no candidate", processor="PuppetlabsProductsURLProvider", environment={"product_name": "facter"}, responses={"/mac": "no releases"}, status=10),
                dict(name="Acrobat previous update", processor="AdobeAcrobatProUpdateInfoProvider", environment={"major_version": "11"}, responses=acrobat_responses),
                dict(name="Acrobat unsupported major", processor="AdobeAcrobatProUpdateInfoProvider", environment={"major_version": "12"}, status=10),
                dict(name="Acrobat unsupported OS", processor="AdobeAcrobatProUpdateInfoProvider", environment={"major_version": "11", "target_os": "10.5"}, status=10),
            ]
            no_previous = dict(acrobat_responses)
            no_previous[base + "/11/11.0.99/com_adobe_Acrobat_Pro_univ_9.plist"] = plistlib.dumps(dict(current, PreviousURLTemplate="noTemplate"))
            cases.append(dict(name="Acrobat upstream noTemplate bug", processor="AdobeAcrobatProUpdateInfoProvider", environment={"major_version": "11"}, responses=no_previous, status=1))
            # The vendor config command is replaced by a shell fixture that records
            # arguments and writes only inside each isolated test directory.
            config = '#!/bin/sh\nprintf "%s\\n" "$@" > argv.txt\nexit 4\n'
            for label, script, status in [("argv and nonzero without stderr", config, 0), ("stderr failure", '#!/bin/sh\necho warning >&2\n', 10)]:
                cases.append(dict(name="Sassafras " + label, processor="SassafrasK2ClientCustomizer", status=status, environment={"base_pkg_path": "$ROOT/K2 Client.pkg", "k2clientconfig_path": "$ROOT/config", "k2clientconfig_options": "-s  server.example -v"}, files={"config": script, "K2 Client.pkg": "fixture package"}, modes={"config": "644"}))
            cases.append(dict(name="Sassafras missing script", processor="SassafrasK2ClientCustomizer", status=10, environment={"base_pkg_path": "$ROOT/pkg", "k2clientconfig_path": "$ROOT/missing", "k2clientconfig_options": ""}))
            cases.extend(native_cases(root))
            verbose_cases = []
            for case in cases:
                if case.get("status", 0) == 0:
                    levels = (1,) if case["processor"] == "AdobeReaderRepackager" else (1, 3)
                    for level in levels:
                        verbose_cases.append(dict(case, name=case["name"] + f" verbosity {level}",
                                                  environment=dict(case["environment"], verbose=level),
                                                  compare_logs=True))
            cases.extend(verbose_cases)
            for index, case in enumerate(cases):
                case["environment"].update(CURL_PATH=str(wrapper))
                case["environment"].setdefault("verbose", 0)
                state["responses"] = case.get("responses", {})
                state["requests"] = []
                expected, py_error = outcome(case, root / f"python-{index}", reference)
                expected_requests = list(state["requests"])
                state["requests"] = []
                actual, rs_error = outcome(case, root / f"rust-{index}", rust)
                actual_requests = list(state["requests"])
                # The recorded argument fixture contains its isolated package path.
                for result, fixture in [(expected, root / f"python-{index}"), (actual, root / f"rust-{index}")]:
                    if (fixture / "argv.txt").exists():
                        result["files"]["argv.txt"] = {"kind": "argv", "value": harness.normalize((fixture / "argv.txt").read_text(), fixture)}
                expected_status = case.get("status", 0)
                actual_status = expected_status
                passed = expected["status"] == expected_status and actual["status"] == actual_status and expected_requests == actual_requests
                passed = passed and expected == actual
                record = dict(name=case["name"], processor=case["processor"], passed=passed, python_status=expected["status"], rust_status=actual["status"], requests=actual_requests)
                evidence.append(record)
                print(f"{'PASS' if passed else 'FAIL'} {case['name']}", flush=True)
                if not passed:
                    print(json.dumps({"python": expected, "rust": actual, "python_error": py_error, "rust_error": rs_error, "python_requests": expected_requests, "rust_requests": actual_requests}, indent=2, default=str))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    if args.coverage_output:
        args.coverage_output.parent.mkdir(parents=True, exist_ok=True)
        args.coverage_output.write_text(json.dumps(evidence, indent=2) + "\n")
    failed = sum(not item["passed"] for item in evidence)
    print(f"{len(evidence) - failed}/{len(evidence)} legacy community comparisons passed")
    return bool(failed)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        raise SystemExit(worker(sys.argv[2]))
    raise SystemExit(main())
