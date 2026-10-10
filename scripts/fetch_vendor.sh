#!/usr/bin/env bash
# Fetch the alphaTab distribution (renderer/player JS, Bravura music font, SoundFonts)
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

# Richer SoundFonts for the transport's SoundFont menu (~70 MB total, fetched
# once). MuseScore General is the app default; sonivox above stays as the
# lightweight fallback. Existing files are kept, so re-running is cheap.
fetch_font() {
    local url="$1" name="$2"
    if [ -s "$DEST/soundfont/$name" ]; then
        echo "  soundfont/$name (already present)"
        return
    fi
    echo "  soundfont/$name"
    curl -fSL --progress-bar "$url" -o "$DEST/soundfont/$name.part"
    mv "$DEST/soundfont/$name.part" "$DEST/soundfont/$name"
}
MUSESCORE="https://ftp.osuosl.org/pub/musescore/soundfont/MuseScore_General"
GENERALUSER="https://github.com/mrbumpy409/GeneralUser-GS/raw/main"
fetch_font "$MUSESCORE/MuseScore_General.sf3" MuseScore_General.sf3
fetch_font "$MUSESCORE/MuseScore_General_License.md" MuseScore_General_License.md
fetch_font "$GENERALUSER/GeneralUser-GS.sf2" GeneralUser-GS.sf2
fetch_font "$GENERALUSER/documentation/LICENSE.txt" GeneralUser-GS_LICENSE.txt
echo "Done."
