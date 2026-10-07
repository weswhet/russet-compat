# Munki metadata option coverage

The pinned contract contains 46 declarations and 94 explicit aliases, plus the two framework help aliases. `munki-fixtures.json` supplies an independent MunkiImporter case for each alias. Each case invokes the frozen Python processor with the pinned Swift makepkginfo tool and compares the Rust processor in a separate FileRepo. No software is installed, and embedded scripts are never run.

Run `Scripts/differential_rust_munki.py --python /path/to/python3.11.9 --coverage-output /tmp/munki-coverage.json` on macOS with Munki 7.2.0.5787 and its pinned Python libraries. The JSON records actual statuses and pass/fail evidence per alias.

All declarations are parsed by `metadata::Options::parse` using the frozen manifest. Runtime implementation references below are in `rust/crates/munki/src/metadata.rs` unless stated otherwise.

| Declaration | Aliases | Runtime implementation | Differential fixtures |
| --- | --- | --- | --- |
| `name` | `--name` | `generate` | `option-name-alias-0` through `-0` |
| `displayname` | `--displayname` | `generate` | `option-displayname-alias-0` through `-0` |
| `description` | `--description` | `generate` | `option-description-alias-0` through `-0` |
| `pkgvers` | `--pkgvers` | `generate` | `option-pkgvers-alias-0` through `-0` |
| `restartAction` | `--RestartAction` | `generate` | `option-restartAction-alias-0` through `-0` |
| `uninstallMethod` | `--uninstall-method`, `--uninstall_method` | `generate` | `option-uninstallMethod-alias-0` through `-1` |
| `installcheckScript` | `--installcheck-script`, `--installcheck_script` | `generate` | `option-installcheckScript-alias-0` through `-1` |
| `uninstallcheckScript` | `--uninstallcheck-script`, `--uninstallcheck_script` | `generate` | `option-uninstallcheckScript-alias-0` through `-1` |
| `preinstallScript` | `--preinstall-script`, `--preinstall_script` | `generate` | `option-preinstallScript-alias-0` through `-1` |
| `postinstallScript` | `--postinstall-script`, `--postinstall_script` | `generate` | `option-postinstallScript-alias-0` through `-1` |
| `preuninstallScript` | `--preuninstall-script`, `--preuninstall_script` | `generate` | `option-preuninstallScript-alias-0` through `-1` |
| `postuninstallScript` | `--postuninstall-script`, `--postuninstall_script` | `generate` | `option-postuninstallScript-alias-0` through `-1` |
| `uninstallScript` | `--uninstall-script`, `--uninstall_script` | `generate` | `option-uninstallScript-alias-0` through `-1` |
| `versionScript` | `--version-script`, `--version_script` | `generate` | `option-versionScript-alias-0` through `-1` |
| `item` | `-i`, `--itemname`, `--appname`, `-a` | `disk_image` | `option-item-alias-0` through `-3` |
| `destinationpath` | `--destinationpath`, `-d` | `disk_image` | `option-destinationpath-alias-0` through `-1` |
| `destitemname` | `--destinationitemname`, `--destinationitem` | `disk_image` | `option-destitemname-alias-0` through `-1` |
| `user` | `--owner`, `--user`, `-o` | `disk_image` | `option-user-alias-0` through `-2` |
| `group` | `--group`, `-g` | `disk_image` | `option-group-alias-0` through `-1` |
| `mode` | `--mode`, `-m` | `disk_image` | `option-mode-alias-0` through `-1` |
| `pkgname` | `--pkgname`, `-p` | `disk_image` | `option-pkgname-alias-0` through `-1` |
| `uninstalleritem` | `--uninstalleritem`, `--uninstallerdmg`, `--uninstallerpkg`, `--uninstallpkg`, `-U` | `generate` | `option-uninstalleritem-alias-0` through `-4` |
| `installerChoices` | `--installer-choices`, `--installer-choices-xml`, `--installer_choices_xml` | `package` | `option-installerChoices-alias-0` through `-2` |
| `installerEnvironment` | `--installer-environment`, `--installer_environment`, `-E` | `generate` | `option-installerEnvironment-alias-0` through `-2` |
| `unattendedInstall` | `--unattended-install`, `--unattended_install` | `generate` | `option-unattendedInstall-alias-0` through `-1` |
| `unattendedUninstall` | `--unattended-uninstall`, `--unattended_uninstall` | `generate` | `option-unattendedUninstall-alias-0` through `-1` |
| `forceInstallAfterDate` | `--force-install-after-date`, `--force_install_after_date` | `generate` | `option-forceInstallAfterDate-alias-0` through `-1` |
| `file` | `--file`, `-f` | `generate; installs::create_item` | `option-file-alias-0` through `-1` |
| `installerType` | `--installer-type`, `--installer_type` | `disk_image; osinstaller::stage_metadata` | `option-installerType-alias-0` through `-1` |
| `nopkg` | `--nopkg` | `generate` | `option-nopkg-alias-0` through `-0` |
| `autoremove` | `--autoremove` | `generate` | `option-autoremove-alias-0` through `-0` |
| `onDemand` | `--OnDemand` | `generate` | `option-onDemand-alias-0` through `-0` |
| `minimumMunkiVersion` | `--minimum-munki-version`, `--minimum_munki_version` | `generate` | `option-minimumMunkiVersion-alias-0` through `-1` |
| `minimumOSVersion` | `--minimum-os-version`, `--minimum_os_version` | `generate` | `option-minimumOSVersion-alias-0` through `-1` |
| `maximumOSVersion` | `--maximum-os-version`, `--maximum_os_version` | `generate` | `option-maximumOSVersion-alias-0` through `-1` |
| `supportedArchitectures` | `--arch`, `--supported_architecture`, `--supported-architecture` | `generate` | `option-supportedArchitectures-alias-0` through `-2` |
| `updateFor` | `--update-for`, `-u`, `--update_for` | `generate` | `option-updateFor-alias-0` through `-2` |
| `requires` | `--requires`, `-r` | `generate` | `option-requires-alias-0` through `-1` |
| `blockingApplication` | `--blocking-application`, `-b`, `--blocking_application` | `generate` | `option-blockingApplication-alias-0` through `-2` |
| `catalog` | `--catalog`, `-c` | `generate` | `option-catalog-alias-0` through `-1` |
| `category` | `--category` | `generate` | `option-category-alias-0` through `-0` |
| `developer` | `--developer` | `generate` | `option-developer-alias-0` through `-0` |
| `iconName` | `--icon`, `--iconname`, `--icon-name`, `--icon_name` | `generate` | `option-iconName-alias-0` through `-3` |
| `notes` | `--notes` | `generate` | `option-notes-alias-0` through `-0` |
| `printWarnings` | `--print-warnings`, `--no-print-warnings` | `Options::parse; disk_image (writable-image hash policy)` | `option-printWarnings-alias-0` through `-1` |
| `version` | `--version`, `-V` | `Options::output_mode; importer::execute_classified (unexpected-error rejection)` | `option-version-alias-0` through `-1` |

