#!/usr/bin/env python3
"""Extract the exact published Python reference without installing it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PACKAGE_URL = "https://github.com/autopkg/autopkg/releases/download/v3.0.0/autopkg-3.0.0.pkg"
PACKAGE_SHA256 = "cd554cc4b807dec7fb0aee73fe9ab0cddf764e215fb6f10c1b7f7d263c52f8cf"
CERTIFI_SHA256 = "2089fc5a25836401faee99f722460b01b393999746a0fb817943666d6ecc0458"
REFERENCE_COMMIT = "c36e58f8d3d8ddb70b6c2d848d2ceca7f767ce5c"
PYTHON = Path("Payload/Library/AutoPkg/Python3/Python.framework/Versions/3.11/bin/python3.11")


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verify_package(path):
    if sha256(path) != PACKAGE_SHA256:
        raise ValueError("Reference package does not match the pinned release SHA-256")


def inspect_runtime(executable):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for key in ("SSL_CERT_FILE", "SSL_CERT_DIR", "PYTHONHOME", "PYTHONPATH"):
        environment.pop(key, None)
    code = """
import appdirs, certifi, hashlib, json, lxml, os, platform, ssl, xattr, yaml
import CoreFoundation, Foundation, Quartz, SystemConfiguration, LaunchServices
print(json.dumps({
    'python_version': platform.python_version(),
    'certifi_version': certifi.__version__,
    'certifi_path': certifi.where(),
    'certifi_sha256': hashlib.sha256(open(certifi.where(), 'rb').read()).hexdigest(),
    'effective_ssl_cert_file': os.environ.get('SSL_CERT_FILE'),
    'openssl_version': ssl.OPENSSL_VERSION,
    'verify_paths': ssl.get_default_verify_paths()._asdict(),
    'default_store': ssl.create_default_context().cert_store_stats(),
}))
"""
    result = subprocess.run([str(executable), "-c", code], env=environment,
                            text=True, capture_output=True, check=True)
    record = json.loads(result.stdout)
    if (record["python_version"] != "3.11.9" or
            record["certifi_version"] != "2025.10.05" or
            record["certifi_sha256"] != CERTIFI_SHA256 or
            record["effective_ssl_cert_file"] != record["certifi_path"]):
        raise ValueError("Extracted reference runtime does not match the pinned release")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package", type=Path, help="Use a previously downloaded, hash-verified package")
    parser.add_argument("--github-env", type=Path, help="Append the reference interpreter to this workflow environment file")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("The published reference interpreter requires macOS")
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error("Output must be a new directory")
    with tempfile.TemporaryDirectory(prefix="autopkg-reference-download-") as temp:
        package = args.package.absolute() if args.package else Path(temp) / "autopkg-3.0.0.pkg"
        if args.package is None:
            subprocess.run(["/usr/bin/curl", "--fail", "--location", "--output", str(package),
                            PACKAGE_URL], check=True)
        verify_package(package)
        output.mkdir(parents=True)
        extracted = output / "expanded"
        subprocess.run(["/usr/sbin/pkgutil", "--expand-full", str(package), str(extracted)], check=True)
        executable = extracted / PYTHON
        record = inspect_runtime(executable)
        record.update(reference_commit=REFERENCE_COMMIT, package_url=PACKAGE_URL,
                      package_sha256=PACKAGE_SHA256, executable=str(executable))
        (output / "reference-runtime.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        if args.github_env:
            if "\n" in str(executable) or "\r" in str(executable):
                raise ValueError("Reference interpreter path cannot contain a newline")
            with args.github_env.open("a", encoding="utf-8") as destination:
                destination.write(f"REFERENCE_PYTHON={executable}\nPYTHONDONTWRITEBYTECODE=1\n")
        print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
