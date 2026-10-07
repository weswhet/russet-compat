#!/usr/bin/env python3
"""Freeze makepkginfo option declarations from the pinned official Munki source."""
import argparse
import hashlib
import json
import re
import subprocess

from compat_paths import COMPATIBILITY

COMMIT = "8896fe831e870732aac760f76566762fc35d5d00"
PATHS = ["code/cli/munki/shared/admin/pkginfoOptions.swift", "code/cli/munki/makepkginfo/makepkginfo.swift"]


def long_name(name):
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name).lower()


def capture():
    files, options = [], []
    for path in PATHS:
        url = f"https://raw.githubusercontent.com/munki/munki/{COMMIT}/{path}"
        raw = subprocess.check_output(["curl", "--fail", "--silent", "--show-error", "--location", url])
        text = raw.decode()
        files.append({"path": path, "url": url, "sha256": hashlib.sha256(raw).hexdigest()})
        for match in re.finditer(r"@(Option|Flag)\((.*?)\)\s*var\s+(\w+)([^\n]*)", text, re.S):
            kind, attr, name, declaration = match.groups()
            group = list(re.finditer(r"struct (\w+):", text[:match.start()]))[-1].group(1)
            # Extract the explicit NameSpecification independently from help text.
            naming = re.search(r"name:\s*(\[[^\]]*\]|\.\w+)", attr)
            names = naming.group(1) if naming else ".long"
            flags = []
            if re.search(r"\.long\b|\.shortAndLong\b", names):
                flags.append("--" + long_name(name))
            if re.search(r"\.short\b|\.shortAndLong\b", names):
                flags.append("-" + name[0])
            flags += ["--" + n for n in re.findall(r'\.customLong\("([^\"]+)"\)', names)]
            flags += ["-" + n for n in re.findall(r'\.customShort\("([^\"]+)"\)', names)]
            if "inversion: .prefixedNo" in attr:
                flags.append("--no-" + long_name(name))
            options.append({"group": group, "property": name, "kind": kind, "flags": flags,
                            "swift_declaration": "var " + name + declaration,
                            "source_path": path, "source_line": text[:match.start()].count("\n") + 1})
    return {"schema_version": 1, "munki_version": "7.2.0.5787", "tag": "v7.2.0", "commit": COMMIT,
            "release_url": "https://github.com/munki/munki/releases/tag/v7.2.0", "sources": files,
            "options": options, "implicit_framework_options": ["-h", "--help"],
            "positional_arguments": [{"name": "installer-item", "type": "String?"}],
            "enum_values": {"RestartAction": ["RequireRestart", "RecommendRestart", "RequireLogout"],
                            "InstallerType": ["copy_from_dmg", "stage_os_installer"],
                            "SupportedArchitecture": ["x86_64", "arm64"]},
            "limitations": "Static declaration capture; metadata effects and validation semantics require runtime parity tests."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = COMPATIBILITY / "munki-makepkginfo-options.json"
    data = json.dumps(capture(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if path.read_text() != data:
            parser.exit(1, "Pinned Munki option capture differs.\n")
        print("Verified pinned Munki 7.2.0.5787 option declarations.")
    else:
        path.write_text(data)
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
