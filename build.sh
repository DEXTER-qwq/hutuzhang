#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
PIP=".venv/bin/pip"

if [ ! -x "$PY" ]; then
  echo "creating venv…"
  python3 -m venv .venv
fi

"$PIP" -q install -r requirements.txt

export PYINSTALLER_CONFIG_DIR="$PWD/.pyinstaller-cache"

.venv/bin/pyinstaller \
  --noconfirm --clean --windowed \
  --name "糊涂账" \
  --icon assets/icon.icns \
  --add-data "app/static:app/static" \
  --collect-submodules uvicorn \
  --collect-submodules webview \
  --osx-bundle-identifier local.hutuzhang.app \
  run.py

APP="dist/糊涂账.app"
PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 1.0.0" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string 1.0.0" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion 1.0.0" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 1.0.0" "$PLIST"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true

echo
echo "done → $APP"
