#!/bin/bash
# Run on the Docker host (not in the container): checks the Intel GPU and prints the docker run /
# compose lines that give A2RM QuickSync without --privileged.
echo "Intel GPU on this host"
if command -v lspci >/dev/null; then
    lspci -nnk 2>/dev/null | grep -A3 -E 'VGA|Display' | sed 's/^/  /'
fi
if ! ls /dev/dri/renderD* >/dev/null 2>&1; then
    echo "  No /dev/dri/renderD* device: no GPU driver loaded on the host (i915 or xe). QuickSync can't work."
    exit 1
fi
ls -ln /dev/dri | sed 's/^/  /'
RENDER=$(ls /dev/dri/renderD* | head -1)
GID=$(stat -c %g "$RENDER")
echo
echo "Give the container the render device and its group (by NUMBER: the name 'render' means a different"
echo "group inside the container). A2RM's startup also adds the arm user to the device's group by itself."
echo
echo "docker run:   --device $RENDER:$RENDER --group-add $GID"
echo "compose:      devices: [\"$RENDER:$RENDER\"]"
echo "              group_add: [\"$GID\"]"
echo
echo "Then in arm.yaml:  HB_ARGS_DVD / HB_ARGS_BD  get  -e qsv_h264 --encoder-preset speed"
echo "And check it:      docker exec <container> a2rm-check"
