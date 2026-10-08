#!/usr/bin/env python3
"""Compare the pinned CLI in separate homes, preferences, repositories and caches.

The extracted Python preference bundle ID and file configuration directory are
redirected to isolated test locations, preventing production preference reads. Its resulting empty-domain
warning is removed explicitly. Rust uses AUTOPKG_RS_PREFERENCES_FILE to bypass
native preferences. Neither CLI can write production preferences or caches.
"""
import argparse
import io
import json
import os
from pathlib import Path
import plistlib
import runpy
import re
import subprocess
import sys
import tarfile
import tempfile
import uuid
import threading
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from compat_paths import COMPATIBILITY, REFERENCE_COMMIT as REFERENCE, ROOT, RUST_DEBUG_CLI, reference_archive

SPEC = COMPATIBILITY / "cli-parser-reference.json"
REFERENCE_PYTHON = sys.executable
REFERENCE_PYTHON_VERSION = "3.11.9"


def worker(code, arguments, capture=False):
    sys.path.insert(0, code)
    sys.argv = ["autopkg"] + arguments
    if arguments == ["--capture-processor-order"]:
        import autopkglib
        def order(value):
            return [[key, order(item)] for key, item in value.items()] if isinstance(value, dict) else {"display": re.sub(r"object at 0x[0-9a-fA-F]+>", "object at 0x0>", str(value))}
        data={name: {field: order(getattr(autopkglib.get_processor(name), field, getattr(autopkglib.get_processor(name), "__doc__", "") if field == "description" else {}))
                     for field in ("description", "input_variables", "output_variables")}
              for name in autopkglib.core_processor_names()}
        # This reference default depends on the host SDK installation, not source.
        for key, fields in data["SignToolVerifier"]["input_variables"]:
            if key == "signtool_path":
                for field in fields:
                    if field[0] == "default": field[1]={"dynamic":"signtool_default_path"}
        print(json.dumps(data))
        return
    if capture:
        import optparse
        def parse(parser, *args, **kwargs):
            result = {"usage": parser.get_usage(), "help": parser.format_help(), "options": [
                {"flags": option._short_opts + option._long_opts,
                 "takes_value": option.takes_value(), "type": option.type,
                 "choices": option.choices, "nargs": option.nargs}
                for option in parser.option_list]}
            print(json.dumps(result))
            raise SystemExit(0)
        optparse.OptionParser.parse_args = parse
    runpy.run_path(str(Path(code) / "autopkg"), run_name="__main__")


