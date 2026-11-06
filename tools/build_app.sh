#!/bin/bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
APP="$APP_DIR/Music Cut.app"
if [ ! -d "$APP/Contents/MacOS" ]; then
  echo "缺少 Music Cut.app 骨架。" >&2
  exit 1
fi
clang -O2 -arch arm64 "$APP_DIR/tools/launcher.c" -o "$APP/Contents/MacOS/MusicCut"
chmod +x "$APP/Contents/MacOS/MusicCut"
plutil -replace LSArchitecturePriority -json '["arm64"]' "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP"
echo "Rebuilt $APP"
