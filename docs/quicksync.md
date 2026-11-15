# Intel QuickSync (QSV) with A2RM

QuickSync lets HandBrake encode on the Intel GPU instead of the CPU. On an i7-12700T (UHD 770), DVDs went from
about 100 fps (ARM's default x264 "HQ" preset) to about 1,000 fps, with the CPU much cooler.

Tested on a 12th-gen Intel (Alder Lake, UHD 770, Linux `i915` driver) with Debian 13 as the Docker host. Other
generations should work the same way but haven't been tested; notes at the end.

## 1. Check the host

Nothing needs installing on the host. Run:

```bash
scripts/a2rm-host-check.sh
```

It shows the GPU and its kernel driver (`i915`, or `xe` on newer GPUs), the `/dev/dri` devices, and the exact
`docker run` / compose lines for your machine. If there's no `/dev/dri/renderD128`, the host has no GPU driver
loaded and QuickSync can't work.

## 2. Build the image with QSV

```bash
docker build --build-arg INTEL_QSV=1 -t a2rm:latest .
```

This adds Intel's media driver (iHD) and oneVPL GPU runtime from Intel's package repository, then compiles
HandBrake against them (about 10 minutes). `HANDBRAKE_VERSION` (default 1.11.2) picks the HandBrake version.

Why HandBrake is compiled here: HandBrake 1.10 and newer use FFmpeg's QSV code, which needs libva with VA-API 1.15
or newer **when HandBrake is compiled**. ARM's base image is Ubuntu 22.04 with libva 2.14 (VA-API 1.14), so a
stock HandBrake says "QSV available" and then fails with `hwaccel: failed to create hwdevice`. Installing a newer
libva afterwards doesn't help.

## 3. Run the container

Give it the render device and the device's group **by number** (inside the container the name `render` is a
different group):

```bash
docker run ... --device /dev/dri/renderD128:/dev/dri/renderD128 --group-add 992 ...
```

```yaml
devices:
  - /dev/dri/renderD128:/dev/dri/renderD128
group_add:
  - "992"      # the gid of /dev/dri/renderD128 on the host (ls -ln /dev/dri)
```

`card0` isn't needed, and QuickSync doesn't need `--privileged`. A2RM's startup also adds the `arm` user to the
device's group by itself, so `--group-add` is a second safety net. (ARM's own Docker guide uses `--privileged` for
the optical drives; whether ripping works without it hasn't been tested here.)

## 4. Tell ARM to use it

In `arm.yaml`, add the encoder to HandBrake's arguments:

```yaml
HB_ARGS_DVD: "--subtitle scan -F -e qsv_h264 --encoder-preset speed"
HB_ARGS_BD: "--subtitle scan -F --subtitle-burned --audio-lang-list eng --all-audio -e qsv_h264 --encoder-preset speed"
```

`qsv_h265` makes files about 25-35% smaller at about the same speed. With HandBrake 1.9.2,
`--enable-qsv-decoding` nearly doubles Blu-ray speed; it doesn't work with HandBrake 1.11.2.

**Important:** if QSV is set here but broken, HandBrake doesn't fall back to the CPU. The job fails at the
transcode step, after the whole rip. Check first:

## 5. Check it

```bash
docker exec <container> a2rm-check
```

It checks MakeMKV (installed version, expired or not), that the `arm` user can open the render device, that the
VA-API driver loads, and runs a 5-second test encode with `qsv_h264` as the `arm` user. Exit code 0 means all good.

On a real job, the HandBrake part of the job log shows `encoder: H.264 (Intel QSV)` (HandBrake 1.10+) or
`qsv_enc_init: using 'hardware (1) via VAAPI'` (1.9.x), and a DVD encodes at around 1,000 fps.

## Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| `qsv: is available on this system` / "Intel QuickSync supported!" | **Means nothing**: printed even with no GPU or driver | Run `a2rm-check` |
| `MFXVideoCORE_QueryPlatform failed` then `qsv is not available on the system`, exit 3 | No render device, or the `arm` user can't open it | Pass `/dev/dri/renderD128`; restart the container or add `--group-add <gid>` |
| `vainfo: error: failed to initialize display` | Same | Same |
| `va_openDriver() returns -1` | Wrong `LIBVA_DRIVER_NAME` (e.g. `i965` on Gen12) or no iHD driver | Unset it (or set `iHD`); build with `INTEL_QSV=1` |
| `hwaccel: failed to create hwdevice` + `libva: This version of libva doesn't support ... VA-API 1.15.0` | HandBrake compiled against an old libva | Build with `INTEL_QSV=1` |
| `invalid pointer qsv_data->qsv_info=(nil)` | No QSV driver/runtime in the image | Build with `INTEL_QSV=1` |
| `undefined symbol: vaMapBuffer2` | HandBrake built against a newer libva than installed | Same libva at build and run time |
| `trying to overwrite ... enctools.so ... libmfx-gen1.2` (apt) | Ubuntu's and Intel's runtime packages mixed | Remove `libmfx-gen1.2` first (the build script does) |
| Job log says `x264` / `libx264` | `HB_ARGS_*` has no `-e qsv_h264` | Step 4 |
| `Cannot load libnvidia-encode.so.1`, `can't connect to X server` | Harmless | Ignore |

FFmpeg working doesn't prove HandBrake works (they use different code paths); test HandBrake itself.

To watch the GPU while encoding: `intel_gpu_top` on the host (needs root), "Video" engine above 0%.

## What the pieces are

- **VA-API** (libva + the iHD driver) talks to the GPU.
- **oneVPL** (dispatcher `libvpl` + GPU runtime `libmfx-gen`) is Intel's encode API on top of it. It replaced the
  old Media SDK (`libmfx1`), which doesn't support 12th-gen and newer GPUs.
- "QSV" in HandBrake and FFmpeg means oneVPL.

## Other Intel generations (not tested)

- AV1 hardware encoding needs Arc or Meteor Lake and newer; 12th gen can only decode AV1.
- 10-bit HEVC encoding from 7th gen (Kaby Lake).
- GPUs older than Broadwell need the old `i965` driver, which this build doesn't install.