def setup(root):
    root.mkdir()
    for name in ("home", "cache", "recipes", "overrides", "repos", "config/Autopkg", "tmp"):
        (root / name).mkdir(parents=True)
    prefs = {"CACHE_DIR": str(root / "cache"), "RECIPE_SEARCH_DIRS": [str(root / "recipes")],
             "RECIPE_OVERRIDE_DIRS": [str(root / "overrides")], "RECIPE_REPO_DIR": str(root / "repos"),
             "RECIPE_REPOS": {}, "RECIPE_MAP_PATH": str(root / "recipe_map.json"), "FAIL_RECIPES_WITHOUT_TRUST_INFO": False}
    (root / "prefs.plist").write_bytes(plistlib.dumps(prefs))
    (root / "config/Autopkg/config.plist").write_bytes(plistlib.dumps(prefs))
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root / "home"),
           "USERPROFILE": str(root / "home"), "XDG_CONFIG_HOME": str(root / "config"),
           "LOCALAPPDATA": str(root / "config"), "AUTOPKG_RS_PREFERENCES_FILE": str(root / "prefs.plist"),
           "AUTOPKG_RS_CACHE_DIR": str(root / "cache"), "COLUMNS": "80", "LC_ALL": "C", "TZ": "UTC"}
    env.update({"TMPDIR":str(root/"tmp"),"TMP":str(root/"tmp"),"TEMP":str(root/"tmp")})
    if sys.platform == "win32":
        for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
            if key in os.environ: env[key]=os.environ[key]
    recipe = {"Identifier": "org.test.cli", "Description": "CLI fixture", "MinimumVersion": "1.0.0",
              "Input": {"NAME": "Fixture"}, "Process": [{"Processor": "FileCreator", "Arguments": {
                  "file_path": "%RECIPE_DIR%/../created.txt", "file_content": "hello"}}]}
    (root / "recipes/Fixture.recipe").write_bytes(plistlib.dumps(recipe))
    install = dict(recipe, Identifier="org.test.cli.install")
    (root / "recipes/Fixture.install.recipe").write_bytes(plistlib.dumps(install))
    child={"Identifier":"org.test.child","ParentRecipe":"org.test.cli","Input":{"NAME":"Child"},"Process":[{"Processor":"FindAndReplace","Arguments":{"input_string":"hello","find":"hello","replace":"child","output_var":"custom"}}]}
    (root/"recipes/Child.recipe").write_bytes(plistlib.dumps(child))
    check=dict(recipe,Identifier="org.test.check",Process=recipe["Process"]+[{"Processor":"EndOfCheckPhase"},{"Processor":"FileCreator","Arguments":{"file_path":"%RECIPE_DIR%/../after-check.txt","file_content":"after"}}])
    (root/"recipes/Check.recipe").write_bytes(plistlib.dumps(check))
    deprecated=dict(recipe,Identifier="org.test.deprecated",Process=[{"Processor":"DeprecationWarning","Arguments":{"warning_message":"Use replacement recipe."}}])
    (root/"recipes/Deprecated.recipe").write_bytes(plistlib.dumps(deprecated))
    (root/"recipes.txt").write_text("Fixture\nFixture.install\n")
    return env


