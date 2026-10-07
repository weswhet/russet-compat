#!/usr/bin/env python3
"""Compare TLS trust and hostname failures with an isolated local HTTPS server."""
import argparse
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading

import differential_rust_http as http


@contextmanager
def tls_environment(certificate, capath=None):
    updates = {"SSL_CERT_DIR": str(capath) if capath is not None else None,
               "SSL_CERT_FILE": str(certificate) if certificate is not None else None,
               "CURL_CA_BUNDLE": None, "REQUESTS_CA_BUNDLE": None,
               "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1"}
    previous = {name: os.environ.get(name) for name in updates}
    try:
        for name, value in updates.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--rust", type=Path, default=http.harness.RUST_DEBUG_CLI)
    args = parser.parse_args()
    python_path = str(Path(args.python).absolute())
    version = subprocess.check_output([python_path, "-c", "import platform; print(platform.python_version())"], text=True).strip()
    if version != "3.11.9":
        parser.error(f"Release reference requires Python 3.11.9, found {version}")
    openssl = shutil.which("openssl")
    if not openssl:
        parser.error("OpenSSL is required to create the isolated certificate fixture")
    curl_version = subprocess.check_output(["curl", "--version"], text=True)
    schannel = "Schannel" in curl_version
    print(f"TLS transport: {curl_version.splitlines()[0]}", flush=True)
    # On macOS the release Python, including its sitecustomize, is the oracle.
    with tls_environment("/nonexistent-autopkg-ca-probe.pem", ""):
        fallback = subprocess.check_output(
            [python_path, "-c", "import os; print(os.environ.get('SSL_CERT_FILE', ''))"], text=True).strip()
    shipped_macos = sys.platform == "darwin" and fallback != "/nonexistent-autopkg-ca-probe.pem"
    if sys.platform == "darwin" and not shipped_macos:
        parser.error("macOS TLS parity requires the Python interpreter shipped in AutoPkg 3.0.0 (including sitecustomize)")
    with tls_environment(None, ""):
        defaults = json.loads(subprocess.check_output([python_path, "-c", """
import certifi, hashlib, json, ssl
def roots(context):
    return sorted(hashlib.sha256(c).hexdigest() for c in context.get_ca_certs(binary_form=True))
print(json.dumps({'paths': ssl.get_default_verify_paths()._asdict(),
                  'default_roots': roots(ssl.create_default_context()),
                  'certifi_roots': roots(ssl.create_default_context(cafile=certifi.where()))}))
"""], text=True))
    if shipped_macos:
        assert defaults["default_roots"] == defaults["certifi_roots"]
        assert len(defaults["default_roots"]) == 147
        http.reference_certifi(python_path)  # Bind the oracle to the exact shipped PEM.
        print("PASS shipped macOS default roots are exactly pinned certifi (147 roots)", flush=True)
    elif sys.platform.startswith("linux"):
        assert defaults["paths"]["openssl_cafile"] == "/usr/lib/ssl/cert.pem", defaults["paths"]
        assert defaults["paths"]["capath"] is None
        assert defaults["default_roots"], "Default CA file must remain active with an empty CA directory"
        print("PASS Linux default CA file remains active with SSL_CERT_DIR empty", flush=True)
    failures = 0
    with tempfile.TemporaryDirectory(prefix="autopkg-tls-differential-") as temporary:
        root = Path(temporary).resolve()
        certificate, key = root / "localhost.crt", root / "localhost.key"
        config = root / "certificate.cnf"
        config.write_text("[req]\nprompt=no\ndistinguished_name=subject\nx509_extensions=extensions\n"
                          "[subject]\nCN=localhost\n[extensions]\nsubjectAltName=DNS:localhost\n"
                          "basicConstraints=critical,CA:true\nkeyUsage=digitalSignature,keyEncipherment,keyCertSign\n"
                          "extendedKeyUsage=serverAuth\n")
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-config", str(config), "-keyout", str(key), "-out", str(certificate)],
                       check=True, capture_output=True)
        archive = http.harness.reference_archive()
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(root / "reference")
        python = [python_path, str(Path(http.harness.__file__).resolve()), "--worker", str(root / "reference/Code")]
        rust = [str(args.rust.resolve()), "processor-run"]
        server = http.ThreadingHTTPServer(("127.0.0.1", 0), http.Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cases = []
        # Only OpenSSL hash-indexed CApath entries should contribute trust.
        certifi_path = subprocess.check_output(
            [python_path, "-c", "import certifi; print(certifi.where())"], text=True).strip()
        subject_hash = subprocess.check_output(
            [openssl, "x509", "-subject_hash", "-noout", "-in", str(certificate)], text=True).strip()
        for layout, filename, expected_status in [
                ("hashed", f"{subject_hash}.0", 0),
                ("unhashed", "localhost.pem", 1),
                ("gap", f"{subject_hash}.1", 0),
                ("leading-zero", f"{subject_hash}.01", 0),
                ("signed-index", f"{subject_hash}.+1", 1),
                ("wrong-hash", "00000000.0", 1)]:
            directory = root / f"capath-{layout}"
            directory.mkdir()
            shutil.copyfile(certificate, directory / filename)
            cases.append({"name": f"URLDownloaderPython-capath-{layout}",
                          "processor": "URLDownloaderPython", "mode": "trusted",
                          "environment": {"RECIPE_CACHE_DIR": "$ROOT", "NAME": "TLSFixture"},
                          "certificate": certifi_path, "capath": directory,
                          "path": "/artifact.bin", "status": expected_status})
        cases.append({"name": "URLDownloaderPython-empty-capath-custom-file",
                      "processor": "URLDownloaderPython", "mode": "trusted",
                      "environment": {"RECIPE_CACHE_DIR": "$ROOT", "NAME": "TLSFixture"},
                      "certificate": certificate, "capath": "",
                      "path": "/artifact.bin", "status": 0})
        # Existing invalid PEMs are not replaced by sitecustomize.
        invalid_pem = root / "empty.pem"
        invalid_pem.write_bytes(b"")
        cases.append({"name": "URLDownloaderPython-existing-empty-pem",
                      "processor": "URLDownloaderPython", "mode": "trusted",
                      "environment": {"RECIPE_CACHE_DIR": "$ROOT", "NAME": "TLSFixture"},
                      "certificate": invalid_pem, "capath": "",
                      "path": "/artifact.bin", "status": 1})
        for processor in ["URLDownloader", "URLDownloaderPython", "URLTextSearcher"]:
            environment = ({"re_pattern": r"version=([\d.]+)"} if processor == "URLTextSearcher"
                           else {"RECIPE_CACHE_DIR": "$ROOT", "NAME": "TLSFixture"})
            for mode in ["trusted", "untrusted", "wrong-hostname"]:
                case_environment = dict(environment)
                # Native Schannel ignores SSL_CERT_FILE. Explicit trust keeps this
                # case about certificate/hostname verification on every backend.
                if processor != "URLDownloaderPython" and mode != "untrusted":
                    case_environment["curl_opts"] = ["--cacert", str(certificate)]
                cases.append({"name": f"{processor}-{mode}", "processor": processor, "mode": mode,
                              "environment": case_environment, "path": "/artifact.bin",
                              "status": 0 if mode == "trusted" else (1 if processor == "URLDownloaderPython" else 10)})
            if processor != "URLDownloaderPython":
                if shipped_macos:
                    # Invalid overrides are replaced before curl starts. Compare
                    # its actual error log: a valid certifi store rejects this
                    # local issuer (60), rather than failing to open a CA file (77).
                    for label, override in [("absent", None), ("missing", root / "missing.pem"),
                                            ("empty", ""), ("directory", root)]:
                        cases.append({"name": f"{processor}-shipped-ca-fallback-{label}",
                                      "processor": processor, "mode": "untrusted",
                                      "environment": {**environment, "verbose": 1,
                                                      "curl_opts": ["--silent", "--show-error"]},
                                      "certificate": override, "path": "/artifact.bin",
                                      "status": 10, "compare_logs": True})
                # Preserve the native tool's environment trust behavior separately.
                # https://curl.se/docs/sslcerts.html
                cases.append({"name": f"{processor}-environment-ca", "processor": processor, "mode": "trusted",
                              "environment": dict(environment), "path": "/artifact.bin",
                              "status": 10 if schannel else 0})
                cases.append({"name": f"{processor}-explicit-ca", "processor": processor, "mode": "untrusted",
                              "environment": {**environment, "curl_opts": ["--cacert", str(certificate)]},
                              "path": "/artifact.bin", "status": 0})
            else:
                for label, cafile in [("missing", str(root / "missing.pem")), ("empty", "")]:
                    cases.append({"name": f"{processor}-{label}-ca", "processor": processor, "mode": "trusted",
                                  "environment": dict(environment), "path": "/artifact.bin",
                                  "certificate": cafile, "status": 1 if shipped_macos else 10})
                cases.append({"name": f"{processor}-ignores-curl-insecure", "processor": processor, "mode": "untrusted",
                              "environment": {**environment, "curl_opts": ["--insecure"]},
                              "path": "/artifact.bin", "status": 1})
        try:
            for index, case in enumerate(cases):
                hostname = "127.0.0.1" if case["mode"] == "wrong-hostname" else "localhost"
                url = f"https://{hostname}:{server.server_port}"
                with tls_environment(case.get("certificate", certificate if case["mode"] != "untrusted" else None),
                                     case.get("capath")):
                    expected, python_error = http.run(case, python, root / f"python-{index}", url, server)
                    actual, rust_error = http.run(case, rust, root / f"rust-{index}", url, server)
                passed = expected == actual and all(run["status"] == case["status"] for run in expected["runs"])
                print(f"{'PASS' if passed else 'FAIL'} {case['name']}", flush=True)
                if not passed:
                    failures += 1
                    print(json.dumps({"python": expected, "rust": actual}, indent=2, default=str))
                    print(f"Python stderr: {python_error}\nRust stderr: {rust_error}")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print(f"{len(cases) - failures}/{len(cases)} TLS cases passed.")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
