# Live recipes on Linux: October 7, 2026

Russet ran the **121 recipes** that completed on macOS in the
[October 7 sweep](live-recipes-2026-10-07.md) three ways on the same day:
macOS with Apple's tools, macOS with Russet's native replacements
(`RUSSET_NATIVE=all`), and Linux. In the final run, **every recipe that passed
with Apple's tools also passed with the native replacements**. The only
exceptions were the 12 install recipes on Linux, which need macOS, and one
VLC timeout on each native leg. The 34 packages and disk images built on
Linux all passed Apple's own checks on macOS.

The recipes are from [autopkg/recipes at
`0f7b61ab061c77710be4140c404910a11f89fc7b`](https://github.com/autopkg/recipes/tree/0f7b61ab061c77710be4140c404910a11f89fc7b).
Russet was the `linux-apple-formats` branch at
`6c3be3b05ac5257807dcac8ba53fce09108410cf`
([weswhet/russet#3](https://github.com/weswhet/russet/pull/3)). The
[machine-readable results](linux-live-recipes-2026-10-07.json) list every
recipe's outcome on each leg.

## Final run

[Run 37719294313](https://github.com/weswhet/russet-compat/actions/runs/37719294313)
on GitHub-hosted runners: `macos-15` for both macOS legs and `ubuntu-24.04`
for Linux.

| Outcome | macOS, Apple tools | macOS, native | Linux |
| --- | ---: | ---: | ---: |
| Passed | 121 | 120 | 108 |
| Timeout | 0 | 1 | 1 |
| Needs macOS (install recipes) | 0 | 0 | 12 |

- **Install recipes:** the 12 on Linux stop at `Installer` or
  `InstallFromDMG`, which Russet supports only on macOS.
- **VLC timeouts:** `VLC.download` timed out on the native macOS leg and
  `VLC.install` on Linux, within the 300-second limit per recipe. Each covers
  a 95 MB download, extracting the disk image, and a deep check of the app's
  signature. Locally, the native check of `VLC.app` took 20 seconds and
  Apple's `codesign` 22 seconds. The workflow's default limit is now 600
  seconds.
- **Linux-built outputs:** 30 packages and 4 disk images. On macOS,
  `pkgutil --payload-files`, `lsbom`, and `installer -pkginfo` read every
  package fully, and `hdiutil verify` and `hdiutil attach` accepted every
  image.

## Earlier runs and what they found

The first two runs found compatibility gaps. Every one also failed on macOS
with `RUSSET_NATIVE=all`, so each was reproduced and fixed on a Mac.

| Run | Linux passed | macOS native passed | Fixed afterward |
| --- | ---: | ---: | --- |
| [37713075204](https://github.com/weswhet/russet-compat/actions/runs/37713075204) | 47 | 51 | Finder aliases to folders read as HFS+ directory hard links (OmniGroup, Praat); multi-stream bzip2 tar archives (TextMate); bundles without an executable (Adium); non-Mach-O files signed with extended attributes (VLC); nested code with an unlisted extension (Transmit); `certificate root = H"..."` requirements and case-insensitive paths (Cyberduck); large extended attributes on Linux. |
| [37716394267](https://github.com/weswhet/russet-compat/actions/runs/37716394267) | 99 | 111 | CMS signed attributes that aren't in DER order (OmniPlan 3); Finder info on folders inside a bundle (OmniOutliner 4); seal entries recorded as a bare SHA-1 hash (Transmit 4). |
| [37719294313](https://github.com/weswhet/russet-compat/actions/runs/37719294313) | 108 | 120 | None. |

## Limitations

- This covers the recipes that completed on macOS on October 7. Recipes that
  need custom processors, or that failed with Apple's tools too, weren't run.
- Downloads came from live vendor servers, so another day can fetch different
  versions.
- Packages, disk images, and icons Russet builds aren't byte-identical to
  Apple's, so their hashes differ from the macOS run's.
