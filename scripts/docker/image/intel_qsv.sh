#!/bin/bash
# Intel QuickSync (QSV) support (used by the Dockerfile with --build-arg INTEL_QSV=1).
#
# What's needed, from testing on a 12th-gen Intel (UHD 770), see docs/quicksync.md:
#  - Intel's media driver (iHD) and oneVPL GPU runtime. Ubuntu 22.04's are too old for HandBrake,
#    so they come from Intel's package repository for Ubuntu 22.04 ("jammy").
#  - HandBrake compiled against that newer libva: HandBrake 1.10+ needs VA-API >= 1.15 at compile
#    time, otherwise it says "QSV available" and then fails with "hwaccel: failed to create hwdevice".
#    (Installing a newer libva afterwards doesn't help; the check is compiled in.)
# No source builds of libva / media-driver / oneVPL are needed.
#
# Usage: intel_qsv.sh HANDBRAKE_VERSION
set -euo pipefail
HANDBRAKE_VERSION="${1:?HandBrake version}"
export DEBIAN_FRONTEND=noninteractive

if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then
    echo "Intel QSV is only set up on amd64; skipping."
    exit 0
fi

echo "Adding Intel's GPU package repository (Ubuntu 22.04)"
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gpg
curl -fsSL https://repositories.intel.com/gpu/intel-graphics.key | gpg --dearmor -o /usr/share/keyrings/intel-graphics.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/intel-graphics.gpg] https://repositories.intel.com/gpu/ubuntu jammy client" \
    > /etc/apt/sources.list.d/intel-gpu-jammy.list
apt-get update

# Ubuntu's runtime package (libmfx-gen1.2) clashes with Intel's (libmfxgen1): remove it first
apt-get remove -y libmfx-gen1.2 >/dev/null 2>&1 || true
apt-get install -y --no-install-recommends \
    intel-media-va-driver-non-free libmfxgen1 libvpl2 libva2 libva-drm2 libva-x11-2 libva-wayland2 libva-dev vainfo

echo "Building HandBrake $HANDBRAKE_VERSION against libva $(pkg-config --modversion libva)"
sed -i "s|^HANDBRAKE_VERSION=.*|HANDBRAKE_VERSION=${HANDBRAKE_VERSION}|" /install_handbrake.sh
grep -q "^HANDBRAKE_VERSION=${HANDBRAKE_VERSION}\$" /install_handbrake.sh
bash /install_handbrake.sh
rm -rf /var/lib/apt/lists/*

HandBrakeCLI --version 2>/dev/null | grep -q "HandBrake ${HANDBRAKE_VERSION}"
echo "Intel QSV support installed: libva $(pkg-config --modversion libva), HandBrake ${HANDBRAKE_VERSION}"
