#!/usr/bin/env python3
"""Compare active Windows packaging/signatures with pinned Python and real tools.

No package is installed. Only ZIP timestamps, NuGet core-property UUIDs/timestamps,
unordered OPC content-type declarations, Chocolatey log timestamp/process-ID
prefixes, its seven exact optional promotional banners, sentinel addresses, and isolated fixture/build
paths are normalized. Installer scripts and payload member bytes remain exact.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import xml.etree.ElementTree as ET

import differential_rust_processors as harness


def content_types(data):
    # OPC content-type mappings are keyed by Extension or PartName, not position.
    # Compare a sorted list, never a dict/set: duplicate declarations remain visible.
    # https://ecma-international.org/publications-and-standards/standards/ecma-376/
    tree = ET.fromstring(data)
    ns = "{http://schemas.openxmlformats.org/package/2006/content-types}"
    if tree.tag != ns + "Types":
        raise ValueError("Unexpected OPC content-types root")
    def node(element):
        return [element.tag, sorted(element.attrib.items()), element.text or "",
                [node(child) for child in element], element.tail or ""]
    declarations = []
    for element in tree:
        if element.tag not in {ns + "Default", ns + "Override"}:
            raise ValueError("Unexpected OPC content-type declaration")
        declarations.append(node(element))
    return {"tag": tree.tag, "attributes": sorted(tree.attrib.items()),
            "text": tree.text or "", "tail": tree.tail or "",
            "declarations": sorted(declarations, key=lambda item: json.dumps(item, sort_keys=True))}


# Exact _proBusinessMessages entries in Chocolatey 2.7.4, with the logger's
# trailing newline. The source strings each also start with a blank line.
# https://github.com/chocolatey/choco/blob/2.7.4/src/chocolatey/infrastructure.app/services/ChocolateyPackageService.cs#L61-L98
CHOCO_PROMOTIONS = (
    "Are you ready for the ultimate experience? Check out Pro / Business!\n"
    " https://chocolatey.org/compare\n",
    "Enjoy using Chocolatey? Explore more amazing features to take your\n"
    "experience to the next level at\n"
    " https://chocolatey.org/compare\n",
    "Did you know the proceeds of Pro (and some proceeds from other\n"
    " licensed editions) go into bettering the community infrastructure?\n"
    " Your support ensures an active community, keeps Chocolatey tip-top,\n"
    " plus it nets you some awesome features!\n"
    " https://chocolatey.org/compare\n",
    "Did you know some organizations use Chocolatey completely internally\n"
    " without using the community repository or downloads from the internet?\n"
    " Wait until you see how Package Builder and Package Internalizer can\n"
    " help you achieve more, quicker, and easier! Get your trial started\n"
    " today at https://chocolatey.org/compare\n",
    "An organization needed total software management life cycle automation.\n"
    " They evaluated Chocolatey for Business. You won't believe what happens\n"
    " next!\n"
    " https://chocolatey.org/compare\n",
    "Did you know that Package Synchronizer and AutoUninstaller enhancements\n"
    " in licensed versions are up to 95% effective in removing system\n"
    " installed software without an uninstall script? Find out more at\n"
    " https://chocolatey.org/compare\n",
    "Did you know Chocolatey goes to eleven? And it turns great developers /\n"
    " system admins into something amazing! Singlehandedly solve your\n"
    " organization's struggles with software management and save the day!\n"
    " https://chocolatey.org/compare\n",
)


def chocolatey_promotion(content, processor=False):
    # Remove only full known random advertisements, including their own leading
    # blank log line. Changed text, warnings, and adjacent output stay significant.
    for message in CHOCO_PROMOTIONS:
        if processor:
            block = "ChocolateyPackager: \n" + "".join(
                "ChocolateyPackager: " + line for line in message.splitlines(keepends=True))
        else:
            block = "$TIMESTAMP $PID [WARN ] - \n" + message
        content = re.sub(r"(?m)^" + re.escape(block), "", content)
    return content


def chocolatey_log(content):
    # Only the standard log-prefix timestamp and process ID are nondeterministic.
    content = re.sub(r"(?m)^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,.]\d+ \d+ (?=\[[A-Z ]+\] -)",
                     "$TIMESTAMP $PID ", content)
    return chocolatey_promotion(content)


def package_contents(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != len(set(members)):
            raise ValueError("Duplicate NuGet member names")
        core = [name for name in members if re.fullmatch(
            r"package/services/metadata/core-properties/[0-9a-fA-F]+\.psmdcp", name)]
        if len(core) != 1:
            raise ValueError(f"Expected exactly one NuGet core-property member: {core}")
        result = {}
        for name in sorted(members):
            data = archive.read(name)
            canonical_name = name.replace(core[0], "package/services/metadata/core-properties/$UUID.psmdcp")
            if name == "[Content_Types].xml":
                result[canonical_name] = {"content_types": content_types(data)}
            elif name == core[0] or name == "_rels/.rels":
                tree = ET.fromstring(data)
                for element in tree.iter():
                    if element.tag.rsplit("}", 1)[-1] in {"created", "modified"}:
                        element.text = "$TIMESTAMP"
                    for key, value in list(element.attrib.items()):
                        element.attrib[key] = value.replace(core[0], "package/services/metadata/core-properties/$UUID.psmdcp")
                if name == "_rels/.rels":
                    # Relationship IDs are fresh opaque UUIDs; targets carry identity.
                    for relationship in tree:
                        relationship.set("Id", "$RELATIONSHIP:" + relationship.attrib["Target"])
                result[canonical_name] = {"xml": ET.tostring(tree, encoding="unicode")}
            else:
                result[canonical_name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        return result


def normalize(value, root, build):
    if isinstance(value, str):
        value = value.replace(str(root.parent / (root.name + "-home")), "$HOME")
        value = harness.normalize(value, root)
        if build:
            value = value.replace(build, "$BUILD")
        return value
    if isinstance(value, dict):
        return {normalize(k, root, build): normalize(v, root, build) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, root, build) for v in value]
    return value


def run(case, root, command):
    result, error = harness.run_case(case, root, command)
    if case["processor"] == "ChocolateyPackager" and "processor_logs" in result:
        result["processor_logs"] = chocolatey_promotion(result["processor_logs"], processor=True)
    environment = result["environment"] or {}
    build = environment.get("choco_build_directory")
    # Retained builds are represented by their generated identity, not temp suffix.
    for path in root.rglob("*.nupkg"):
        result["files"][path.relative_to(root).as_posix()] = {"package": package_contents(path)}
    for path in root.rglob("*.log"):
        content = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        if case["processor"] == "ChocolateyPackager":
            content = chocolatey_log(content)
        result["files"][path.relative_to(root).as_posix()] = {"text": content}
    if build:
        build_relative = build.replace("$ROOT", "").lstrip("/\\").replace("\\", "/")
        result["files"] = {("$BUILD" + name[len(build_relative):] if name == build_relative or name.startswith(build_relative + "/") else name): value
                           for name, value in result["files"].items()}
        result["build_exists"] = Path(harness.replace(build, root)).is_dir()
    if case.get("expected_error"):
        result["terminal_error"] = error.partition("ProcessorError: ")[2].strip()
    return normalize(result, root, build), error


def reference_signtool(python, code):
    probe = """
