#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

VERSION="$(python3 -c 'from app.version import __version__; print(__version__)')"
APP="dist/糊涂账.app"
ZIP="../hutuzhang-${VERSION}-macos-arm64.zip"

bash build.sh
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
echo
echo "release → $ZIP"
ls -lh "$ZIP"
