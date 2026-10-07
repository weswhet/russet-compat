# Primary-repository processor follow-up: October 7, 2026

All **94 recipes previously rejected for custom processors** now resolve their
processors natively. The hosted macOS sweep returned **81 successful exits,
12 failures, and one timeout**, with no processor rejections. Of the 81 successful
exits, **65 completed their workflows**, 15 intentionally stopped with Office 2016
deprecation warnings, and one skipped catalog rebuilding. The latter 16 are not
completed download or packaging workflows.

These additions are on `main`, not in the published 1.0.0 archives. The original
46 processor contracts remain frozen. Twelve promoted implementations and one
shared alias add 13 names, giving 58 implementations under 59 recipe names. See
[processor support and boundaries](https://github.com/weswhet/russet/blob/main/compatibility/community-processors.md).

## Scope and provenance

The selection is exactly the 94 `unsupported_custom_processor` entries in the
[historical 228-recipe baseline](live-recipes-2026-10-07.json). The baseline and
its counts remain unchanged. Recipes came from
[autopkg/recipes at `0f7b61ab061c77710be4140c404910a11f89fc7b`](https://github.com/autopkg/recipes/tree/0f7b61ab061c77710be4140c404910a11f89fc7b),
with source bytes checked before and after execution.

[The eight-shard sweep](https://github.com/weswhet/russet/actions/runs/37572245613)
used GitHub-hosted `macos-15` runners, separate caches and Munki repositories,
and a 900-second limit per implementation and recipe. Its source was
[`b4085d008253c54ad201ab998c12333e440ef399`](https://github.com/weswhet/russet/commit/b4085d008253c54ad201ab998c12333e440ef399);
the native binary SHA-256 was
`2815ed6225acc20d6e929cf22c12d68fc597bd9c76fc82be99ec17fdbdfabdba`.
No local VMs were used.

The [machine-readable evidence](community-live-recipes-2026-10-07.json) contains
all 94 recipe paths, hashes, statuses, failure messages, artifact directories,
and log/report digests. The Actions run retains `live-recipes-0` through
`live-recipes-7` artifacts with full logs and reports. Focused retries are recorded
separately and do not replace the sweep counts.

Successful Rust runs were **not** rerun with Python. These live results establish
native recipe coverage, not complete end-to-end parity. Python fallback ran after
Rust failures. Where both failed, their causes were inspected; equal failure
statuses alone do not establish equivalent behavior.

## Sweep outcomes

| Outcome | Recipes |
|---|---:|
| Completed workflow | 65 |
| Expected Office 2016 deprecation stop | 15 |
| Expected catalog skip (`Munki/MakeCatalogs.munki.recipe`) | 1 |
| Failed | 12 |
| Timed out | 1 |
| Rejected for an unsupported processor | 0 |
| Total | 94 |

The 15 deprecation stops cover Excel, OneNote, Outlook, PowerPoint, and Word 2016
across download, Munki, and package recipes. They follow the pinned provider's
retirement behavior; no download is expected.

| Remaining sweep cases | Count | Observed result |
|---|---:|---|
| Five `AdobeFlashPlayer` recipes | 5 | Both implementations downloaded content that `hdiutil` rejected as an unrecognized disk image. |
| `AutoPkg/AutoPkgGitMasterNoPython.pkg.recipe` | 1 | Both failed copying the missing `Code/FoundationPlist` path from the pinned AutoPkg source. |
| `MSOfficeUpdates/MSOfficeMacProduct.download.recipe` | 1 | Template product `MicrosoftOfficeMacProduct` is not a concrete supported product; both failed. |
| `Mozilla/FirefoxWindows.download.recipe` and `Mozilla/Firefox.nupkg.recipe` | 2 | Windows signature verification could not run on macOS. Native Windows retries are described below. |
| `RelocatablePython.build` and `.dmg` | 2 | Rust reached pip with an unresolved nested requirements path. Initial Python fallback lacked its legacy interpreter path; the focused retry below confirmed the actual pip failure. |
| `SassafrasK2Client/SassafrasK2Client.munki.recipe` | 1 | Both received HTTP 404 from the configured download URL. |
| `AutoPkg/AutoPkgGitMaster.pkg.recipe` | 1 | Rust exceeded 900 seconds. Initial Python fallback lacked `/usr/local/autopkg/python`; that fallback does not explain the Rust timeout. |

An [extended AutoPkg build retry](https://github.com/weswhet/russet/actions/runs/37576169122)
is **pending** at this evidence checkpoint. The timeout remains unresolved here.

## Focused requirements-path comparison

The [focused RelocatablePython retry](https://github.com/weswhet/russet/actions/runs/37574104325)
made the reference interpreter available at its required legacy path on the
hosted runner. Both implementations then reached pip and failed opening:

```text
%RECIPE_CACHE_DIR%/relocatable-python/requirements_python3_recommended.txt
```

Both recipe runs exited 70; pip exited 1. Rust took 385.383 seconds and Python
31.016 seconds. These durations reflect this individual run and are not a
performance benchmark.

The recipe sets `REQUIREMENTS_PATH` in `Input`, then injects
`requirements_path: '%REQUIREMENTS_PATH%'`. Python's single-pass substitution
leaves the nested cache token unresolved; Rust preserves that behavior. Engine
regression coverage now checks this ordering, CLI cache precedence, and validation
before cache creation. A concrete `REQUIREMENTS_PATH` is needed for this recipe.
The separate controlled framework test supplies an absolute path and verifies
building, relocating, pip, and HTTPS.

## Native Windows retries

[The Windows 2025 retry](https://github.com/weswhet/russet/actions/runs/37574721494)
ran both untouched Firefox recipes with the runner's native SDK `signtool.exe`
and Chocolatey 2.7.4. Both downloaded the 93,581,312-byte Firefox 157.0.1 MSI,
SHA-256 `19a7e33aaa79b4711c566c53ebf5779fb79efe1ec8adccff2c5cb659aa7fa8ec`.

Both then failed with the native SignTool message:

```text
SignTool Error: The signing certificate does not have a specified SHA1 hash.
```

The inherited recipe still requires certificate SHA-1
`91CABEA509662626E34326687348CAF2DD3B4BBA`; the live download does not satisfy it.
The test preserved that check. No signature bypass was applied, no application
was installed, and no Chocolatey package was produced after the signature failure.
This retry tested Rust against native Windows tools, not against a Python run.
Windows recipe byte hashes differ from the macOS hashes because the checkout
uses CRLF; both correspond to the same pinned recipe commit.

## Controlled validation and final runtime

The final runtime source is
[`1cdfe28055cbbd45d674fda40b7ebd61bf31631d`](https://github.com/weswhet/russet/commit/1cdfe28055cbbd45d674fda40b7ebd61bf31631d).
It includes fixes and regression coverage added after the sweep, including
processor trust records and report handling. The complete 94-recipe sweep has
not been repeated at this later source.

The controlled community suites cover **112 comparisons**: 62 modern-provider,
41 legacy-provider, and nine builder/FileRepo cases. They compare isolated
Python and Rust fixtures; this evidence is separate from the live pass count.
Source capture verifies all 12 promoted manifests, their defaults and source
hashes, and the shared alias while retaining the original 46 manifests.

| Final-source validation | Status at this checkpoint |
|---|---|
| [Four-target builds, tests, lint, installation and rollback](https://github.com/weswhet/russet/actions/runs/37575291895) | Passed on macOS arm64, macOS x86-64, Windows x86-64, and Linux x86-64 |
| [Windows Python differential](https://github.com/weswhet/russet/actions/runs/37575291906) | Passed |
| [macOS/Linux Python differential](https://github.com/weswhet/russet/actions/runs/37575291916) | Passed on macOS arm64 and Linux x86-64, including the relocated framework and native metadata, icon, and mounted-volume lifetime checks |

Live endpoints, release versions, and signing certificates can change. The
recorded recipe hashes, binary hashes, logs, and run IDs identify the tested
conditions; they do not promise that retired or externally broken recipes will
succeed later.
