#!/usr/bin/env python3
"""Capture the pinned Python API without importing or executing processor code."""
import argparse
import ast
import copy
import hashlib
import json

from compat_paths import COMPATIBILITY, REFERENCE_COMMIT as REFERENCE, reference_text

NAMES = """DeprecationWarning EndOfCheckPhase PackageRequired StopProcessingIf VariableSetter Copier FileCreator FileFinder FileMover FindAndReplace PathDeleter Symlinker GitHubReleasesInfoProvider SparkleUpdateInfoProvider URLDownloader URLDownloaderPython URLGetter URLTextSearcher AppDmgVersioner PkgInfoCreator PlistEditor PlistReader Versioner AppPkgCreator DmgCreator DmgMounter FlatPkgPacker FlatPkgUnpacker InstallFromDMG Installer PkgCopier PkgCreator PkgExtractor PkgPayloadUnpacker PkgRootCreator Unarchiver MunkiCatalogBuilder MunkiImporter MunkiInfoCreator MunkiInstallsItemsCreator MunkiOptionalReceiptEditor MunkiPkginfoMerger MunkiSetDefaultCatalog ChocolateyPackager CodeSignatureVerifier SignToolVerifier""".split()
FIELDS = ("description", "input_variables", "output_variables", "lifecycle")


def source(path):
    return reference_text(path)


def capture():
    if len(NAMES) != 46 or len(set(NAMES)) != 46:
        raise ValueError("The compatibility inventory must contain exactly 46 unique processors")
    sources = {name: source(f"Code/autopkglib/{name}.py") for name in NAMES}
    constants_source = source("Code/nuget/ChocolateyInstallGenerator.py")
    constants = {n.target.id: ast.literal_eval(n.value) for n in ast.parse(constants_source).body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id.startswith("CHOCO_")}
    manifests = {}

    def manifest(name):
        if name == "Processor":
            return dict(description=None, input_variables={}, output_variables={}, lifecycle={})
        if name in manifests:
            return manifests[name]
        tree = ast.parse(sources[name])
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
        result = copy.deepcopy(manifest(next((b.id for b in cls.bases if isinstance(b, ast.Name) and (b.id in NAMES or b.id == "Processor")), "Processor")))
        local = {**constants, "DefaultValue": {"python_sentinel": "ChocolateyPackager.DefaultValue"}, "__doc__": ast.get_docstring(cls, clean=False)}

        def value(node):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"signtool_default_path", "_default_use_python_native_extractor"}:
                return {"python_expression": ast.unparse(node)}
            if isinstance(node, ast.JoinedStr):
                return "".join(str(value(v.value)) if isinstance(v, ast.FormattedValue) else v.value for v in node.values)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join":
                return value(node.func.value).join(value(node.args[0]))
            if isinstance(node, ast.Dict):
                result = {}
                for key, item in zip(node.keys, node.values):
                    if key is None:
                        result.update(value(item))
                    else:
                        result[value(key)] = value(item)
                return result
            if isinstance(node, ast.Name):
                return local[node.id]
            if isinstance(node, ast.Attribute):
                return manifest(node.value.id)[node.attr]
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "copy":
                return copy.deepcopy(value(node.func.value))
            try:
                return ast.literal_eval(node)
            except ValueError as exc:
                raise ValueError(f"{name}: unsupported {ast.unparse(node)}") from exc

        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        try:
                            local[target.id] = value(node.value)
                        except (ValueError, KeyError, AttributeError):
                            pass

        for node in cls.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and node.value is not None:
                        try:
                            local[target.id] = value(node.value)
                        except (ValueError, KeyError, AttributeError):
                            if target.id in FIELDS:
                                raise
                            continue
                        if target.id in FIELDS:
                            result[target.id] = local[target.id]
                    elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id in FIELDS:
                        local[target.value.id][value(target.slice)] = value(node.value)
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id in FIELDS:
                    if call.func.attr != "pop":
                        raise ValueError(f"Unsupported manifest mutation: {ast.unparse(node)}")
                    local[call.func.value.id].pop(value(call.args[0]))
        manifests[name] = result
        return result

    for name in sorted(NAMES):
        manifest(name)
    cli_source = source("Code/autopkg")
    tree = ast.parse(cli_source)
    commands = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "subcommands" for t in n.targets))
    subcommands = {}
    for key, val in zip(commands.keys, commands.values):
        subcommands[ast.literal_eval(key)] = {ast.literal_eval(k): v.id if isinstance(v, ast.Name) else ast.literal_eval(v) for k, v in zip(val.keys, val.values)}
    options = {}
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_option":
                def literal_or_source(n):
                    try:
                        return ast.literal_eval(n)
                    except (ValueError, TypeError):
                        return {"python_expression": ast.unparse(n)}
                options.setdefault(fn.name, []).append({"flags": [literal_or_source(a) for a in node.args], "settings": {kw.arg: literal_or_source(kw.value) for kw in node.keywords}})
    return {"schema_version": 1, "reference_commit": REFERENCE, "processors": manifests, "cli": {"subcommands": subcommands, "options_by_function": options}, "source_sha256": {**{f"Code/autopkglib/{n}.py": hashlib.sha256(s.encode()).hexdigest() for n, s in sources.items()}, "Code/autopkg": hashlib.sha256(cli_source.encode()).hexdigest(), "Code/nuget/ChocolateyInstallGenerator.py": hashlib.sha256(constants_source.encode()).hexdigest()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the frozen contract differs from the pinned source")
    args = parser.parse_args()
    output = COMPATIBILITY / "reference.json"
    data = json.dumps(capture(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not output.exists() or output.read_text() != data:
            parser.exit(1, "Frozen contract differs; run Scripts/capture_rust_contract.py to regenerate.\n")
        print(f"Verified {len(NAMES)} processor manifests and CLI contract at {REFERENCE}.")
    else:
        output.write_text(data)
        print(f"Wrote {output}")


if __name__ == "__main__":
    main()