## Additional behavior

Repeated array options, scalar last-wins behavior, warning-toggle ordering and writable-image hash effects, file-backed description/notes, script/uninstall-method precedence, stage_os_installer, invalid enums/versions/modes/dates/environment, missing scripts/files, and unknown future options have separate cases. Existing metadata unit differential tests cover effective `--nopkg` with no installer; the importer alias case verifies that `--nopkg` is ignored when an installer is supplied.

## Output modes

`--version` and `-V` return version text with status 0 from makepkginfo; `--help` and `-h` similarly return help text. The frozen Python MunkiImporter then tries to parse that text as a plist and raises `plistlib.InvalidFileException`, producing standalone exit status 1. Rust rejects these modes explicitly with the same unexpected-error class/status, before repository mutation. Error prose is clearer; traceback text is not a compatibility target. These flags cannot generate usable processor metadata. Unknown future options remain processor errors (standalone status 10).

Coverage is bounded to the recorded values and combinations. It does not establish equivalence for every possible input string or operating-system image.

Writable images are mounted read-write by both implementations. Those three explicit fixtures compare logical volume contents and modes instead of nondeterministic filesystem journal/header bytes, using the native harness readonly inspector. Each run still requires the imported image bytes to equal its post-inspection input bytes exactly. Metadata hashes are compared without normalization, including `N/A` versus a digest when warnings are disabled.
