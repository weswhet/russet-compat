#!/usr/bin/env bash
# Fetch Python AutoPkg at the pinned upstream commit into reference/, or into
# $AUTOPKG_REFERENCE_SOURCE. The folder holds a bare repository with only that
# commit, the extracted Code tree, requirements.txt, and a COMMIT marker.
set -euo pipefail

commit=c36e58f8d3d8ddb70b6c2d848d2ceca7f767ce5c
url=https://github.com/autopkg/autopkg.git
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
destination="${AUTOPKG_REFERENCE_SOURCE:-$root/reference}"

# Only replace a previous reference folder or an empty one.
if [ -e "$destination" ] && [ ! -f "$destination/COMMIT" ] && [ -n "$(ls -A "$destination")" ]; then
	echo "$destination exists and isn't a reference folder; remove it or set AUTOPKG_REFERENCE_SOURCE." >&2
	exit 1
fi
rm -rf "$destination"
mkdir -p "$destination"
git -c init.defaultBranch=main init --quiet --bare "$destination/git"
git --git-dir="$destination/git" fetch --quiet --no-tags --depth=1 "$url" "$commit"
test "$(git --git-dir="$destination/git" rev-parse "$commit^{commit}")" = "$commit"
# Extract with this platform's Git settings, as the suites always have; the
# suites read exact committed bytes from the bare repository.
git --git-dir="$destination/git" archive "$commit" Code requirements.txt | (cd "$destination" && tar -x)
printf '%s\n' "$commit" >"$destination/COMMIT"
echo "Fetched Python AutoPkg $commit into $destination"