def invoke(command, args, root, executable=None, case="", prepared_env=None, git_url=None):
    env = prepared_env if prepared_env is not None else setup(root)
    args = [arg.replace("$ROOT", str(root)) for arg in args]
    server=None
    api_url=None
    requests=[]
    if "stateful" in case:
        inputs = {"file_path":"%RECIPE_DIR%/../created.txt", "file_content":"before",
                  "input_string":"%file_content%", "find":"before", "replace":"after",
                  "result_output_var_name":"file_content", "predicate":"TRUEPREDICATE"}
        recipe = {"Identifier":"org.test.stateful", "Input":inputs,
                  "Process":[{"Processor":"FindAndReplace"}]}
        (root/"recipes/Stateful.recipe").write_bytes(plistlib.dumps(recipe))
    if case.startswith("run-filesystem-verbose"):
        base="%RECIPE_DIR%/../"
        steps=[("FileCreator",{"file_path":base+"source.txt","file_content":"hello"}),
               ("Copier",{"source_path":base+"source.*","destination_path":base+"copy.txt"}),
               ("FileMover",{"source":base+"copy.txt","target":base+"moved.txt"}),
               ("FileFinder",{"pattern":base+"moved.*"}),
               ("FindAndReplace",{"input_string":"hello world","find":"world","replace":"friend"}),
               ("Symlinker",{"source_path":base+"moved.txt","destination_path":base+"link.txt"}),
               ("PkgRootCreator",{"pkgroot":base+"pkgroot","pkgdirs":{"Applications":"0755","Library/Test":"0700"}}),
               ("VariableSetter",{"custom":"value"}),
               ("PackageRequired",{"PKG":base+"moved.txt"}),
               ("StopProcessingIf",{"predicate":"FALSEPREDICATE"}),
               ("PathDeleter",{"continue_on_error":True,"path_list":[base+"link.txt",base+"pkgroot",base+"absent.txt"]})]
        if sys.platform != "darwin":
            steps=[step for step in steps if step[0] != "StopProcessingIf"]
        recipe={"Identifier":"org.test.fsverbose","Input":{},"Process":[{"Processor":name,"Arguments":values} for name,values in steps]}
        (root/"recipes/Files.recipe").write_bytes(plistlib.dumps(recipe))
    if case.startswith("run-failure"):
        recipe={"Identifier":"org.test.failure","Input":{},"Process":[{"Processor":"PlistReader","Arguments":{"info_path":"%RECIPE_DIR%/absent.plist"}}]}
        (root/"recipes/Failure.recipe").write_bytes(plistlib.dumps(recipe))
    if case.startswith("auth-"):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                requests.append(self.headers.get("Authorization"))
                release={"name":"1.0","tag_name":"v1.0","prerelease":False,"body":None,"assets":[{"name":"App.zip","browser_download_url":"https://example.invalid/App.zip","url":"https://api.example.invalid/asset","created_at":"2026-01-01T00:00:00Z"}]}
                body=json.dumps(release if self.path.endswith("/latest") else [release]).encode()
                self.send_response(200);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        api_url=f"http://127.0.0.1:{server.server_port}"
        threading.Thread(target=server.serve_forever,daemon=True).start()
        (root/"token").write_text("file-token")
        prefs=plistlib.loads((root/"prefs.plist").read_bytes())
        if case != "auth-file-fallback": prefs["GITHUB_TOKEN"]="preference-token"
        (root/"prefs.plist").write_bytes(plistlib.dumps(prefs))
        recipe={"Identifier":"org.test.auth","Input":{"github_repo":"owner/project","GITHUB_URL":api_url,"GITHUB_TOKEN":"recipe-token","GITHUB_TOKEN_PATH":str(root/"token"),"curl_opts":["--noproxy","*"]},"Process":[{"Processor":"GitHubReleasesInfoProvider"},{"Processor":"FileCreator","Arguments":{"file_path":str(root/"created.txt"),"file_content":"%GITHUB_TOKEN%"}}]}
        (root/"recipes/Auth.recipe").write_bytes(plistlib.dumps(recipe))
    try:
        result = subprocess.run(command + args, executable=executable, cwd=root, env=env, input=b"", capture_output=True, timeout=30)
    finally:
        if server:
            server.shutdown();server.server_close()
    def normalized_text(text):
        text=text.replace(str(root).replace("\\", "\\\\"),"$ROOT")
        text=text.replace(str(root),"$ROOT")
        if git_url: text=text.replace(git_url,"$GIT")
        text=re.sub(r"-receipt-\d{8}-\d{6}\.plist", "-receipt-$TIMESTAMP.plist", text)
        return text.replace(api_url,"$API") if api_url else text
    def norm(data):
        text = normalized_text(data.decode(errors="replace"))
        text = text.replace("WARNING: Did not load any default preferences.\r\n", "").replace("WARNING: Did not load any default preferences.\n", "")
        # Python object addresses in Chocolatey's sentinel default are nondeterministic.
        return re.sub(r"(<autopkglib.ChocolateyPackager.VariableSentinel object at )0x[0-9a-fA-F]+>", r"\g<1>0x0>", text)
    outcome = {"status": result.returncode, "stdout": norm(result.stdout), "stderr": norm(result.stderr)}
    if sys.platform != "darwin" and executable is None:
        # Keep the actual reference dependency diagnostics as evidence, separate
        # from command output. Native Rust must not fabricate Python import warnings.
        messages={
            "WARNING: Failed 'from Foundation import CFPreferencesCopyAppValue' in autopkglib.MunkiSetDefaultCatalog",
            "WARNING: Failed 'from Foundation import NSDictionary' in autopkglib.MunkiInstallsItemsCreator",
            "WARNING: Failed 'from Foundation import NSPredicate' in autopkglib.StopProcessingIf",
            "WARNING: Library 'xattr' unavailable. Defining no-op implementation.",
        }
        diagnostics=[]
        for stream in ("stdout","stderr"):
            retained=[]
            preamble=True
            for line in outcome[stream].splitlines(keepends=True):
                if preamble and line.rstrip("\r\n") in messages: diagnostics.append({"stream":stream,"text":line})
                else:
                    preamble=False
                    retained.append(line)
            outcome[stream]="".join(retained)
        if diagnostics: outcome["reference_import_diagnostics"]=diagnostics
    if api_url: outcome["authorization_headers"]=requests
    for name in ("report.plist", "created.txt", "cache/autopkg_results.plist", "after-check.txt"):
        path = root / name
        if path.is_file():
            if name.endswith(".plist"):
                def normalize(value):
                    if isinstance(value, str): return normalized_text(value)
                    if isinstance(value, list): return [normalize(v) for v in value]
                    if isinstance(value, dict): return {k: normalize(v) for k,v in value.items()}
                    return value
                outcome[name] = normalize(plistlib.loads(path.read_bytes()))
            else:
                outcome[name] = path.read_text()
    generated={}
    paths=[root/"prefs.plist",root/"recipe_map.json",root/"New.recipe",root/"New.recipe.yaml"]
    paths.extend((root/"overrides").glob("*.recipe*"))
    paths.extend((root/"cache").glob("*/receipts/*.plist"))
    for path in paths:
        if not path.is_file(): continue
        relative=str(path.relative_to(root))
        relative=re.sub(r"-receipt-\d{8}-\d{6}\.plist$","-receipt-$TIMESTAMP.plist",relative)
        if path.suffix == ".json": value=json.loads(path.read_text())
        elif path.suffix == ".yaml":
            # YAML syntax differs between serializers; recipe values are the interface.
            value=json.loads(subprocess.check_output([REFERENCE_PYTHON,"-c","import yaml,json,sys; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))",str(path)],cwd=root,env=env))
        else: value=plistlib.loads(path.read_bytes())
        def clean(v):
            if isinstance(v,str): return normalized_text(v)
            if isinstance(v,list): return [clean(item) for item in v]
            if isinstance(v,dict): return {clean(k):clean(item) for k,item in v.items()}
            return v
        generated[relative]=clean(value)
    outcome["generated_files"]=generated
    return outcome


