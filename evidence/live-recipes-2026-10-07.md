# Live upstream recipe validation — October 7, 2026

All **228 recipes** in [autopkg/recipes at `0f7b61ab061c77710be4140c404910a11f89fc7b`](https://github.com/autopkg/recipes/tree/0f7b61ab061c77710be4140c404910a11f89fc7b) were attempted on GitHub-hosted macOS 15 arm64 runners. After targeted retries, **121 completed successfully, 94 required unsupported custom processors, and 13 remained unsuccessful**. The inventory includes 226 plist recipes and two YAML recipes.

The sweep found one Rust defect affecting two Google Earth recipes. Rust passed a wildcard package path inside a DMG literally to `pkgutil`; Python resolved it first. The fix resolves exactly one matching package while retaining the mount through extraction. Both live recipes passed after the fix, and all nine focused Python/Rust differential cases passed: successful, missing, and ambiguous matches at three verbosity levels.

**The fix is on `main`; the published RustyPkg 1.0.0 downloads have not been replaced with patched binaries.**

The [machine-readable results](live-recipes-2026-10-07.json) provide the per-recipe evidence index. Counts below combine the complete initial sweep with the latest targeted results; they do not represent a second full sweep of the patched binary.

| Final outcome | Recipes | Interpretation |
| --- | ---: | --- |
| Completed successfully | 121 | Rust exited with status 0. Includes one no-op sample recipe, leaving 120 nonempty recipes. |
| Unsupported custom processors | 94 | Rejected under the documented v1 boundary; these are not passes. |
| Other unsuccessful runs | 13 | Vendor responses, missing template inputs or parents, and an unsupported platform operation; details below. |
| Total attempted | 228 | No recipe was omitted from the initial inventory. |

## Runs and revisions

| Evidence | Revision or scope | Result |
| --- | --- | --- |
| [Complete eight-shard sweep](https://github.com/weswhet/rustypkg/actions/runs/37568540781) | `14600322f268eab0e0948b507f5f7879e2993f15` | 113 passed, 94 custom-processor rejections, 19 failed in both implementations, and two Rust failures where Python passed. |
| [Google Earth retry](https://github.com/weswhet/rustypkg/actions/runs/37569360810) | Fix `b330627796b00ea8b8b1e843b26c040e0631d1fd` | Both previously incompatible recipes passed. |
| [Authenticated GitHub retry](https://github.com/weswhet/rustypkg/actions/runs/37569764406) | Harness revision `6d62bf96c87b2e1b821326b541f4895fb3ee1800` | All six recipes retried after GitHub API failures passed with authentication. |
| [Four-platform Rust regression checks](https://github.com/weswhet/rustypkg/actions/runs/37569360855) | Google Earth fix | All four platform jobs passed. |
| [Windows differential regression checks](https://github.com/weswhet/rustypkg/actions/runs/37569360899) | Google Earth fix | Passed. |
| [macOS and Linux differential regression checks](https://github.com/weswhet/rustypkg/actions/runs/37569360902) | Google Earth fix, including new mounted-package glob cases | Passed. |

The Google Earth and authenticated retries used the same Rust binary SHA-256: `aba7c9da7cbd94c88dbd536a37aadb14802f23f3c597a339bd11fd330545fc0d`.

The authenticated retry covered `AutoPkg-Release.download`, `TextMate2.download`, `TextMate2.install`, `munkitools.munki`, `munkitools6-signed.munki`, and `munkitools6.munki`. Its token was supplied through a mode-0600 file outside uploaded evidence. Captured harness commands and processor input logs contain only the token-file path.

## Remaining unsuccessful recipes

These outcomes describe this snapshot and these live responses. A failure in both implementations is not proof that every detail of their behavior is equivalent.

| Recipes | Count | Observed reason |
| --- | ---: | --- |
| `AdobeAIR/AdobeAIR.download.recipe`, `AdobeAIR/AdobeAIR.pkg.recipe`, `AdobeAIR/AdobeAir.munki.recipe` | 3 | Both implementations failed to mount the downloaded DMG response. |
| `Skype/Skype.download.recipe`, `Skype/Skype.pkg.recipe`, `Skype/Skype.munki.recipe`, `Skype/Skype.install.recipe` | 4 | Both implementations failed to mount the downloaded DMG response. |
| `OmniGroup/OmniGroupProduct.download.recipe`, `OmniGroup/OmniGroupProduct.pkg.recipe`, `OmniGroup/OmniGroupProduct.munki.recipe` | 3 | Templates need product-specific `NAME` input; the default feed response failed XML parsing in both implementations. |
| `SassafrasK2Client/SassafrasK2Client.download.recipe` | 1 | Download returned HTTP 404 in both implementations. |
| `Barebones/Yojimbo.install.recipe` | 1 | Parent recipe identifier differs in case from the available identifier; the parent was not found. |
| `AutoPkg/AutoPkgGitMaster.nupkg.recipe` | 1 | Requires a Windows-only operation unavailable on the macOS runner. |

The 94 custom-processor rejections are listed separately in the machine-readable index. No Python custom processors were used to make those recipes pass in Rust.

## Method and limits

- The runner preserved upstream recipe files and recorded the source commit and file hashes. Recipes ran sequentially within each shard, with a 180-second timeout per implementation and process-group termination on timeout.
- Each attempt used separate preferences, cache, temporary files, and Munki FileRepo state. Python retries used separate state from Rust. Cleanup detached disk images backed by files in that attempt's scratch directory.
- Supported recipes that failed in Rust were retried using the pinned AutoPkg source `c36e58f8d3d8ddb70b6c2d848d2ceca7f767ce5c`, the published AutoPkg 3.0.0 Python runtime, and Munki `7.2.0.5787` tools. Successful Rust runs were not all rerun with Python.
- Status 0 means the recipe completed according to its own control flow. It does not establish output equivalence, prove that an installer performed an installation, or exclude recipe-level skips. Reports and logs retain that evidence. `SampleSharedProcessor/SampleSharedProcessor.recipe` is a no-op sample included in the 121 successes.
- Hosted runners had native Rust helpers installed. Although recipe caches and repositories were isolated, installed applications and package receipts could persist between recipes within a shard. The sweep was not a clean-machine test of each individual recipe.
- Downloads were live and independent. Authentication, vendor availability, download contents, and responses can change. The six authenticated retries demonstrate those recipes with authentication, not an anonymous API guarantee.
- This was a macOS arm64 upstream-recipe sweep. The separate regression matrix covered other release platforms; it did not run these 228 recipes on every platform.

## Local Mac smoke tests

Six unchanged download recipes passed on the user's live macOS 27.0 Mac with the published RustyPkg 1.0.0 binary: Google Chrome, Transmit 5, VLC, Cyberduck, Firefox Signed Pkg, and AutoPkg Release. They used isolated scratch state and did not install applications or activate privileged helpers. No local VM was used.

These smoke tests supplement the hosted-runner sweep; they are not six additional recipes beyond the 228-recipe inventory.
