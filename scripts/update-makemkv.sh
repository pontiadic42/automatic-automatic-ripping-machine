#!/bin/bash
# Rebuild the A2RM image with the current MakeMKV (MakeMKV's free beta expires with each new release).
# Run on the Docker host from the repository folder:
#   scripts/update-makemkv.sh [image tag] [extra docker build args...]
#   e.g. scripts/update-makemkv.sh a2rm:latest --build-arg INTEL_QSV=1
# It only builds the image; recreate your container afterwards (when nothing is ripping).
set -euo pipefail
TAG="${1:-a2rm:latest}"
shift || true
VERSION=$(curl -fsSL https://www.makemkv.com/download/ | grep -o 'makemkv-sha-[0-9.]*[0-9]\.txt' | head -1 \
          | sed 's/makemkv-sha-//; s/\.txt$//')
[ -n "$VERSION" ] || { echo "Couldn't find the current MakeMKV version on makemkv.com" >&2; exit 1; }
CURRENT=$(docker run --rm --entrypoint makemkvcon "$TAG" -r --cache=1 info disc:9999 2>/dev/null \
          | grep -o 'MakeMKV v[0-9.]*[0-9]' | head -1 | sed 's/MakeMKV v//' || true)
echo "Current MakeMKV: $VERSION. In $TAG: ${CURRENT:-not built yet}"
if [ "$CURRENT" = "$VERSION" ]; then
    echo "Nothing to do."
    exit 0
fi
cd "$(dirname "$0")/.."
docker build --build-arg MAKEMKV_VERSION="$VERSION" "$@" -t "$TAG" .
echo "Built $TAG with MakeMKV $VERSION. Recreate the container to use it (when no disc is ripping)."
