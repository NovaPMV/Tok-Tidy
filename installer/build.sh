#!/usr/bin/env bash
# Builds dist/TokTidy-Setup-<version>.exe. Needs NSIS 3 (Ubuntu: sudo apt install nsis; macOS: brew install makensis).
# Optional: REPO_URL=https://github.com/you/TokTidy ./installer/build.sh
set -euo pipefail
cd "$(dirname "$0")"
VERSION=$(python3 -c "import json;print(json.load(open('../app/package.json'))['version'])")
RCEDIT_URL="https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe"
RCEDIT_SHA256="3e7801db1a5edbec91b49a24a094aad776cb4515488ea5a4ca2289c400eade2a"
mkdir -p build ../dist
if [ ! -f build/rcedit-x64.exe ]; then
  echo "Downloading rcedit ..."
  curl -fsSL -o build/rcedit-x64.exe "$RCEDIT_URL"
fi
echo "$RCEDIT_SHA256  build/rcedit-x64.exe" | sha256sum -c -
ARGS=(-V2 "-DVERSION=$VERSION")
if [ -n "${REPO_URL:-}" ]; then ARGS+=("-DREPO_URL=$REPO_URL"); fi
makensis "${ARGS[@]}" TokTidy.nsi
( cd ../dist && sha256sum "TokTidy-Setup-$VERSION.exe" > "TokTidy-Setup-$VERSION.exe.sha256" )
echo "Built dist/TokTidy-Setup-$VERSION.exe"
