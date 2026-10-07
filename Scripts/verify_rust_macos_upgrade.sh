#!/bin/sh
# Development-only: upgrade and restore a running pinned Python installation.
set -eu
[ "${AUTOPKG_DISPOSABLE_VM:-}" = 1 ] || exit 1
case "$(/usr/sbin/sysctl -n hw.model)" in VirtualMac*) ;; *) exit 1 ;; esac
[ "$(/usr/bin/id -u)" = 0 ] || exit 1
[ "$#" = 2 ] || { echo 'Usage: verify_rust_macos_upgrade.sh NATIVE_ARCHIVE_DIRECTORY PYTHON_REFERENCE_TAR_GZ_OR_PUBLISHED_PKG' >&2; exit 1; }
[ ! -e /Library/AutoPkg ] && [ ! -e /usr/local/autopkg ] || {
    echo 'Reference upgrade requires an empty disposable guest' >&2; exit 1;
}
for reserved in /usr/local/bin/autopkg \
    /Library/LaunchDaemons/com.github.autopkg.autopkgserver.plist \
    /Library/LaunchDaemons/com.github.autopkg.autopkginstalld.plist; do
    [ ! -e "$reserved" ] && [ ! -L "$reserved" ] || {
        echo "Reference upgrade refuses existing path: $reserved" >&2; exit 1;
    }
done
native=$1
reference=$2
[ -f "$native/install.sh" ] && [ -x "$native/bin/autopkg-rs" ] && [ -f "$reference" ] || {
    echo 'Native archive or Python reference archive is missing' >&2; exit 1;
}
work=$(/usr/bin/mktemp -d /private/tmp/autopkg-reference-upgrade.XXXXXXXX)
/bin/chmod 0755 "$work"
echo "Reference upgrade evidence: $work"
fixture=/usr/local/share/autopkg-reference-upgrade-${work##*.}
receipt=org.autopkg.rust.upgradefixture.${work##*.}
[ ! -e "$fixture" ] || exit 1
packaging=/Library/LaunchDaemons/com.github.autopkg.autopkgserver.plist
installation=/Library/LaunchDaemons/com.github.autopkg.autopkginstalld.plist
cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    # Only paths reserved by the empty-guest precondition and this unique run.
    /bin/launchctl bootout system "$packaging" >/dev/null 2>&1 || true
    /bin/launchctl bootout system "$installation" >/dev/null 2>&1 || true
    /usr/sbin/pkgutil --forget "$receipt" >/dev/null 2>&1 || true
    if [ -f "$work/published-receipts.txt" ]; then
        while IFS= read -r installed_receipt; do
            /usr/sbin/pkgutil --forget "$installed_receipt" >/dev/null 2>&1 || true
        done < "$work/published-receipts.txt"
    fi
    /bin/rm -f "$fixture" /usr/local/bin/autopkg "$packaging" "$installation"
    /bin/rm -rf /Library/AutoPkg /usr/local/autopkg
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
case "$reference" in
    *.pkg)
        /usr/sbin/pkgutil --pkgs | /usr/bin/sort > "$work/receipts-before.txt"
        /usr/sbin/installer -pkg "$reference" -target / > "$work/published-package-install.txt"
        /usr/sbin/pkgutil --pkgs | /usr/bin/sort > "$work/receipts-after.txt"
        /usr/bin/comm -13 "$work/receipts-before.txt" "$work/receipts-after.txt" > "$work/published-receipts.txt"
        /usr/bin/shasum -a 256 "$reference" > "$work/published-package-sha256.txt"
        ;;
    *)
/usr/bin/tar -xzf "$reference" -C /Library
/usr/sbin/chown -R root:wheel /Library/AutoPkg
/bin/mkdir -p /usr/local/autopkg /usr/local/bin
/bin/ln -s /Library/AutoPkg/Python3/bin/python3.11 /usr/local/autopkg/python
/bin/ln -s /Library/AutoPkg/autopkg /usr/local/bin/autopkg
/bin/cp /Library/AutoPkg/autopkgserver/autopkgserver.plist "$packaging"
/bin/cp /Library/AutoPkg/autopkgserver/autopkginstalld.plist "$installation"
/bin/chmod 0644 "$packaging" "$installation"
        ;;
