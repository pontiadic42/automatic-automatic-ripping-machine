#!/bin/bash
# Install a current MakeMKV into the image (used by the Dockerfile).
#
# The ARM base image (arm-dependencies) was built with whatever MakeMKV was current then, and
# MakeMKV's free beta stops working when a newer release comes out ("This application version
# is too old", exit 253). So the image installs MakeMKV itself.
#
# Usage: makemkv.sh [VERSION|latest]   (default: latest, looked up on makemkv.com)
set -euo pipefail

WANTED="${1:-latest}"
if [[ "$WANTED" == "latest" || -z "$WANTED" ]]; then
    WANTED=$(curl -fsSL https://www.makemkv.com/download/ | grep -o 'makemkv-sha-[0-9.]*[0-9]\.txt' | head -1 \
             | sed 's/makemkv-sha-//; s/\.txt$//')
    if [[ -z "$WANTED" ]]; then
        echo "ERROR: couldn't find the current MakeMKV version on makemkv.com." >&2
        echo "Pass one instead, e.g. --build-arg MAKEMKV_VERSION=2.0.0" >&2
        exit 1
    fi
    echo "Current MakeMKV release: $WANTED"
fi

installed() {
    makemkvcon -r --cache=1 info disc:9999 2>/dev/null | grep -o 'MakeMKV v[0-9.]*[0-9]' | head -1 | sed 's/MakeMKV v//' || true
}

HAVE=$(installed)
if [[ "$HAVE" == "$WANTED" ]]; then
    echo "MakeMKV $WANTED is already installed."
    exit 0
fi
echo "Installing MakeMKV $WANTED (image has ${HAVE:-none})"

# The base image's installer looks the version up by itself; give it ours instead
WORK=$(mktemp -d)
sed "s|^    MAKEMKV_VERSION=\$(curl.*|    MAKEMKV_VERSION=${WANTED}|" /install_makemkv.sh > "$WORK/install.sh"
grep -q "MAKEMKV_VERSION=${WANTED}\$" "$WORK/install.sh" || { echo "ERROR: /install_makemkv.sh has changed; can't set the version" >&2; exit 1; }
(cd "$WORK" && bash install.sh)
rm -rf "$WORK"

HAVE=$(installed)
if [[ "$HAVE" != "$WANTED" ]]; then
    echo "ERROR: MakeMKV reports '${HAVE:-nothing}' after installing $WANTED" >&2
    exit 1
fi
if makemkvcon -r --cache=1 info disc:9999 2>/dev/null | grep -q '^MSG:5021,'; then
    echo "ERROR: MakeMKV $WANTED says it's too old (a newer release exists?)" >&2
    exit 1
fi
echo "MakeMKV $WANTED installed."
