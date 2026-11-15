#!/bin/bash
# a2rm-check: is MakeMKV usable, and does Intel QuickSync really work for ARM's user?
# Run inside the container:  docker exec arm-rippers a2rm-check
# Exit code: 0 = all good, 1 = something to fix (MakeMKV expired, or QSV set in arm.yaml but broken).
#
# "qsv: is available on this system" from HandBrake means nothing (it's printed even without a GPU),
# so this runs a real 5-second test encode as the arm user.

CONFIG=${ARM_CONFIG_FILE:-/etc/arm/config/arm.yaml}
RUN_AS=arm
PROBLEMS=0
ok()   { echo "  [OK]   $*"; }
warn() { echo "  [WARN] $*"; }
bad()  { echo "  [FAIL] $*"; PROBLEMS=1; }
as_arm() { if [ "$(id -u)" = 0 ]; then su -s /bin/bash "$RUN_AS" -c "$*"; else bash -c "$*"; fi; }

echo "MakeMKV"
MKV=$(makemkvcon -r --cache=1 info disc:9999 2>&1)
VERSION=$(echo "$MKV" | grep -o 'MakeMKV v[0-9.]*[0-9]' | head -1)
if echo "$MKV" | grep -q '^MSG:5021,'; then
    bad "${VERSION:-MakeMKV} has expired (\"too old\"). Rebuild the image with the current version: scripts/update-makemkv.sh"
elif [ -n "$VERSION" ]; then
    ok "$VERSION"
else
    bad "makemkvcon didn't run: $(echo "$MKV" | head -1)"
fi

echo "Intel QuickSync"
QSV_WANTED=0
grep -E '^HB_ARGS_(DVD|BD):.*qsv_' "$CONFIG" >/dev/null 2>&1 && QSV_WANTED=1
RENDER=$(ls /dev/dri/renderD* 2>/dev/null | head -1)
if [ -z "$RENDER" ]; then
    if [ $QSV_WANTED = 1 ]; then
        bad "arm.yaml asks for a qsv encoder but there's no /dev/dri/renderD* in the container. Add --device /dev/dri/renderD128"
    else
        echo "  No GPU passed in (/dev/dri); HandBrake uses the CPU. That's fine if you don't want QuickSync."
    fi
else
    if as_arm "test -r $RENDER -a -w $RENDER"; then
        ok "$RENDER is readable/writable by $RUN_AS"
    else
        bad "$RUN_AS can't open $RENDER (device group $(stat -c %g "$RENDER")). Restart the container (startup adds the group) or use --group-add $(stat -c %g "$RENDER")"
    fi
    if command -v vainfo >/dev/null; then
        VA=$(as_arm "vainfo --display drm --device $RENDER 2>&1")
        DRIVER=$(echo "$VA" | grep -o 'Driver version: .*' | head -1)
        if echo "$VA" | grep -q 'VAEntrypointEncSlice'; then
            ok "VA-API: ${DRIVER:-driver loaded}"
        else
            bad "VA-API driver doesn't load: $(echo "$VA" | grep -iE 'error|fail' | head -1)"
            [ -n "${LIBVA_DRIVER_NAME:-}" ] && warn "LIBVA_DRIVER_NAME=$LIBVA_DRIVER_NAME is set; on Gen12+ it must be iHD (or unset)"
        fi
    else
        bad "No Intel media driver tools in this image (vainfo missing). Build with --build-arg INTEL_QSV=1"
    fi
    if ! HandBrakeCLI --help 2>/dev/null | grep -q 'qsv_h264'; then
        bad "This HandBrake has no qsv_h264 encoder. Build with --build-arg INTEL_QSV=1"
    else
        TMP=$(as_arm "mktemp -d")
        as_arm "ffmpeg -v error -f lavfi -i testsrc2=size=720x480:rate=30 -t 5 -c:v mpeg2video -q:v 4 $TMP/in.mkv"
        START=$(date +%s%N)
        as_arm "HandBrakeCLI -i $TMP/in.mkv -o $TMP/out.mkv -e qsv_h264 --encoder-preset speed > $TMP/hb.log 2>&1"
        RC=$?
        SECONDS_TAKEN=$(awk -v ns=$(( $(date +%s%N) - START )) 'BEGIN { printf "%.1f", ns / 1e9 }')
        if [ $RC = 0 ] && grep -qE "qsv_enc_init: using 'hardware|encoder: H.264 \(Intel QSV\)" "$TMP/hb.log"; then
            ok "Test encode with qsv_h264 on the GPU ($(HandBrakeCLI --version 2>/dev/null | head -1), ${SECONDS_TAKEN:-?} s)"
        else
            REASON=$(grep -E 'libva: This version|failed to create hwdevice|qsv is not available|MFXVideoCORE|invalid pointer|ERROR' "$TMP/hb.log" | head -2)
            bad "Test encode with qsv_h264 failed (exit $RC): ${REASON:-see $TMP/hb.log}"
            echo "$REASON" | grep -q 'VA-API 1.15' && echo "         HandBrake was compiled against an old libva. Rebuild with --build-arg INTEL_QSV=1"
        fi
        rm -rf "$TMP"
    fi
    if [ $QSV_WANTED = 0 ]; then
        warn "A GPU is passed in but HB_ARGS_DVD/HB_ARGS_BD in arm.yaml have no '-e qsv_h264', so HandBrake uses the CPU"
    fi
fi
exit $PROBLEMS
