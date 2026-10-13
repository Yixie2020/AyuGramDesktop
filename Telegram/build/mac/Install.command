#!/bin/bash
# AyuGram Desktop - macOS Install Helper
# Copies AyuGram.app to /Applications, then removes the quarantine
# attribute and re-signs the installed copy so Gatekeeper lets it run.
#
# If macOS refuses to open this file ("unsafe" / "unidentified developer"):
#   1. Open Terminal (Applications > Utilities > Terminal)
#   2. Drag this file into the Terminal window
#   3. Press Enter
# Running from Terminal bypasses the Gatekeeper prompt.
# Or just follow README.txt in the same folder - no script needed.

DEST_PATH="/Applications/AyuGram.app"
SRC_PATH="$(cd "$(dirname "$0")" && pwd)/AyuGram.app"

echo "=== AyuGram Desktop Installer ==="
echo ""

if [ -d "$SRC_PATH" ]; then
    # The DMG volume is read-only, so never touch the copy inside it.
    echo "Installing to /Applications..."
    if [ -d "$DEST_PATH" ]; then
        echo "Existing installation found, replacing..."
        rm -rf "$DEST_PATH"
    fi
    cp -R "$SRC_PATH" "/Applications/"
elif [ -d "$DEST_PATH" ]; then
    echo "No AyuGram.app next to this script, fixing $DEST_PATH in place..."
else
    echo "Error: AyuGram.app not found next to this script or in /Applications"
    read -p "Press Enter to exit..."
    exit 1
fi

# Clear the quarantine attribute on the installed copy (Finder and cp
# both carry it over from the downloaded DMG).
echo "Removing quarantine attribute..."
xattr -cr "$DEST_PATH"

# Re-sign with an ad-hoc signature.
echo "Re-signing application..."
codesign --force --deep --sign - "$DEST_PATH" > /dev/null 2>&1 || true

echo ""
echo "=== Installation Complete ==="
echo ""
echo "AyuGram Desktop has been installed to $DEST_PATH"
echo "You can now open it from Applications or Spotlight."
echo ""
read -p "Press Enter to exit..."
