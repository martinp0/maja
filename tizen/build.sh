#!/bin/sh
# Sestaví tizen/package (složka pro `aerotizen install`) a tizen/dist/Maja.wgt.
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$root/web"
npm ci --no-audit --no-fund
npx vite build --config vite.tv.config.ts
cd "$root"
if [ -f tizen/package/tv.html ]; then
  mv tizen/package/tv.html tizen/package/index.html
fi
cp tizen/config.xml tizen/package/config.xml
cp web/public/pwa-192.png tizen/package/icon.png
mkdir -p tizen/dist
rm -f tizen/dist/Maja.wgt
(cd tizen/package && zip -r -X ../dist/Maja.wgt .)
echo "tizen/package  and  tizen/dist/Maja.wgt"
