# Installed macOS helper validation

On October 6, 2026, the native archive passed installation and helper checks in
a newly created Tiddly VirtualMac2,1 guest running macOS 15.6.1 (24G90), arm64.
The guest was created for this task; no host installation or launchd service was
changed. The guest had no AutoPkg or separately installed Python runtime.

`Scripts/verify_rust_macos_vm.sh` requires an explicit disposable-VM flag and
a VirtualMac hardware model, or an explicitly opted-in GitHub-hosted macOS
runner. Its completed second VM run verified:

- Clean archive installation at the existing AutoPkg paths and both original
  launchd labels and sockets.
- Native CLI execution with `PATH=/nonexistent`.
- Package and app-package creation requested by the unprivileged nobody user.
- Actual package installation, installed payload bytes, and pkgutil receipt.
- Rejection of a package payload owned by a different user, using kernel peer
  credentials; malformed socket-request rejection.
- Privileged file copying from a real mounted disk image.
- Native Munki default-catalog preference reading in the disposable guest.
- Upgrade, restoration of the preceding native generation, and rollback of the
  initial clean installation, including unloaded launchd jobs.

The installed fixture payload and receipt were removed; the guest catalog
preference was deleted. The full test ended with exit status zero. Evidence
was copied from `/private/tmp/autopkg-rust-validation.IzZhOU3B` to
`rust/dist/macos-vm-evidence` before a later guest restart cleared temporary
files. Rollback generations remain in the guest.

## Upgrade from the running Python reference

`Scripts/verify_rust_macos_upgrade.sh` then passed in the same disposable guest.
The development reference combined unmodified `Code` from `c36e58f`, a
relocatable Python 3.11.9 interpreter, and the pinned reference dependencies.
It was installed at the legacy paths with the original launchd plists and
the `/usr/local/autopkg/python` interpreter entry point. This is a development
reference installation, not a historical signed installer package.

The expanded check ran the following differential cases through both installed
implementations, with separate input and cache roots:

| Processor | Verified behavior |
| --- | --- |
| PkgCreator | An unprivileged request creates a real flat package through the privileged helper. |
| AppPkgCreator | `pathname` discovers the synthetic app; default bundle identifier, version, output path, version key, and build flags match. A repeated request reuses the package and returns `new_package_request=False`. |
| Installer | With both skip inputs omitted, the unprivileged processor installs the fixture package through the privileged helper. `download_changed=False` skips installation after the payload and receipt have been removed. |
| InstallFromDMG | A real disk image is mounted and its file is copied through the privileged helper, using default item options and separate destinations. `download_changed=False` leaves the removed destination absent. |

All seven output environments matched after normalizing the isolated fixture
roots. The installed package and copied image payload matched their source
bytes; ownership and permissions matched between implementations. Package receipts matched after excluding
only `install-time`. The app package was expanded and its bundled Info.plist
matched the source app. No app package or embedded script was installed.

After the Python cases, the script upgraded to the native archive below and
ran the Rust cases with `PATH=/nonexistent`. It then rolled back and verified
SHA-256 identity of the restored CLI, both helpers, and both launchd plists.
The restored Python CLI, packaging helper, and installation helper all handled
new requests successfully.

The final expanded run exited zero and wrote evidence to
`/private/tmp/autopkg-reference-upgrade.wX7eiANR`. Its output plists, helper
diagnostics, receipts, and hash records were copied to
`rust/dist/macos-helper-differential-evidence.tar.gz` (SHA-256
`55d1f56587bf8ed5ab98b0824669aac222c033a17daa938a36b56169c48ab388`).
The reference archive SHA-256
was `259dd1f806d52eddeec419b61dcba2c339bc36a52bad19f65a857fe85cb97492`.
The unique `/usr/local/share/autopkg-reference-upgrade-wX7eiANR` payload and
`org.autopkg.rust.upgradefixture.wX7eiANR` receipt were removed between the
implementations and again after the restored-helper check. A separate guest
check confirmed that the payload, receipt, AutoPkg installation, CLI symlink,
and both launchd jobs were absent. Cleanup also runs if a test fails. Python
was only a development reference, never part of the native archive.

A large QGA reference-file upload stalled before this run. Only the task VM
was restarted, and the reference archive was instead transferred over the
private VM network and hash-verified. An initial retry stopped because the
reboot removed the previous temporary native archive. The script now checks
both archives before installing the reference; after restoring that archive,
the complete run above passed.

## Tested archive contents

These hashes identify the candidate tested, independently of later source edits.

