# Historical development validation

These notes preserve checks and limitations recorded during the migration.
See the RustyPkg 1.0.0 release validation record (`RELEASE-VALIDATION.md` in Russet) for
the final source, exact artifact provenance, platform gates, and rollback evidence.

Reference: AutoPkg `c36e58f8d3d8ddb70b6c2d848d2ceca7f767ce5c`; Munki
`7.2.0.5787`; release-reference Python `3.11.9`. These results describe this checkout, not a stable release
certification. Python remains the installed/default implementation.

## Migration checkpoints

At `092fd02`, all four development jobs in
[37559097158](https://github.com/weswhet/rustypkg/actions/runs/37559097158)
and the Windows comparison workflow
[37559097147](https://github.com/weswhet/rustypkg/actions/runs/37559097147)
passed, including the ten harness tests and eleven native Windows cases.
An earlier Intel archive job exposed a busy-device failure in synthetic DMG
fixture setup, before Rust mounting or cleanup ran. The fixture now explicitly
unmounts its freshly partitioned volumes before setup detachment. Its existing
native test passes locally; the Rust cleanup assertions remain unchanged.
This fixture correction requires a new final-source validation run.

The ASCII regex distinct-scalar limit has been removed. Matching now retains
the original Unicode text and capture offsets. The expanded HTTP corpus passes
100 cases against the published macOS Python runtime, including five cases
above the former limit. All 254 vendored regex tests pass, along with a wrapper
test covering the entire Unicode scalar alphabet. Final-source platform and
archive validation must be repeated for this change.

The published `v3.0.0` macOS package is now the authoritative runtime reference,
not a separately provisioned Python 3.11.9 environment. Its SHA-256 is
`cd554cc4b807dec7fb0aee73fe9ab0cddf764e215fb6f10c1b7f7d263c52f8cf`.
`Scripts/prepare_rust_macos_reference.py` verifies and extracts that package
without installing it. The shipped `sitecustomize.py` replaces an absent or
invalid `SSL_CERT_FILE` with bundled certifi; matching the Python version alone
does not reproduce that policy. The corrected Rust Python-downloader trust
path and native curl fallback pass 32 TLS cases and 95 verbose HTTP cases
against this runtime. The native curl cases compare exact error diagnostics
for absent, empty, missing, and directory certificate overrides. The portable
processor corpus passes 111 cases, CLI 179, native package/image workflows 66,
and Munki 174 in one complete run. The full sequential workspace run using the
published interpreter passes 190 tests with none ignored.

Portable logging also passed all 111 fixtures at verbosity levels 0, 1, 2,
and 4 with the earlier provisioned reference. The current published-runtime
HTTP corpus passes 95 cases at verbosity 2; the earlier 93-case corpus also
passed levels 1 and 4. Native package/image logging covers 22 workflows at
verbosity levels 0, 1, and 4, including malformed package, payload, image, and
app-package inputs. CLI cases compare stdout, stderr, status, receipts, reports,
persistent files, and stateful pre/postprocessor ordering and failures.

The runtime pin matters: Python 3.14 changes multiline docstring indentation,
and the published package supplies additional TLS policy. Earlier local
comparisons used other interpreter builds; current results above use the
extracted published package without installing it on the host.

Installed macOS helper validation passed twice in a disposable macOS 15.6.1
ARM VM. The latest run covers real package creation and installation, kernel
peer authorization, malformed socket requests, disk-image copying, application
packaging, launchd activation, upgrade, and two rollbacks. See
[macos-vm-validation.md](macos-vm-validation.md) for exact artifact identities
and limits. Host launchd services and the installed AutoPkg remain untouched.

The candidate installer also passed in a fresh Ubuntu 24.04 x86-64 container
without Python installed: real archive installation, file creation with an
empty PATH, upgrade, and two rollbacks. This check used the earlier Linux
binary with the current installer; CI rebuilds and repeats it against current
source. Local shell/archive distribution tests pass (nine tests); two
PowerShell tests skip when that runtime is absent.

Hosted validation runs in the public [Russet repository](https://github.com/weswhet/russet/actions).
At commit `7dfb5ecf5352056a3bdeaa740a9e71fbbd03b492`, development run
[37555649207](https://github.com/weswhet/rustypkg/actions/runs/37555649207)
passed all four target jobs: macOS arm64 and Intel, Linux x86-64, and Windows
x86-64. These jobs include native build and distribution checks; hosted macOS
jobs exercise installed helpers. Windows differential run
[37555649246](https://github.com/weswhet/rustypkg/actions/runs/37555649246)
also passed, including all 11 active native Chocolatey and signature comparisons
against Python. Those cases cover omitted defaults, explicit tool paths, real
package creation, valid signatures, unsigned rejection, and applicable failures.

The native Windows comparison now preserves installer, nuspec, and payload
bytes while comparing OPC content-type declarations independently of their
insignificant order. Duplicate declarations and all attributes remain visible.
Chocolatey log timestamp and process-ID prefixes are normalized; remaining log
text is compared exactly. Earlier Windows TLS, file-handle, encoding, default
logging, and signature blank-line failures were corrected before this checkpoint.

The exact arm64 artifact from the successful development run also passed an
actual published Python 3.0.0 package installation, Rust upgrade, all seven
helper comparisons, and rollback in the disposable VM. Artifact and evidence
hashes are recorded in [macos-vm-validation.md](macos-vm-validation.md).

The Windows exact-archive run
[37556485004](https://github.com/weswhet/rustypkg/actions/runs/37556485004)
passed. The Linux job in exact-archive run
[37556482892](https://github.com/weswhet/rustypkg/actions/runs/37556482892)
also passed; its macOS archive jobs remain pending. These results apply to the
`7dfb5ec` checkpoint, not a later artifact. The ASCII-regex scalar-limit fix
described above requires new final-source artifact gates. Do not
infer pending archive results from source-built checks or the arm64 VM result. The standard macOS
comparison encountered an `hdiutil create` failure while preparing a fixture,
before its Munki comparison; a fresh-host rerun is pending. All required
archive and differential jobs must pass for the selected final artifacts before
promotion. No stable executable switch is recorded here.

The subsequent `4bb0e72040ba7349cd0c8d4a6f51905f8082105c` checkpoint passed
all four development jobs in
[37557550476](https://github.com/weswhet/rustypkg/actions/runs/37557550476)
and the Windows source differential workflow
[37557550507](https://github.com/weswhet/rustypkg/actions/runs/37557550507).
Its exact arm64 artifact also passed the published Python 3.0.0 upgrade,
seven helper comparisons, restored-Python requests, and rollback in the VM;
see [macos-vm-validation.md](macos-vm-validation.md) for hashes.

Windows exact-archive run
[37558162435](https://github.com/weswhet/rustypkg/actions/runs/37558162435)
identified a randomly emitted Chocolatey Pro advertisement as the remaining
comparison difference. Chocolatey 2.7.4 can select from seven messages defined
in its [package service](https://github.com/chocolatey/choco/blob/2.7.4/src/chocolatey/infrastructure.app/services/ChocolateyPackageService.cs).
The harness recognizes only those exact complete advertisement blocks; it
preserves warnings and all other output. Its ten offline tests cover all seven
messages and use native fixture paths on Windows. This correction requires new final-artifact gates and does
not turn the failed comparison into a passing run. Unix exact-archive run
[37558160224](https://github.com/weswhet/rustypkg/actions/runs/37558160224)
is still in progress at this checkpoint. No stable switch is recorded.

Verbose processor output is now compared separately from reference import
warnings and final exception diagnostics. The optional reference worker log
file captures output only during processor execution. Comparisons preserve
multiline messages and default declaration order; the Chocolatey default
sentinel's process-specific address is explicitly normalized.

## Earlier development baseline

- All 46 frozen processor manifests and the CLI contract check passed.
- Workspace tests, including opt-in native and pinned-reference cases: 163
  passed, none ignored, with `--include-ignored --test-threads=1`.
- Chocolatey null-field and default-cache regressions are included in the full
  result. Nine renderer boundary cases were also checked against Python.
- Portable differential fixtures: 21/21 passed on arm64 debug and x86-64
  release through Rosetta.
- Local HTTP differential fixtures: 20/20 passed on arm64 release.
- Native predicate differential fixtures: 9/9 passed on arm64 release; the
  preceding x86-64 release also passed all nine through Rosetta.
- Strict workspace Clippy and formatting checks passed.
- Both macOS architectures built CLI and helper binaries. Portable example
  recipes ran with `PATH=/nonexistent`, confirming those workflows need no
  discoverable Python. Archives contain no Python runtime.

The full native suite exercises package and disk-image creation, extraction,
Foundation predicates, signature checks, separate Munki repositories, metadata
options, receipts, icons, synthetic OS-installer metadata, trust, and isolated
helper sockets. It does not install OS software or privileged launchd services.

One concurrent full-suite run lost a temporary mounted image before a reference
comparison. The same test passed alone and the full sequential run passed. This
intermittent native-test concurrency failure remains unresolved; use sequential
execution for the opt-in native fixture suite until isolated further.

## Cross-platform builds

Linux x86-64 workspace tests passed in a Debian Bookworm container: 143 passed,
zero failed, two opt-in reference tests ignored. The container ran under x86-64
emulation on the ARM Mac. GNU bfd linked successfully after the emulated LLVM
linker crashed; one Cargo spawning stall was resolved by a single-build-job retry.
That historical source checkpoint passed with that configuration. Hosted Ubuntu CI and native-machine execution remain distinct
checks.

An earlier Linux archive was extracted in a clean Debian Bookworm slim x86-64
container. Neither Python executable nor the python3-minimal package was
present. With an empty PATH and temporary HOME, the binary listed all 46
processors and completed a three-step Unicode/null/report recipe. Runtime
libraries were limited to glibc, libm, libgcc_s, and the system loader.

Windows x86-64 GNU release compilation and the complete workspace/all-targets
compile check passed using an ARM64 Linux host and MinGW. The PE imports only
Windows system DLLs. The archive passed ZIP integrity and executable-byte
verification. See [windows-gnu-build.md](https://github.com/weswhet/russet/blob/main/compatibility/windows-gnu-build.md) for toolchain
details and artifact hashes. This is not a Windows runtime or MSVC test.

## Remaining release gates

- Confirm the pending exact-archive workflows and fresh-host macOS differential
  rerun for the final candidate; retain artifact identities and outcomes.
- Complete the release review of success, failure, and default coverage across
  all 46 processors and CLI commands. Representative passing fixtures do not
  establish every possible input combination.
- Confirm the regex correction on every target using the expanded corpus.
- Make promotion and the executable switch a separate recorded action after
  the required gates pass. Existing Python installations remain the rollback
  route; no source or preference format conversion is required.

During default-cache testing, previously unisolated tests created seven files
under a newly created normal AutoPkg cache. Birth-time and file-type checks
confirmed their provenance; those files and empty cache directories were removed.
All CLI recipe tests now use temporary preferences or isolated home directories.
A subsequent check confirmed the normal cache remained absent.

No host executable switch, host privileged-service activation, or production
repository comparison was performed. Reference and Rust mutable fixture
repositories and caches are separate. Privileged activation is confined to
the disposable VM and hosted macOS runner checks.
