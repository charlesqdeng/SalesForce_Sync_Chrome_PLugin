#!/usr/bin/env bash
# Builds "Salesforce Sheets.app" in the project root. Double-clicking the app
# opens Terminal and runs start.sh. Rerun after changing make_icon.swift.
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$APP_DIR")"
APP="$ROOT/Salesforce Sheets.app"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Drawing icon..."
swift "$APP_DIR/make_icon.swift" "$WORK/icon.png"
ICONSET="$WORK/AppIcon.iconset"
mkdir "$ICONSET"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$WORK/icon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" "$WORK/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done

echo "Assembling $APP..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Salesforce Sheets</string>
    <key>CFBundleDisplayName</key>
    <string>Salesforce Sheets</string>
    <key>CFBundleIdentifier</key>
    <string>local.salesforce-sheets.launcher</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/launcher" <<'LAUNCHER'
#!/usr/bin/env bash
# The app lives in the project root, three levels above this script.
ROOT="$(cd -- "$(dirname -- "$0")/../../.." && pwd)"
open -a Terminal "$ROOT/start.sh"
LAUNCHER
chmod +x "$APP/Contents/MacOS/launcher"

# Make Finder pick up the new icon.
touch "$APP"
echo "Done. Double-click \"Salesforce Sheets.app\" to launch."
