#!/bin/bash
# Fabrique 9AM9AM-TEAM.app (le logiciel Mac) à la racine du projet.
# Usage : ./app/build.sh          (nécessite Xcode ou les outils en ligne de commande)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
APP="$ROOT/9AM9AM-TEAM.app"
NAME="9AM9AM-TEAM"

if ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "swiftc introuvable : installe Xcode ou lance « xcode-select --install »." >&2
  exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Binaire universel : le même dossier sert sur un Mac Apple Silicon et sur un Mac Intel
# (le projet voyage). Un binaire à une seule architecture échoue sur l'autre.
MIN_MACOS="12.0"   # repris dans Info.plist (LSMinimumSystemVersion)
BIN_TMP="$(mktemp -d)"
trap 'rm -rf "$BIN_TMP"' EXIT

echo "Compilation (arm64 + x86_64)…"
xcrun swiftc -O -swift-version 5 -target arm64-apple-macos$MIN_MACOS \
  -o "$BIN_TMP/$NAME-arm64" app/main.swift -framework Cocoa -framework WebKit
xcrun swiftc -O -swift-version 5 -target x86_64-apple-macos$MIN_MACOS \
  -o "$BIN_TMP/$NAME-x86_64" app/main.swift -framework Cocoa -framework WebKit
lipo -create -output "$APP/Contents/MacOS/$NAME" "$BIN_TMP/$NAME-arm64" "$BIN_TMP/$NAME-x86_64"

# Icône : dessinée par le programme lui-même, puis convertie en .icns (facultative).
ICON_OK=0
TMP="$(mktemp -d)"
if "$APP/Contents/MacOS/$NAME" --render-icon "$TMP/icon.png" 2>/dev/null && [ -s "$TMP/icon.png" ]; then
  mkdir "$TMP/AppIcon.iconset"
  for spec in 16:icon_16x16 32:icon_16x16@2x 32:icon_32x32 64:icon_32x32@2x 128:icon_128x128 \
              256:icon_128x128@2x 256:icon_256x256 512:icon_256x256@2x 512:icon_512x512 1024:icon_512x512@2x; do
    sips -z "${spec%%:*}" "${spec%%:*}" "$TMP/icon.png" --out "$TMP/AppIcon.iconset/${spec##*:}.png" >/dev/null
  done
  if iconutil -c icns "$TMP/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns" 2>/dev/null; then
    ICON_OK=1
  fi
fi
rm -rf "$TMP"
[ "$ICON_OK" = 1 ] || echo "(icône non générée : le logiciel utilisera l'icône par défaut)"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>local.9am9am.team</string>
  <key>CFBundleExecutable</key><string>$NAME</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>ProjectPath</key><string>$ROOT</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST

codesign --force --sign - "$APP" >/dev/null 2>&1 || echo "(signature locale impossible : le logiciel s'ouvrira quand même)"
echo "OK : $APP"