def unarchiver_python_default(stdout):
    """Rewrites the USE_PYTHON_NATIVE_EXTRACTOR default in Russet's Unarchiver
    processor-info output to Python AutoPkg's Linux value."""
    lines = stdout.split("\n")
    for index, line in enumerate(lines):
        if line.strip() == "USE_PYTHON_NATIVE_EXTRACTOR:":
            indent = len(line) - len(line.lstrip())
            for later in range(index + 1, len(lines)):
                current = lines[later]
                if current.strip() and len(current) - len(current.lstrip()) <= indent:
                    break
                if current.strip() == "default: False":
                    lines[later] = current.replace("default: False", "default: True")
                    return "\n".join(lines)
    return stdout


def equivalent(name, expected, actual):
    if name == "git-transport-sequence":
        return expected.keys()==actual.keys() and all(equivalent(step,expected[step],actual[step]) for step in expected)
    expected={k:v for k,v in expected.items() if k != "reference_import_diagnostics"}
    actual={k:v for k,v in actual.items() if k != "reference_import_diagnostics"}
    # This release intentionally promotes 12 recipe processors and one alias.
    # Accept only the exact new inventory; preserve every other output field.
    frozen = json.loads((COMPATIBILITY / "reference.json").read_text())["processors"]
    community = json.loads((COMPATIBILITY / "community-processors.json").read_text())
    # Require matching complete LF or CRLF inventories. In particular, do not
    # normalize line endings between implementations or elsewhere in the output.
    for newline in ("\n", "\r\n"):
        original_inventory = newline.join(sorted(frozen)) + newline
        expanded_inventory = newline.join(sorted(set(frozen) | set(community["processors"]) | set(community["aliases"]))) + newline
        if expected.get("stdout") == original_inventory and actual.get("stdout") == expanded_inventory:
            actual = dict(actual, stdout=original_inventory)
            break
    if name == "processor-info-Unarchiver" and sys.platform.startswith("linux"):
        # Russet extracts zip and cpio archives with its built-in ditto
        # replacement on Linux, so it deliberately defaults
        # USE_PYTHON_NATIVE_EXTRACTOR to False there, where Python AutoPkg
        # defaults it to True. Accept exactly that default and nothing else.
        actual = dict(actual, stdout=unarchiver_python_default(actual.get("stdout", "")))
    if not name.startswith("run-failure"):
        return expected == actual
    # Stack frames are runtime-specific, not a compatibility equality assertion.
    # Keep both full traces in results; compare every other report/environment/file field.
    left=json.loads(json.dumps(expected)); right=json.loads(json.dumps(actual))
    python_failures=left.get("report.plist",{}).get("failures",[])
    rust_failures=right.get("report.plist",{}).get("failures",[])
    if not python_failures or len(python_failures)!=len(rust_failures): return False
    for py,rs in zip(python_failures,rust_failures):
        py_trace=py.get("traceback",""); rs_trace=rs.get("traceback","")
        message=py.get("message","")
        if not py_trace.startswith("Traceback (most recent call last):") or message not in py_trace: return False
        if "Native Rust backtrace:" not in rs_trace or not rs_trace.startswith(message+"\n"): return False
        py["traceback"]=rs["traceback"]="$RUNTIME_STACK_FRAMES"
    return left==right


