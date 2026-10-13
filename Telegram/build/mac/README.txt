AyuGram Desktop for macOS
=========================

This build is ad-hoc signed (no Apple Developer ID), so macOS will
refuse to open it until the quarantine attribute is removed. That is
expected and harmless - it only means the file came from the internet.

Easiest way (no scripts)
------------------------

1. Drag AyuGram.app onto the Applications folder icon in this window.
2. Open Terminal (Applications > Utilities > Terminal).
3. Copy and paste this line, then press Enter:

   xattr -cr /Applications/AyuGram.app

4. Open AyuGram from Applications or Spotlight.

Alternative: run Install.command
--------------------------------

Install.command does the same thing automatically. If macOS says it is
"unsafe" or "from an unidentified developer", run it from Terminal
instead of double-clicking:

1. Open Terminal.
2. Drag Install.command into the Terminal window.
3. Press Enter.

If macOS says the app "is damaged and can't be opened"
------------------------------------------------------

That is the same quarantine issue. Run the xattr command above, then
try again. If it still fails, run these two lines in Terminal:

   xattr -cr /Applications/AyuGram.app
   codesign --force --deep --sign - /Applications/AyuGram.app

Why does macOS complain?
------------------------

AyuGram is built and signed locally without an Apple Developer ID, so
it cannot be notarized. Notarization is what normally tells Gatekeeper
a downloaded app is safe. Until we have a paid Apple Developer account,
the xattr step is required on first install.
