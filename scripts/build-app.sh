#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
uv sync --frozen
mkdir -p .build
swiftc -O -target arm64-apple-macosx14.0 native/recognize.swift -o .build/recognize
uv run pyinstaller --noconfirm --clean --onedir --name stradale-server \
  --add-data "$PWD/studio/ui:studio/ui" --add-binary "$PWD/.build/recognize:studio" \
  --distpath .build/backend --workpath .build/pyinstaller --specpath .build launcher.py
app="dist/Stradale Studio.app"
# Replace only generated build output.
if [ -d "$app" ]; then rm -rf "$app"; fi
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
swiftc -O -target arm64-apple-macosx14.0 native/App.swift -o "$app/Contents/MacOS/StradaleStudio" -framework AppKit -framework WebKit
cp -R .build/backend/stradale-server "$app/Contents/Resources/backend"
swift scripts/icon.swift .build/AppIcon.iconset
iconutil -c icns .build/AppIcon.iconset -o "$app/Contents/Resources/AppIcon.icns"
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Stradale Studio</string>
<key>CFBundleDisplayName</key><string>Stradale Studio</string>
<key>CFBundleIdentifier</key><string>labs.nightly.stradale-studio</string>
<key>CFBundleExecutable</key><string>StradaleStudio</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.2.0</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundleVersion</key><string>2</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
<key>NSHumanReadableCopyright</key><string>Stradale Studio · Nightly Labs</string>
</dict></plist>
PLIST
codesign --force --deep --sign - "$app"
codesign --verify --deep --strict "$app"
printf '\nBuilt %s\n' "$PWD/$app"
