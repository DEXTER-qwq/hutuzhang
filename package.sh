#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

VERSION="1.0.0"
APP="dist/糊涂账.app"
ZIP="../hutuzhang-${VERSION}-macos-arm64.zip"

bash build.sh
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
echo
echo "release → $ZIP"
ls -lh "$ZIP"