esac
/usr/local/bin/autopkg version > "$work/python-version-before.txt"
for daemon in "$packaging" "$installation"; do
    label=$(/usr/libexec/PlistBuddy -c 'Print :Label' "$daemon")
    if ! /bin/launchctl print "system/$label" >/dev/null 2>&1; then
        /bin/launchctl bootstrap system "$daemon"
    fi
done
/bin/launchctl print system/com.github.autopkgserver > "$work/python-launchd-before.txt"
/usr/local/autopkg/python - "$work" "$fixture" "$receipt" <<'PY'
import pathlib, plistlib, sys
root, fixture = map(pathlib.Path, sys.argv[1:3])
receipt = sys.argv[3]
(root / "home").mkdir()
(root / "image-source").mkdir()
(root / "image-source/copied.txt").write_text("reference image copy\n")
for implementation in ("python", "rust"):
    base = root / implementation
    payload = base / "pkg/payload" / fixture.relative_to("/")
    payload.parent.mkdir(parents=True)
    payload.write_text("reference upgrade payload\n")
    app = base / "app/source/UpgradeApp.app/Contents"
    app.mkdir(parents=True)
    (app / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": receipt + ".app", "CFBundleName": "UpgradeApp",
        "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "2.0",
        "CFBundleVersion": "42",
    }))
    values = {
        "PkgCreator": {"RECIPE_CACHE_DIR": str(base / "pkg"), "pkg_request": {
            "pkgroot": str(base / "pkg/payload"), "pkgdir": str(base / "pkg"),
            "pkgname": "UpgradeFixture", "pkgtype": "flat", "id": receipt, "version": "1.0"}},
        # Omit app_path, version, bundleid, pkg_path and all declared defaults.
        "AppPkgCreator": {"RECIPE_CACHE_DIR": str(base / "app"), "pathname": str(base / "app/source")},
        # Omit both skip flags to exercise default actual installation.
        "Installer": {"RECIPE_CACHE_DIR": str(base / "pkg"), "pkg_path": str(base / "pkg/UpgradeFixture.pkg")},
        "InstallFromDMG": {"dmg_path": str(root / "fixture.dmg"), "items_to_copy": [{
            "source_item": "copied.txt", "destination_path": str(base / "copied")}]},
    }
    for name, value in values.items():
        (base / (name + ".input.plist")).write_bytes(plistlib.dumps(value))
    for name in ("Installer", "InstallFromDMG"):
        value = dict(values[name], download_changed=False)
        (base / (name + ".skip.input.plist")).write_bytes(plistlib.dumps(value))
PY
/usr/bin/hdiutil create -srcfolder "$work/image-source" -format UDZO "$work/fixture.dmg"
/usr/sbin/chown -R nobody:nobody "$work/python" "$work/rust" "$work/home"
printf '<?xml version="1.0"?><plist version="1.0"><dict/></plist>\n' > "$work/preferences.plist"
run_processor() {
    implementation=$1 processor=$2 input=$3 output=$4
    if [ "$implementation" = python ]; then
        set -- /usr/bin/env HOME="$work/home" PYTHONPATH=/Library/AutoPkg \
            /usr/local/autopkg/python "/Library/AutoPkg/autopkglib/$processor.py"
    else
        set -- /usr/bin/env PATH=/nonexistent HOME="$work/home" \
            AUTOPKG_RS_PREFERENCES_FILE="$work/preferences.plist" \
            /usr/local/bin/autopkg processor-run "$processor"
    fi
    if [ "$processor" = InstallFromDMG ]; then
        "$@" < "$input" > "$output" 2> "$output.stderr"
    else
        /usr/bin/sudo -u nobody "$@" < "$input" > "$output" 2> "$output.stderr"
    fi
}
run_implementation() {
    implementation=$1
    base=$work/$implementation
    for processor in PkgCreator AppPkgCreator Installer InstallFromDMG; do
        run_processor "$implementation" "$processor" "$base/$processor.input.plist" "$base/$processor.output.plist"
    done
    /usr/bin/cmp "$fixture" "$base/pkg/payload$fixture"
    /usr/bin/cmp "$work/image-source/copied.txt" "$base/copied/copied.txt"
    /usr/bin/stat -f '%u:%g:%OLp' "$fixture" "$base/copied/copied.txt" > "$base/installed-permissions.txt"
    /usr/sbin/pkgutil --pkg-info-plist "$receipt" > "$base/installed-receipt.plist"
    /usr/sbin/pkgutil --expand-full "$base/app/UpgradeApp-2.0.pkg" "$base/app-expanded"
    /usr/bin/cmp "$base/app/source/UpgradeApp.app/Contents/Info.plist" \
        "$base/app-expanded/Payload/Applications/UpgradeApp.app/Contents/Info.plist"
    run_processor "$implementation" AppPkgCreator "$base/AppPkgCreator.input.plist" "$base/AppPkgCreator.repeat.output.plist"
    /bin/rm "$fixture"
    /usr/sbin/pkgutil --forget "$receipt"
    # Skip cases must not recreate the removed destinations or receipt.
    /bin/rm "$base/copied/copied.txt"
    for processor in Installer InstallFromDMG; do
        run_processor "$implementation" "$processor" "$base/$processor.skip.input.plist" "$base/$processor.skip.output.plist"
    done
    [ ! -e "$fixture" ] && [ ! -e "$base/copied/copied.txt" ]
    if /usr/sbin/pkgutil --pkg-info "$receipt" >/dev/null 2>&1; then exit 1; fi
}
run_implementation python
/usr/bin/shasum -a 256 /Library/AutoPkg/autopkg /Library/AutoPkg/autopkgserver/autopkgserver \
    /Library/AutoPkg/autopkgserver/autopkginstalld "$packaging" "$installation" > "$work/legacy-hashes-before.txt"
