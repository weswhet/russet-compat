# Russet compatibility suites

This repository checks that [Russet](https://github.com/weswhet/russet), a
native implementation of AutoPkg, behaves like Python AutoPkg. Each suite runs
the same fixtures through Russet and through Python AutoPkg 3.0.0 at upstream
commit `c36e58f8d3d8ddb70b6c2d848d2ceca7f767ce5c`, and then compares the
results, logs, and files that each one produces.

The suites live here, not in Russet, so that Russet contains no Python. This
repository doesn't include Python AutoPkg either: `Scripts/fetch_reference.sh`
downloads it at the pinned commit when you run the suites.

## What the suites compare

| Suite | Compares | Platforms |
| --- | --- | --- |
| `differential_rust_processors.py` | Portable processor fixtures, including logs at verbosity 2 | macOS, Linux, Windows |
| `differential_rust_http.py` | Downloads, GitHub API calls, and the download cache, against a local HTTP server | macOS, Linux, Windows |
| `differential_rust_download_cache.py` | Download cache metadata and extended attributes | macOS, Linux |
| `differential_rust_tls.py` | TLS trust and host name failures, against a local HTTPS server | macOS, Linux, Windows |
| `differential_rust_cli.py` | Commands, options, recipe reports, preferences, and repository handling | macOS, Linux, Windows |
| `differential_rust_native.py` | Package and disk image processors | macOS |
| `differential_rust_munki.py` | All seven Munki processor entry points, with the pinned Munki 7.2.0 tools | macOS |
| `differential_community_modern.py`, `differential_community_legacy.py`, `differential_community_builders.py` | The community processors that Russet implements, against their pinned sources in `community-source/` | macOS |
| `differential_rust_windows_native.py` | Chocolatey packaging and signature verification with the real Windows tools | Windows |
| `run_live_recipes.py`, `run_live_windows_recipes.py` | Unchanged `autopkg/recipes` recipes against live download servers | macOS, Linux, Windows |
| `compare_live_legs.py`, `verify_linux_outputs.py` | Same-day live runs on macOS with Apple's tools, macOS with Russet's native replacements, and Linux; then the Linux-built packages and disk images, checked with Apple's tools | macOS, Linux |

Three capture scripts check the frozen contracts that Russet builds against,
which live in Russet's `compatibility/` folder:

- `capture_rust_contract.py`: the 46 built-in processor manifests and the
  command-line options.
- `capture_community_processors.py`: the community processor manifests.
- `capture_munki_options.py`: the `makepkginfo` options from the pinned Munki
  source.

Run each one with `--check` to compare. Without `--check`, it rewrites the file
in your Russet checkout so that you can propose the change to Russet.

## Run the suites locally

You need Git, Bash, a Rust toolchain, and Python 3. The reference runs with
Python 3.11.9, the version that AutoPkg 3.0.0 ships.

1. Clone this repository, and then clone Russet into a `russet` folder inside
   it:

   ```sh
   git clone https://github.com/weswhet/russet-compat.git
   cd russet-compat
   git clone https://github.com/weswhet/russet.git russet
   ```

   To test another Russet checkout, set `RUSSET_ROOT` to its path instead.

1. Fetch Python AutoPkg at the pinned commit:

   ```sh
   bash Scripts/fetch_reference.sh
   ```

   The script creates a `reference` folder that holds the pinned commit and its
   `Code` folder. To put it somewhere else, set `AUTOPKG_REFERENCE_SOURCE`.

1. Get the reference interpreter.

   - On macOS, extract the interpreter from the published AutoPkg 3.0.0
     package. The script verifies the package and the interpreter against
     pinned hashes, and it doesn't install anything:

     ```sh
     python3 Scripts/prepare_rust_macos_reference.py --output reference-runtime
     export REFERENCE_PYTHON="$PWD/reference-runtime/expanded/Payload/Library/AutoPkg/Python3/Python.framework/Versions/3.11/bin/python3.11"
     ```

   - On Linux, create a virtual environment with Python 3.11.9 and the pinned
     packages:

     ```sh
     python3.11 -m venv .reference-venv
     .reference-venv/bin/python -m pip install appdirs==1.4.4 PyYAML==6.0.3 certifi==2025.10.5 lxml==6.1.0 xattr==1.2.0
     export REFERENCE_PYTHON="$PWD/.reference-venv/bin/python"
     ```

   - On Windows, follow the steps in
     `.github/workflows/windows-differential.yml`, which install the pinned
     packages from `reference/requirements.txt`.

1. Build Russet:

   ```sh
   cargo build --manifest-path russet/rust/Cargo.toml --locked -p russet
   ```

1. Run a suite:

   ```sh
   python3 Scripts/differential_rust_cli.py --python "$REFERENCE_PYTHON"
   ```

   Each suite prints `PASS` or `FAIL` for every case and a total at the end,
   and it exits with a nonzero status if any case fails. The suites find the
   Russet executable in `russet/rust/target/debug/`; use `--rust` to choose
   another one.

The Munki and community suites need more setup, such as the Munki tools in
`/usr/local/munki`. The steps in `.github/workflows/differential.yml` show
everything that each suite needs.

## How CI chooses the Russet version

| Workflow | Runs | Russet version |
| --- | --- | --- |
| `differential.yml` (macOS and Linux) | On pushes and pull requests to `main`, every Monday, and on demand | `main`, or the `russet_ref` that you enter |
| `windows-differential.yml` | Same as `differential.yml` | Same as `differential.yml` |
| `live-recipes.yml` (macOS) | Only on demand, because it downloads from vendor servers | The `russet_ref` that you enter, `main` by default |
| `live-recipes-windows.yml` | Only on demand, from this repository's `main` branch | The `russet_ref` that you enter, `main` by default |
| `live-recipes-linux.yml` (macOS and Linux) | Only on demand, because it downloads from vendor servers | The `russet_ref` that you enter, `main` by default |

To compare a Russet branch, tag, or commit, open the workflow on the **Actions**
tab, click **Run workflow**, and enter it in **russet_ref**. Each run records
the exact Russet commit in its summary.

The live recipe workflow installs Russet on the runner with
`cargo xtask package` and the archive's installer, so it needs a Russet
version that includes the `xtask` packaging command.

`live-recipes-linux.yml` does the same on every leg, including Linux, and runs
the installed `/usr/local/bin/russet`. To test a published release instead of
building one, enter its version, such as `0.0.1`, in **russet_release**. Each
leg then downloads that release's archive, checks it against the release's
`SHA256SUMS`, and installs it.

## Suite notes

The `docs/` folder describes two suites in more detail:

- `cli.md`: what the CLI suite compares, and how to run it.
- `munki-option-coverage.md`: how the Munki suite's fixtures cover each
  `makepkginfo` option declaration and alias.

`live-recipes-linux.yml` runs the recipes three ways on the same day, so
download changes between runs don't look like regressions. The macOS run with
Apple's tools is the baseline: a recipe that passes there and fails on a native
leg fails the comparison, unless the failure is a network diagnostic or, on
Linux, an operation Russet supports only on macOS, such as installing a
package. By default it runs the recipes that completed on macOS in
`evidence/live-recipes-2026-10-07.json`.

On Linux, the CLI suite accepts one deliberate difference: Russet's
`processor-info Unarchiver` shows `USE_PYTHON_NATIVE_EXTRACTOR` defaulting to
`False`, because Russet extracts archives with its built-in `ditto`
replacement there, where Python AutoPkg shows `True`.

`Scripts/verify_rust_macos_upgrade.sh` upgrades a running Python AutoPkg
installation to Russet and then restores it. It runs only as root inside an
empty, disposable macOS virtual machine, and it refuses to run anywhere else.

## Evidence

The `evidence/` folder keeps the results that these suites recorded while
Russet was developed:

- `validation.md`: development checks and their limitations.
- `macos-vm-validation.md`: installs, upgrades, and rollbacks in disposable
  macOS virtual machines.
- `live-recipes-2026-10-07.md` and `.json`: a sweep of the `autopkg/recipes`
  repository.
- `community-live-recipes-2026-10-07.md` and `.json`: a follow-up sweep of the
  recipes that use the community processors.
- `linux-live-recipes-2026-10-07.md` and `.json`: the same live recipes on
  macOS with Apple's tools, on macOS with Russet's native replacements, and on
  Linux.

These records mention commits and workflow runs in Russet's earlier history,
and they describe the code as it was then.

## Repository layout

| Path | Contents |
| --- | --- |
| `Scripts/` | The suites, the capture scripts, and `fetch_reference.sh` |
| `Scripts/compat_paths.py` | The locations of the Russet checkout and the reference |
| `community-source/` | Community processor sources from `autopkg/recipes` at commit `0f7b61ab061c77710be4140c404910a11f89fc7b` |
| `docs/` | Notes on the CLI and Munki suites |
| `evidence/` | Recorded results |
| `.github/workflows/` | The CI workflows |

## License

This repository uses the [Apache License 2.0](LICENSE). The files in
`community-source/` keep the license notices of their original authors.