def git_sequence(command, root, executable=None):
    env = setup(root)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
               GIT_AUTHOR_DATE="2020-01-01T00:00:00Z", GIT_COMMITTER_DATE="2020-01-01T00:00:00Z")
    source=root/"source"; source.mkdir()
    def git(*args, cwd=source):
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True, timeout=20).stdout
    git("init", "--initial-branch=main")
    (source/"Remote.recipe").write_bytes(plistlib.dumps({"Identifier":"org.test.remote", "Input":{}, "Process":[]}))
    git("add", "."); git("commit", "-m", "initial")
    export=root/"export"; export.mkdir()
    git("clone", "--bare", str(source), str(export/"recipes.git"))
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1",0)); port=reserve.getsockname()[1]
    url=f"git://127.0.0.1:{port}/recipes.git"
    daemon=subprocess.Popen(["git","daemon","--reuseaddr","--export-all",f"--base-path={export}","--listen=127.0.0.1",f"--port={port}"],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    outcomes={}
    try:
        deadline=time.monotonic()+5
        while True:
            try:
                with socket.create_connection(("127.0.0.1",port),timeout=.2): break
            except OSError:
                if daemon.poll() is not None or time.monotonic()>deadline: raise RuntimeError("Git daemon did not start")
                time.sleep(.02)
        steps=[("add",["repo-add",url]),("list",["repo-list"]),("unchanged",["repo-update","all"]),
               ("updated",["repo-update","all"]),("recipes",["list-recipes"]),("delete",["repo-delete",url]),("empty",["repo-list"])]
        for name,args in steps:
            if name=="updated":
                (source/"Remote.recipe").write_bytes(plistlib.dumps({"Identifier":"org.test.remote.updated", "Input":{}, "Process":[]}))
                git("add", "."); git("commit", "-m", "update"); git("push",str(export/"recipes.git"),"main")
            result=invoke(command,args[:1]+["--prefs",str(root/"prefs.plist")]+args[1:],root,executable,prepared_env=env,git_url=url)
            checkout=root/"repos/1.0.0.127.recipes"
            if checkout.exists():
                try:
                    result["checkout"]={"head":git("rev-parse","HEAD",cwd=checkout).strip(),"branch":git("branch","--show-current",cwd=checkout).strip(),"recipe":plistlib.loads((checkout/"Remote.recipe").read_bytes())}
                except subprocess.CalledProcessError as error:
                    result["checkout_error"]={"status":error.returncode,"stdout":error.stdout.replace(str(root),"$ROOT"),"stderr":error.stderr.replace(str(root),"$ROOT")}
                except OSError as error:
                    result["checkout_error"]={"errno":error.errno,"message":str(error).replace(str(root),"$ROOT")}
            outcomes[name]=result
    finally:
        daemon.terminate()
        try: daemon.wait(timeout=5)
        except subprocess.TimeoutExpired: daemon.kill(); daemon.wait(timeout=5)
        daemon.stderr.close()
    return outcomes


def main():
    global REFERENCE_PYTHON
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--rust", type=Path, default=RUST_DEBUG_CLI)
    parser.add_argument("--capture-parsers", action="store_true")
    parser.add_argument("--check-parsers", action="store_true", help="Verify runtime parser and processor display captures without rewriting them")
    parser.add_argument("--results", type=Path)
    parser.add_argument("--case", help="Run case names matching this regular expression")
    args = parser.parse_args()
    python = str(Path(args.python).absolute()) if "/" in args.python else args.python
    REFERENCE_PYTHON=python
    version=subprocess.check_output([python,"-c","import platform; print(platform.python_version())"],text=True).strip()
    if version != REFERENCE_PYTHON_VERSION:
        parser.error(f"The pinned AutoPkg release bundles Python {REFERENCE_PYTHON_VERSION}; got {version}. Use --python with that reference interpreter. Runtime docstring formatting differs in Python 3.13 and later.")
    subprocess.run([sys.executable,str(ROOT/"Scripts/capture_rust_contract.py"),"--check"],check=True)
    verbs = json.loads((COMPATIBILITY / "reference.json").read_text())["cli"]["subcommands"]
    failures = []
    outcomes = {}
    with tempfile.TemporaryDirectory(prefix="autopkg-cli-differential-") as temporary:
        temp = Path(temporary).resolve()
        reference = temp / "reference"
        reference.mkdir()
        archive = reference_archive()
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(reference)
        library = reference / "Code/autopkglib/__init__.py"
        source = library.read_text()
        assert source.count('BUNDLE_ID = "com.github.autopkg"') == 1
        source=source.replace('BUNDLE_ID = "com.github.autopkg"', 'BUNDLE_ID = "org.autopkg.cli-test.' + uuid.uuid4().hex + '"')
        # Windows appdirs can consult native shell folders despite LOCALAPPDATA.
        # Redirect its location only; the real Preferences parser remains intact.
        original='config_dir = appdirs.user_config_dir(APP_NAME, appauthor=False)'
        assert source.count(original)==1
        source=source.replace(original,'config_dir = os.path.join(os.environ["XDG_CONFIG_HOME"], APP_NAME)')
        library.write_text(source)
        command = [python, str(Path(__file__).resolve()), "--worker", str(reference / "Code")]
        if args.capture_parsers or args.check_parsers:
            specs = {}
            for verb in verbs:
                if verb in ("help", "version"):
                    continue
                result = invoke(command + ["--capture"], [verb, "--help"], temp / verb)
                if result["status"]:
                    raise RuntimeError(result)
                specs[verb] = json.loads(result["stdout"])
            result=invoke(command,["--capture-processor-order"],temp/"processor-order")
            if result["status"]: raise RuntimeError(result)
            captured={SPEC:{"reference_commit":REFERENCE,"reference_python":REFERENCE_PYTHON_VERSION,"parsers":specs}, COMPATIBILITY/"cli-processor-order.json":json.loads(result["stdout"])}
            for path, data in captured.items():
                if args.check_parsers:
                    if json.loads(path.read_text()) != data: raise RuntimeError(f"Reference metadata changed: {path}")
                else:
                    path.write_text(json.dumps(data,indent=2)+"\n")
            print(f"{'Verified' if args.check_parsers else 'Captured'} {len(specs)} real CLI parsers and processor display order")
            return 0
        executable = args.rust.resolve()
        cases = [("version", ["version"]), ("version-ignores-options", ["version", "--invalid-option"])]
        cases += [("top-" + label, argv) for label, argv in [("empty", []), ("help", ["help"]), ("unknown", ["unknown-verb"]), ("flag", ["--help"])]]
        for verb in verbs:
            if verb in ("help", "version"):
                continue
            for label, tail in [("help", ["--help"]), ("unknown-option", ["--invalid-option"]), ("missing-prefs", ["--prefs"])]:
                cases.append((verb + "-" + label, [verb] + tail))
        for verb in verbs:
            if verb not in ("help", "version"):
                cases.append((verb + "-empty", [verb, "--prefs", "$ROOT/prefs.plist"]))
        for name, argv in [(case["name"], case["arguments"]) for case in json.loads((COMPATIBILITY / "cli-fixtures.json").read_text())]:
            cases.append((name, argv[:1] + ["--prefs", "$ROOT/prefs.plist"] + argv[1:]))
        for processor in json.loads((COMPATIBILITY / "reference.json").read_text())["processors"]:
            cases.append(("processor-info-" + processor, ["processor-info", "--prefs", "$ROOT/prefs.plist", processor]))
        for name, pre, post in [("run-stateful-order", "FileCreator", "FileCreator"),
                                ("run-failure-stateful-pre", "PackageRequired", "FileCreator"),
                                ("run-failure-stateful-post", "FileCreator", "PackageRequired")]:
            cases.append((name, ["run", "--prefs", "$ROOT/prefs.plist", "--report-plist", "$ROOT/report.plist", "--pre", pre, "--post", post, "Stateful"]))
        if sys.platform == "darwin":
            cases.append(("run-stateful-stop", ["run", "--prefs", "$ROOT/prefs.plist", "--pre", "StopProcessingIf", "--post", "FileCreator", "Stateful"]))
        if args.case:
            cases=[case for case in cases if re.search(args.case,case[0])]
        if sys.platform != "darwin" and any(name.startswith("run-filesystem-verbose") for name, _ in cases):
            print("REFERENCE CAPABILITY: filesystem recipes omit StopProcessingIf because pinned Python requires macOS Foundation; portable predicates are gated separately against the frozen native oracle.")
        for index, (name, argv) in enumerate(cases):
            expected = invoke(command, argv, temp / f"python-{index}", case=name)
            actual = invoke(["autopkg"], argv, temp / f"native-{index}", executable=str(executable), case=name)
            outcomes[name] = {"python": expected, "rust": actual}
            if not equivalent(name, expected, actual):
                failures.append(name)
                print("FAIL", name, json.dumps(outcomes[name]))
            else:
                boundary = "runtime traceback boundary" if name.startswith("run-failure") else ""
                if expected.get("reference_import_diagnostics"): boundary += "; Python import diagnostic boundary"
                print("PASS", name, f"({boundary.strip('; ')}; raw diagnostics retained)" if boundary else "")
        name="git-transport-sequence"
        if not args.case or re.search(args.case,name):
            expected=git_sequence(command,temp/"python-git")
            actual=git_sequence(["autopkg"],temp/"native-git",str(executable))
            outcomes[name]={"python":expected,"rust":actual}
            if not equivalent(name, expected, actual):
                failures.append(name); print("FAIL",name,json.dumps(outcomes[name]))
            else: print("PASS",name)
    if args.results:
        args.results.write_text(json.dumps(outcomes, indent=2) + "\n")
    print(f"{len(outcomes)-len(failures)}/{len(outcomes)} CLI cases passed")
    return bool(failures)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--worker":
        code, tail = sys.argv[2], sys.argv[3:]
        capture = tail[:1] == ["--capture"]
        worker(code, tail[1:] if capture else tail, capture)
    else:
        sys.exit(main())
