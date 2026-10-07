#!/usr/bin/env python3
"""Offline tests for native Windows differential evidence normalization."""
import hashlib
import contextlib
import io
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile

import differential_rust_windows_native as native


class NativeEvidenceTests(unittest.TestCase):
    def test_only_complete_known_chocolatey_advertisements_are_removed(self):
        self.assertEqual(len(native.CHOCO_PROMOTIONS), 7)
        self.assertEqual(len(set(native.CHOCO_PROMOTIONS)), 7)
        for message in native.CHOCO_PROMOTIONS:
            for processor in [False, True]:
                with self.subTest(message=message.splitlines()[0], processor=processor):
                    if processor:
                        banner = "ChocolateyPackager: \n" + "".join(
                            "ChocolateyPackager: " + line for line in message.splitlines(keepends=True))
                    else:
                        banner = "$TIMESTAMP $PID [WARN ] - \n" + message
                    before = "important preceding output\n"
                    after = "WARNING: preserve this unrelated warning\nimportant following output\n"
                    self.assertEqual(native.chocolatey_promotion(before + banner + after, processor), before + after)
                    for changed in [banner.replace("/compare", "/different"), banner.replace(message.splitlines()[0], "Unknown message"),
                                    banner.rstrip("\n"), "prefix " + banner, banner.splitlines(keepends=True)[0] + "".join(banner.splitlines(keepends=True)[2:])]:
                        self.assertEqual(native.chocolatey_promotion(before + changed + after, processor), before + changed + after)
            foreign = "SignToolVerifier: \n" + "".join(
                "SignToolVerifier: " + line for line in message.splitlines(keepends=True))
            self.assertEqual(native.chocolatey_promotion(foreign, processor=True), foreign)

    def test_reference_probe_keeps_import_warnings_out_of_json_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "autopkglib"
            package.mkdir()
            (package / "__init__.py").write_text("print('Failed from Foundation import CFPreferencesCopyAppValue')\n")
            module = package / "SignToolVerifier.py"
            module.write_text("def signtool_default_path():\n    print('discovery warning')\n    return r'C:\\Program Files (x86)\\Windows Kits\\10\\App Certification Kit\\signtool.exe'\n")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                path = native.reference_signtool(sys.executable, root)
            self.assertEqual(path, r"C:\Program Files (x86)\Windows Kits\10\App Certification Kit\signtool.exe")
            self.assertIn("Failed from Foundation", stderr.getvalue())
            self.assertIn("discovery warning", stderr.getvalue())
            module.write_text("def signtool_default_path():\n    return None\n")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(native.reference_signtool(sys.executable, root))

    def package(self, path, identity, timestamp, payload=b"exact payload\r\n"):
        core = f"package/services/metadata/core-properties/{identity}.psmdcp"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(core, f'<core xmlns:d="urn:date"><d:created>{timestamp}</d:created><title>fixture</title></core>')
            archive.writestr("_rels/.rels", f'<Relationships><Relationship Id="{identity}" Target="/{core}" /></Relationships>')
            archive.writestr("tools/chocolateyInstall.ps1", payload)
        return native.package_contents(path)

    def test_only_identified_package_nondeterminism_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.package(root / "one.zip", "a" * 32, "2000-01-01")
            second = self.package(root / "two.zip", "b" * 32, "2001-01-01")
            self.assertEqual(first, second)
            changed = self.package(root / "three.zip", "b" * 32, "2001-01-01", b"exact payload\n")
            self.assertNotEqual(first, changed)
            self.assertEqual(first["tools/chocolateyInstall.ps1"]["sha256"], hashlib.sha256(b"exact payload\r\n").hexdigest())

    def test_content_type_order_keeps_attributes_duplicates_and_payloads(self):
        head = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        a = '<Default Extension="xml" ContentType="text/xml"/>'
        b = '<Override PartName="/fixture" ContentType="application/test"/>'
        parse = lambda body: native.content_types((head + body + '</Types>').encode())
        self.assertEqual(parse(a+b), parse(b+a))
        self.assertNotEqual(parse(a+b), parse(a+a+b))
        self.assertNotEqual(parse(a+b), parse(a+b.replace('application/test', 'application/changed')))
        self.assertNotEqual(parse(a+b), parse(a+b.replace('/fixture', '/other')))
        self.assertNotEqual(parse(a+b), parse(a.replace('/>', ' extra="kept"/>')+b))
        self.assertNotEqual(parse(a+b), parse(a.replace('/>', '><child/></Default>')+b))
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            parse('<Unexpected/>')

    def test_package_content_types_semantics_do_not_change_nuspec_hashing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = []
            for index, body in enumerate((
                '<Default Extension="xml" ContentType="text/xml"/><Override PartName="/x" ContentType="a/b"/>',
                '<Override PartName="/x" ContentType="a/b"/><Default Extension="xml" ContentType="text/xml"/>')):
                path = root / f'{index}.zip'
                self.package(path, 'a'*32, '2000-01-01')
                with zipfile.ZipFile(path, 'a') as archive:
                    archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'+body+'</Types>')
                    archive.writestr('fixture.nuspec', b'<package/>\r\n')
                results.append(native.package_contents(path))
            self.assertEqual(*results)
            self.assertEqual(results[0]['fixture.nuspec']['sha256'], hashlib.sha256(b'<package/>\r\n').hexdigest())

    def test_log_prefix_ignores_pid_but_keeps_message_numbers_and_elapsed(self):
        a = '2026-10-07 01:02:03,123 1504 [INFO ] - packed 1504 files in 1.2 seconds\n'
        b = a.replace(',123 1504 [', ',456 9888 [')
        self.assertEqual(native.chocolatey_log(a), native.chocolatey_log(b))
        self.assertNotEqual(native.chocolatey_log(a), native.chocolatey_log(b.replace('1.2 seconds','1.3 seconds')))
        self.assertIn('packed 1504 files', native.chocolatey_log(a))
        self.assertEqual(native.chocolatey_log('message 2026-10-07 01:02:03,123 1504'),
                         'message 2026-10-07 01:02:03,123 1504')

    def test_default_cases_really_omit_manifest_defaults(self):
        cases = {case["name"]: case for case in native.cases("choco.exe", "signtool.exe", "signed.exe", "unsigned.exe")}
        default = cases["chocolatey-defaults"]["environment"]
        for name in ["chocoexe_path", "installer_path", "installer_checksum_type", "KEEP_BUILD_DIRECTORY", "output_directory"]:
            self.assertNotIn(name, default)
        for label in ["signed", "unsigned"]:
            env = cases[f"signtool-{label}-defaults"]["environment"]
            self.assertNotIn("signtool_path", env)
            self.assertNotIn("additional_arguments", env)
            self.assertNotIn("DISABLE_CODE_SIGNATURE_VERIFICATION", env)

    def test_build_paths_normalize_only_selected_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "isolated"
            build = root / "builds" / "id.abc"
            normalized_build = native.harness.normalize(str(build), root)
            suffix = str(build / "file")[len(str(build)):]
            self.assertEqual(native.normalize(str(build / "file"), root, normalized_build), "$BUILD" + suffix)
            unrelated = str(parent / "unrelated" / "builds" / "id.abc" / "file")
            self.assertEqual(native.normalize(unrelated, root, normalized_build), unrelated)

    def test_missing_reference_default_is_an_active_failure_case(self):
        cases = {case["name"]: case for case in native.cases("choco", "sdk", "signed", "unsigned")}
        self.assertEqual(len(cases), 11)
        for label in ["signed", "unsigned"]:
            default = cases[f"signtool-{label}-defaults"]
            self.assertEqual(default["expected_status"], 10)
            self.assertIn("No signtool_path configured", default["expected_error"])
            explicit = cases[f"signtool-{label}-explicit-default-arguments"]
            self.assertEqual(explicit["environment"]["signtool_path"], "sdk")
            self.assertNotIn("additional_arguments", explicit["environment"])
        self.assertEqual(cases["signtool-signed-explicit-default-arguments"]["expected_status"], 0)
        found = {case["name"]: case for case in native.cases("choco", "sdk", "signed", "unsigned", "default-sdk")}
        self.assertEqual(found["signtool-signed-defaults"]["expected_status"], 0)
        self.assertNotIn("expected_error", found["signtool-signed-defaults"])

    def test_real_sdk_discovery_keeps_native_gate_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [root / "Windows Kits/10/bin/10.0.1/x64/signtool.exe",
                     root / "Windows Kits/10/bin/10.0.2/x64/signtool.exe",
                     root / "Windows Kits/10/bin/10.0.9/x86/signtool.exe",
                     root / "Windows Kits/10/App Certification Kit/signtool.exe"]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            self.assertEqual(native.discover_signtool([None, root]), str(paths[1]))
            paths[0].unlink()
            paths[1].unlink()
            self.assertEqual(native.discover_signtool([root]), str(paths[3]))
            paths[3].unlink()
            with self.assertRaisesRegex(RuntimeError, "No real x64"):
                native.discover_signtool([root])


if __name__ == "__main__":
    unittest.main()