| File | SHA-256 |
| --- | --- |
| `bin/autopkg-rs` | `ad76bc22be11ac2d314ce4d3592dc03072f1c1ed48a0adc3a66f2bbc82dee604` |
| `bin/autopkgserver-rs` | `33c3eb2687723328f85f03017626101dc0a10d9ff5b3b9f0b86a85b4070dfe5a` |
| `bin/autopkginstalld-rs` | `71a3012554457000c6b33467f5eaae60ee07b9a34b39aee17187e645da5f6e52` |
| `install.sh` | `3a88a0fef958cab0c6a585872f0dd7aa9e94b78010c2ad2101ff279a10559c3a` |

This verifies this arm64 macOS workflow. It does not establish the Intel hosted
image, Windows, all recipe compatibility, or rollback from every historical
Python installer. Staged tests separately preserve an existing Python-shaped
installation, executable symlink, and launchd configuration.

## Published Python 3.0.0 installer upgrade

On October 6, 2026 (October 7 UTC), the existing `autopkg-rust-release` guest
passed the same seven helper differentials after installing the actual published
Python 3.0.0 package with `/usr/sbin/installer`. This closes the distinction
between the earlier development reference layout and the published installer.
The guest was revalidated as running macOS 15.6.1 with native QGA connected;
it was neither recreated nor restarted for this run.

