# CLI differential coverage

`Scripts/differential_rust_cli.py` executes the actual Python CLI extracted from
commit `c36e58f8d3d8ddb70b6c2d848d2ceca7f767ce5c` and the Rust CLI in separate
temporary directories. It compares exit codes, stdout, stderr, generated recipe
values, preference files, recipe maps, receipts, and run reports.

Every invocation has an isolated home, configuration directory, recipe repository,
cache, override directory, and preference file. On macOS the extracted Python
source receives a harness-only change: its preference bundle identifier becomes
a unique empty test domain. The file preference directory is also redirected to
the test configuration directory because Windows shell-folder APIs can ignore
`LOCALAPPDATA`. The original preference parsers remain intact. Rust's `AUTOPKG_RS_PREFERENCES_FILE` bypasses native
preferences during comparison. Both implementations otherwise execute their real
command code. Their test repositories and caches are never shared.

The generated matrix covers help, unknown options, missing option values, and
empty invocations for all reference verbs and aliases. The additional cases in
`cli-fixtures.json` cover valid and malformed option forms, all 46 processor
inspection commands, recipe inheritance, install aliases, check phases, recipe
lists, pre/postprocessors, summaries, overrides, repository rejection paths,
audits, trust commands, template creation, and GitHub token preference precedence
against a local HTTP server. Verbosity levels 1–3 compare environment and processor
input/output formatting. Control and filesystem recipes compare Copier,
FileMover, FileFinder, FindAndReplace, PathDeleter, Symlinker, PkgRootCreator,
VariableSetter, PackageRequired, and StopProcessingIf at verbosity levels 1–2,
including default-value messages and missing-path skipping. Native standalone
processor execution routes operation messages to stderr when explicitly verbose,
keeping its public plist stdout stream parseable. A real loopback Git daemon exercises clone, repository
listing, unchanged and changed pulls, recipe discovery, deletion, and the resulting
preferences, map, branch, commit, and recipe contents. Each implementation uses
its own source, bare remote, and checkout; Git identities and commit dates are
fixed fixture inputs. A failed PlistReader recipe compares exit status, diagnostic
framing, failure summaries, receipts, and report fields. These cases extend the
corpus; they do not certify every Git error, verbose processor message, or failure
path required for the complete release gate.

Run with Python **3.11.9**, the interpreter bundled by the pinned AutoPkg release
(see `Scripts/make_new_release.py` and `CHANGELOG.md` at the pinned upstream
commit), and the pinned reference
dependencies. Python 3.13 and later change runtime docstring indentation, so the
harness rejects a different interpreter instead of hiding output differences:

```sh
python3 Scripts/differential_rust_cli.py --python /path/to/venv/bin/python
python3 Scripts/differential_rust_cli.py --python /path/to/venv/bin/python --check-parsers
```

`--case REGEX` selects cases; `--results PATH` retains both outcomes for inspection.
`--capture-parsers` refreshes the reference parser and processor display metadata.
Parser metadata comes from actual `optparse` registration, not a hand-written
option inventory. Runtime processor metadata preserves Python's declaration
order, tuple representations, and descriptions. SignTool's default path remains
a native runtime lookup because it depends on the installed Windows SDK.

Normalization is limited to temporary root paths, receipt filename timestamps,
the Chocolatey default sentinel's process-specific memory address, and the
loopback HTTP API and Git daemon port assignments, and the startup warning caused by the intentionally empty Python preference domain.
Generated plist/JSON/YAML files are compared as values; stdout and stderr are
compared as text. Both processes receive the same program name through argv[0].

Failure reports retain the `traceback` field in both implementations. Python stores
Python exception frames; Rust stores the failure message, recipe and processor
context, and a captured native Rust backtrace. This is an explicit runtime
representation boundary, not timestamp normalization: the harness validates both
diagnostics separately, preserves their full text in `--results`, and compares
every other report field strictly. Successful failure cases are labeled with this
boundary in harness output. The native backtrace is captured even when development
debug logging is disabled.

On Windows, native CLI text output uses CRLF just like Python text streams; binary
plist output remains unchanged. Text line endings are not normalized by the
comparison. Temporary roots appearing in Python-quoted strings receive the same
root substitution as plain paths; other backslashes remain significant.

On non-macOS hosts, four exact reference import diagnostics are recorded separately
as `reference_import_diagnostics`: unavailable Foundation imports in
MunkiSetDefaultCatalog, MunkiInstallsItemsCreator, and StopProcessingIf, plus the
reference's unavailable-xattr message. Their original text and stream are retained.
Rust does not invent Python dependency warnings. This explicit dependency boundary
removes only those exact diagnostic lines from command-output comparison; all
other output is compared strictly. StopProcessingIf execution remains covered by
the frozen native Foundation oracle when the Python runtime cannot load Foundation.
Git checkout inspection errors are retained in results instead of terminating the
harness and losing earlier evidence.
