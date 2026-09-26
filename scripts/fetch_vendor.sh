#!/usr/bin/env bash
# Fetch the alphaTab distribution (renderer/player JS, Bravura music font, SoundFont)
# into static/vendor/alphatab/ so the app is fully self-hosted and works offline.
set -euo pipefail

VERSION="${1:-1.8.4}"
BASE="https://cdn.jsdelivr.net/npm/@coderline/alphatab@${VERSION}/dist"
DEST="$(cd "$(dirname "$0")/.." && pwd)/static/vendor/alphatab"

mkdir -p "$DEST/font" "$DEST/soundfont"

fetch() {
    echo "  $1"
    curl -fsSL "$BASE/$1" -o "$DEST/$1"
}

echo "Fetching alphaTab ${VERSION} into $DEST"
fetch alphaTab.min.js
for f in Bravura.woff2 Bravura.woff Bravura.otf Bravura.svg Bravura.eot Bravura-OFL.txt; do
    fetch "font/$f"
done
fetch soundfont/sonivox.sf2
fetch soundfont/LICENSE
echo "Done."