import contextlib, json, sys
sys.path.insert(0, sys.argv[1])
with contextlib.redirect_stdout(sys.stderr):
    from autopkglib.SignToolVerifier import signtool_default_path
    path = signtool_default_path()
print(json.dumps(path))
"""
    result = subprocess.run([python, "-c", probe, str(code)], capture_output=True, text=True)
    sys.stderr.write(result.stderr)
    result.check_returncode()
    path = json.loads(result.stdout)
    if path is not None and not isinstance(path, str):
        raise ValueError("Reference signtool probe must return a JSON string or null")
    return path


def discover_signtool(program_roots):
    """Use the native smoke gate's SDK search, independently of Python defaults."""
    for program_root in program_roots:
        if not program_root:
            continue
        kits = Path(program_root) / "Windows Kits/10"
        candidates = sorted((path for path in (kits / "bin").rglob("signtool.exe")
                             if path.is_file() and path.parent.name == "x64"),
                            key=lambda path: str(path), reverse=True)
        fallback = kits / "App Certification Kit/signtool.exe"
        if fallback.is_file():
            candidates.append(fallback)
        if candidates:
            return str(candidates[0])
    raise RuntimeError("No real x64 Windows SDK signtool is installed in the runner image")


def cases(choco, signtool, signed, unsigned, default_signtool=None):
    base = {"RECIPE_CACHE_DIR": "$ROOT", "id": "autopkg.native.fixture", "version": "1.2.3",
            "title": "AutoPkg native fixture", "authors": "AutoPkg", "description": "Isolated test payload",
            "installer_type": "zip", "pathname": "$ROOT/payload.zip", "verbose": 2}
    archive = {"payload.zip": {"fixture.txt": "Native packaging test; never installed.\n"}}
    result = []
    for name, changes in [
        ("chocolatey-defaults", {}),
        ("chocolatey-explicit-retained", {"chocoexe_path": choco, "KEEP_BUILD_DIRECTORY": True,
             "installer_path": "$ROOT/payload.zip", "installer_checksum_type": "sha256"}),
        ("chocolatey-missing-pathname", {"pathname": "$ROOT/missing.zip"}),
        ("chocolatey-conflicting-installers", {"installer_path": "$ROOT/payload.zip", "installer_url": "https://invalid.example/payload.zip"}),
    ]:
        result.append({"name": name, "processor": "ChocolateyPackager", "environment": {**base, **changes},
                       "zip_archives": archive, "compare_logs": True,
                       "expected_status": 10 if name in {"chocolatey-missing-pathname", "chocolatey-conflicting-installers"} else 0})
    for label, source, status in [("signed", signed, 0), ("unsigned", unsigned, 10)]:
        for mode, options in [("defaults", {}),
                              ("explicit-default-arguments", {"signtool_path": signtool}),
                              ("explicit", {"signtool_path": signtool, "additional_arguments": ["/a"]})]:
            missing_default = mode == "defaults" and default_signtool is None
            case = {"name": f"signtool-{label}-{mode}", "processor": "SignToolVerifier",
                    "environment": {"input_path": "$ROOT/input.exe", "verbose": 2, **options},
                    "copy_files": {"input.exe": source}, "compare_logs": True,
                    "expected_status": 10 if missing_default else status}
            if missing_default:
                case["expected_error"] = "No signtool_path configured. Set signtool_path to the path to signtool.exe."
            result.append(case)
    result.append({"name": "signtool-missing-tool", "processor": "SignToolVerifier",
                   "environment": {"input_path": "$ROOT/input.exe", "signtool_path": "$ROOT/missing.exe", "verbose": 2},
                   "copy_files": {"input.exe": unsigned}, "compare_logs": True, "expected_status": 10})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--rust", required=True, type=Path)
    parser.add_argument("--results", type=Path, default=Path("windows-native-differential.json"))
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows; no emulated or skipped success is reported")
    python = str(Path(shutil.which(args.python) or args.python).absolute())
    rust = args.rust.resolve()
    version = subprocess.check_output([python, "-c", "import platform; print(platform.python_version())"], text=True).strip()
    if version != "3.11.9":
        parser.error(f"Expected pinned Python 3.11.9, found {version}")
    choco = Path(r"C:\ProgramData\chocolatey\bin\choco.exe")
    if not choco.is_file():
        parser.error(f"Required default Chocolatey executable is missing: {choco}")
    with tempfile.TemporaryDirectory(prefix="autopkg-windows-native-") as temporary:
        root = Path(temporary).resolve()
        source = harness.reference_archive()
        with tarfile.open(fileobj=io.BytesIO(source)) as archive:
            archive.extractall(root / "reference")
        code = root / "reference/Code"
        default_signtool = reference_signtool(python, code)
        if default_signtool is not None and not Path(default_signtool).is_file():
            parser.error(f"Pinned Python discovered a missing tool: {default_signtool}")
        signtool = discover_signtool([os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")])
        system = Path(os.environ["SystemRoot"]) / "System32"
        candidates = [system / "WindowsPowerShell/v1.0/powershell.exe", system / "cmd.exe", system / "where.exe", system / "reg.exe"]
        base_python = subprocess.check_output([python, "-c", "import sys; print(sys._base_executable)"], text=True).strip()
        candidates.extend([Path(python), Path(base_python), Path(signtool)])
        verification_tools = list(dict.fromkeys([signtool] + ([default_signtool] if default_signtool else [])))
        signed = None
        for candidate in candidates:
            # Require an embedded signature accepted with omitted additional_arguments.
            if candidate.is_file() and all(subprocess.run([tool, "verify", "/v", "/pa", str(candidate)], capture_output=True).returncode == 0 for tool in verification_tools):
                signed = str(candidate)
                break
        if signed is None:
            parser.error("No signed system, pinned Python, or SDK fixture passes /pa verification without /a")
        if subprocess.run([signtool, "verify", "/v", "/pa", "/a", str(rust)], capture_output=True).returncode != 1:
            parser.error("Negative fixture must be the unsigned development CLI (signtool exit 1)")
        reference = [python, str(Path(harness.__file__).resolve()), "--worker", str(code)]
        native = [str(rust), "processor-run"]
        records = []
        for index, case in enumerate(cases(str(choco), signtool, signed, str(rust), default_signtool)):
            expected, expected_error = run(case, root / f"python-{index}", reference)
            actual, actual_error = run(case, root / f"rust-{index}", native)
            passed = (expected == actual and expected["status"] == case["expected_status"]
                      and (not case.get("expected_error") or expected.get("terminal_error") == case["expected_error"]))
            print(f"{'PASS' if passed else 'FAIL'} {case['name']}", flush=True)
            record = {"name": case["name"], "passed": passed, "expected_status": case["expected_status"]}
            if not passed:
                record.update(python=expected, rust=actual, python_stderr=expected_error, rust_stderr=actual_error)
                print(json.dumps(record, indent=2))
            records.append(record)
        args.results.write_text(json.dumps({"python": version, "reference_commit": harness.REFERENCE,
            "chocolatey_default": str(choco), "signtool_default": default_signtool, "signtool_explicit": signtool, "signed_fixture": signed,
            "cases": records}, indent=2), encoding="utf-8")
        passed = sum(record["passed"] for record in records)
        print(f"{passed}/{len(records)} native Windows differential cases passed.")
        return int(passed != len(records))


if __name__ == "__main__":
    raise SystemExit(main())