/bin/sh "$native/install.sh" install
/bin/launchctl print system/com.github.autopkgserver > "$work/rust-launchd.txt"
/usr/bin/env PATH=/nonexistent /usr/local/bin/autopkg version > "$work/rust-version.txt"
run_implementation rust
/bin/sh /Library/AutoPkg/install.sh rollback
/usr/local/bin/autopkg version > "$work/python-version-after.txt"
/usr/bin/cmp "$work/python-version-before.txt" "$work/python-version-after.txt"
/usr/bin/shasum -a 256 --check "$work/legacy-hashes-before.txt"
/bin/launchctl print system/com.github.autopkgserver > "$work/python-launchd-after.txt"
/bin/rm "$work/python/pkg/UpgradeFixture.pkg"
run_processor python PkgCreator "$work/python/PkgCreator.input.plist" "$work/python/restored-output.plist"
[ -f "$work/python/pkg/UpgradeFixture.pkg" ]
run_processor python Installer "$work/python/Installer.input.plist" "$work/python/restored-install.output.plist"
/usr/bin/cmp "$fixture" "$work/python/pkg/payload$fixture"
/usr/sbin/pkgutil --pkg-info "$receipt" > "$work/restored-receipt.txt"
/usr/local/autopkg/python - "$work" <<'PY'
import pathlib, plistlib, sys
root = pathlib.Path(sys.argv[1])
def normalize(value, fixture):
    if isinstance(value, dict):
        return {key: normalize(item, fixture) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item, fixture) for item in value]
    if isinstance(value, str):
        return value.replace(str(fixture), "$FIXTURE")
    return value
for name in ("PkgCreator", "AppPkgCreator", "AppPkgCreator.repeat", "Installer",
             "Installer.skip", "InstallFromDMG", "InstallFromDMG.skip"):
    expected = normalize(plistlib.loads((root / "python" / (name + ".output.plist")).read_bytes()), root / "python")
    actual = normalize(plistlib.loads((root / "rust" / (name + ".output.plist")).read_bytes()), root / "rust")
    assert expected == actual, (name, expected, actual)
    if name in ("Installer", "InstallFromDMG"):
        assert actual["install_result"] == "DONE", (name, actual)
    elif name.endswith(".skip"):
        assert actual["install_result"] == "SKIPPED", (name, actual)
    elif name == "AppPkgCreator.repeat":
        assert actual["new_package_request"] is False, actual
    print("PASS differential " + name)
receipts = [plistlib.loads((root / impl / "installed-receipt.plist").read_bytes()) for impl in ("python", "rust")]
for receipt in receipts:
    receipt.pop("install-time", None)
assert receipts[0] == receipts[1], receipts
assert (root / "python/installed-permissions.txt").read_bytes() == (root / "rust/installed-permissions.txt").read_bytes()
print("Installed receipts match after normalizing installation time.")
PY
/bin/launchctl bootout system "$packaging"
/bin/launchctl bootout system "$installation"
echo 'Python reference upgrade, four helper processors, defaults, skips, and rollback checks passed.'