The published package SHA-256 was
`cd554cc4b807dec7fb0aee73fe9ab0cddf764e215fb6f10c1b7f7d263c52f8cf`.
Its installed receipt was `com.github.autopkg.autopkg`, and its CLI reported
`3.0.0`. The Rust arm64 archive came from successful public workflow run
[37549787536](https://github.com/weswhet/rustypkg/actions/runs/37549787536),
commit `4907837`; its SHA-256 was
`35cd191141bc42c121d02ee10a748dc9e0899008466e66c52f999e401741bb5b`.

The verifier installed the published package, ran real privileged package
creation, installation, and disk-image copying, upgraded to Rust, repeated those
operations with Python absent from PATH, and rolled back. The restored Python
CLI reported `3.0.0`; CLI/helper/plist hashes matched their original installed
bytes. Restored Python helpers then created and installed a new fixture package.
All seven output environments matched, including defaults, repeat, and skip
behavior. The completed QGA job exited zero. A separate host-side comparison of
the collected plist evidence reconfirmed all seven environment comparisons.

Evidence is retained at `rust/dist/published-upgrade-native/`:
`guest-run.json` records QGA job `099A4C84-DC9E-4775-BE47-348211F1CC45`, and
`published-upgrade-evidence.tar.gz` contains guest fixture directory
`/private/tmp/autopkg-reference-upgrade.Us69U2ho`. The evidence archive SHA-256 is
`008d576aebdcad3261040211061232f37e8018e592a89236ce0c4466079cdecb`.
The job API did not retain stdout; the evidence includes install output,
processor plists and stderr, before/after versions, hashes, and installed receipts.

A separate guest check confirmed removal of the unique payload, its receipt,
the published package receipt, both launchd jobs, installation directories, and
CLI symlink. Task evidence and rollback generations remain in the disposable
guest. No host AutoPkg installation or launchd service was changed. This verifies
published-package rollback for this arm64 artifact and helper protocol; it does
not certify later CLI trust changes or every historical Python release.

## Published-package upgrade with the trust-corrected archive

The same test passed again using the exact arm64 archive from development run
[37552179104](https://github.com/weswhet/rustypkg/actions/runs/37552179104),
commit `de6bef79b27f97cb56560c90fd0fe4f759ee88b7`. The downloaded GitHub artifact
`11452824796` matched its advertised SHA-256
`f0e375bc648c3f137220251fdeb89a90bc81ae69cc992133949977323f87d1bc`.
The contained archive SHA-256 was
`b520d9f52138a08717a4b16797febb0cb95a3b16258ebe466fed9635be7411c0`;
its CLI SHA-256 was
`facfed6dbb83eb1d7ef7b184f51f08470052b93897f514e44ab525f68cc3fb53`.

QGA job `2CE50941-8357-4DE9-B5C6-13738715C5A7` exited zero. All seven helper
comparisons and the rollback checks passed. Unlike the earlier run, this run
retains complete redirected output in `rust/dist/final-published-upgrade/run.log`.
The adjacent `identity.json` records source, artifact, and payload identities.
`evidence.tar.gz` has SHA-256
`88ed36bc37455edc5c20a535cc476a8245d2222121167103bcbc49919989e214`.
Guest cleanup separately confirmed removal of fixture payloads and receipts,
installation paths, the CLI symlink, and launchd jobs. Host installations and
services were unchanged.

## Checkpoint `7dfb5ec` published-package upgrade

The exact arm64 artifact from successful development run
[37555649207](https://github.com/weswhet/rustypkg/actions/runs/37555649207),
commit `7dfb5ecf5352056a3bdeaa740a9e71fbbd03b492`, passed the published-package
upgrade test in the existing `autopkg-rust-release` VM. The guest remained macOS
15.6.1 with native QGA connected; it was not recreated or restarted.

| Artifact | SHA-256 |
| --- | --- |
| GitHub artifact ZIP `11454860459` | `938fe4e798010d91482fad39f41aeb1156394b2a470bf9538f7e626e57c190f6` |
| Native arm64 archive | `a9217d2f87df76b25542121d72071e64b0644be3f5f3b88ae22d64dbd69d573a` |
| CLI | `85398566a34123764855ab64f2f66fbf4ac0def1179f7d726f86b77252dd14bc` |
| Packaging helper | `42859d3551fe5d00b95483e3235ad247beb67a1af7da9efd65edf0ec5a10fa8f` |
| Installation helper | `00d80808b42ee71b3ae5a943c1b1cc48437803c9467afff5fd5679678e811a60` |
| Installer | `3a88a0fef958cab0c6a585872f0dd7aa9e94b78010c2ad2101ff279a10559c3a` |
| Evidence archive | `8c1bf9646be20b1911fdee9c852dc41613a91abe4ba6003f13f3d359e7f47036` |

The downloaded ZIP matched GitHub's advertised digest. The private-network
guest transfer matched the native archive hash. The original published Python
3.0.0 package retained SHA-256
`cd554cc4b807dec7fb0aee73fe9ab0cddf764e215fb6f10c1b7f7d263c52f8cf`.

QGA job `0614BE10-961C-426D-836A-12BF5545B376` exited zero. PkgCreator,
AppPkgCreator, repeated AppPkgCreator, Installer, skipped Installer,
InstallFromDMG, and skipped InstallFromDMG all matched their Python results.
Installed receipts matched after normalizing installation time. Rollback
restored the original Python CLI, helpers, and launchd plists byte-for-byte;
restored helpers successfully created and installed another fixture package.

Evidence is retained under `rust/dist/7dfb5ec-published-upgrade/`, including
`identity.json`, `qga-job.json`, `run.log`, and `evidence.tar.gz`. The archive
contains guest fixture `/private/tmp/autopkg-reference-upgrade.NFoLHsI7` and a
separate cleanup record. Cleanup verified absence of the installed directories,
CLI symlink, launchd jobs, unique payload, published-package receipt, and fixture
receipt. Task evidence and rollback generations remain in the disposable VM.
No host installation, service, tracked source, or documentation was changed by
this verification. This result binds the arm64 published-upgrade route to this
checkpoint. Windows and Linux exact-archive checks subsequently passed; macOS
archive checks remain pending. The regex correction requires new final
artifacts and validation before promotion.

## Checkpoint `4bb0e72` published-package upgrade

The exact arm64 artifact from successful four-target development run
[37557550476](https://github.com/weswhet/rustypkg/actions/runs/37557550476),
commit `4bb0e72040ba7349cd0c8d4a6f51905f8082105c`, passed the same published
Python 3.0.0 upgrade and rollback route in the existing disposable VM.

| Artifact | SHA-256 |
| --- | --- |
| GitHub artifact ZIP `11455498407` | `c7e7f9f8153c1bfc6065fae27ff13bb006767a6b6e59880ff48179a204e9803a` |
| Native arm64 archive | `59557bef1878257eab47ee18d90bb8737fcfe0635e6f6a51f68c49f76600455c` |
| CLI | `8c029be365a7c660e463361625b81f05345dca056ef890c819b56b42f3934eed` |
| Evidence archive | `dae5bec41cc6660e7ab4331de5fb2c557494ce6d8ff61d1f3d489facba67fba8` |

The downloaded ZIP matched GitHub's digest, and the archive hash matched after
private-network transfer. Packaging helper, installation helper, and installer
hashes are unchanged from the `7dfb5ec` table above. The published Python
package retained SHA-256
`cd554cc4b807dec7fb0aee73fe9ab0cddf764e215fb6f10c1b7f7d263c52f8cf`.

QGA job `800BFFA6-B6CA-46E8-8776-A1EE024D28A1` exited zero. All seven helper
comparisons passed: PkgCreator, AppPkgCreator, repeated AppPkgCreator, Installer,
skipped Installer, InstallFromDMG, and skipped InstallFromDMG. Rollback restored
working Python packaging and installation requests. The fixture receipt and
payload checks passed, with only installation time normalized.

`rust/dist/4bb0e72-published-upgrade/` retains `identity.json`, `qga-job.json`,
`run.log`, and `evidence.tar.gz`. A separate cleanup check confirmed the absence
of task payloads and receipts, installation directories, CLI symlink, and both
launchd jobs for guest fixture `/private/tmp/autopkg-reference-upgrade.4lgOATcT`.
The VM was neither recreated nor restarted. Host installation and services were
unchanged. This verifies the artifact's published-package upgrade route;
subsequent harness corrections and final archive gates are recorded separately.
