"""
Settings > Automation > Hardware check: what the container sees of the GPU, whether ARM's
user can use it, what VA-API and HandBrake offer, a real test encode, and encoder
suggestions for this GPU (same checks as scripts/docker/a2rm-check.sh, in the web UI).

Also the optional hardware decoding for Blu-ray transcodes (HW_DECODE_BLURAY): only added
to HandBrake's options after a decode test on this machine passed.
"""
import glob
import json
import logging
import os
import re
import subprocess
import tempfile
import time

import arm.config.config as cfg
from arm.automation import hwcheck, settings

PCI_VENDORS = {"0x8086": "Intel", "0x10de": "NVIDIA", "0x1002": "AMD"}
ENCODER_NAMES = re.compile(r"^\s{20,}([A-Za-z0-9_]+)\s*$")
VA_ENCODE = re.compile(r"VAProfile(\w+?)\s*:\s*VAEntrypointEnc(Slice|SliceLP|Picture)")
DECODE_CACHE_SECONDS = 7 * 24 * 3600


def _run(command, timeout=20):
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=timeout)
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"{command[0]} didn't answer within {timeout} s"
    except OSError as error:
        return -1, str(error)


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def gpus():
    """GPUs the container sees in /sys/class/drm: vendor, PCI id, kernel driver"""
    found = []
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        if "-" in os.path.basename(card):
            continue  # connectors such as card0-HDMI-A-1
        vendor, device = _read(f"{card}/device/vendor"), _read(f"{card}/device/device")
        driver = os.path.basename(os.path.realpath(f"{card}/device/driver")) if os.path.exists(
            f"{card}/device/driver") else ""
        found.append({"card": os.path.basename(card), "vendor": PCI_VENDORS.get(vendor, vendor or "unknown"),
                      "pci_id": f"{vendor[2:]}:{device[2:]}" if vendor and device else "", "driver": driver})
    return found


def render_nodes():
    """Each /dev/dri/renderD* with its group id and whether this process (ARM's user) can open it"""
    nodes = []
    for path in sorted(glob.glob("/dev/dri/renderD*")):
        try:
            gid = os.stat(path).st_gid
        except OSError:
            gid = None
        usable = os.access(path, os.R_OK | os.W_OK)
        nodes.append({"path": path, "gid": gid, "usable": usable,
                      "in_group": gid in os.getgroups() if gid is not None else False,
                      "fix": "" if usable else (f"Give the arm user group {gid}: restart the container (A2RM's "
                                                f"startup adds it) or add --group-add {gid}")})
    return nodes


def vainfo(node):
    """Driver version and what the GPU can encode, from vainfo"""
    code, output = _run(["vainfo", "--display", "drm", "--device", node])
    driver = re.search(r"Driver version: (.*)", output)
    encodes = sorted({match.group(1) for match in VA_ENCODE.finditer(output)})
    codecs = {"H.264": any(p.startswith("H264") for p in encodes), "HEVC": any(p.startswith("HEVC") for p in encodes),
              "AV1": any(p.startswith("AV1") for p in encodes)}
    error = "" if code == 0 else next((line for line in output.splitlines() if re.search("error|fail", line, re.I)),
                                      output.strip()[:200])
    return {"ok": code == 0 and bool(encodes), "driver": driver.group(1).strip() if driver else "",
            "encode_profiles": encodes, "codecs": codecs, "error": error}


def handbrake_info():
    """HandBrake's version and its video encoders"""
    _code, version = _run([hwcheck.handbrake(), "--version"])
    match = re.search(r"HandBrake (\d+)\.(\d+)\.(\d+)", version)
    _code, help_text = _run([hwcheck.handbrake(), "--help"], timeout=30)
    section = help_text.split("--encoder <string>", 1)[-1].split("--encoder-preset", 1)[0]
    encoders = [m.group(1) for m in (ENCODER_NAMES.match(line) for line in section.splitlines()) if m]
    return {"version": match.group(0) if match else (version.strip().splitlines() or [""])[0],
            "version_tuple": tuple(int(part) for part in match.groups()) if match else None,
            "encoders": encoders}


def hb_args_status():
    """What HB_ARGS_DVD / HB_ARGS_BD ask for"""
    status = {}
    for key in ("HB_ARGS_DVD", "HB_ARGS_BD"):
        encoder = hwcheck.encoder_in(cfg.arm_config.get(key, ""))
        status[key] = {"args": str(cfg.arm_config.get(key, "") or ""), "encoder": encoder or "preset default (CPU)",
                       "hardware": hwcheck.is_hardware(encoder)}
    return status


def decode_flag(version_tuple):
    """HandBrake's QSV decoding option: --enable-qsv-decoding up to 1.9, --enable-hw-decoding qsv from 1.10"""
    if version_tuple and version_tuple[:2] < (1, 10):
        return "--enable-qsv-decoding"
    return "--enable-hw-decoding qsv"


def suggestions(report):
    """Encoder presets that suit this machine (shown, never applied automatically)"""
    encoders = set(report["handbrake"]["encoders"])
    codecs = (report.get("vainfo") or {}).get("codecs") or {}
    items = []
    if "qsv_h264" in encoders and codecs.get("H.264"):
        items.append({"title": "Intel QSV H.264 (fast, most compatible)",
                      "args": "-e qsv_h264 --encoder-preset speed",
                      "note": "DVDs ~1,000 fps on a UHD 770, about 11x ARM's default x264 preset."})
    if "qsv_h265" in encoders and codecs.get("HEVC"):
        items.append({"title": "Intel QSV H.265/HEVC (smaller files)",
                      "args": "-e qsv_h265 --encoder-preset speed",
                      "note": "Measured on m80q: 25-35% smaller than qsv_h264, 7-20% slower. Older TVs and "
                              "browsers may not play HEVC."})
    if "qsv_av1" in encoders and codecs.get("AV1"):
        items.append({"title": "Intel QSV AV1", "args": "-e qsv_av1 --encoder-preset speed",
                      "note": "Arc / Meteor Lake and newer only. Smallest files; needs AV1-capable players."})
    elif codecs and not codecs.get("AV1"):
        items.append({"title": "AV1", "args": "",
                      "note": "This GPU can't encode AV1 (vainfo shows no AV1 encode entrypoint)."})
    if any(e.startswith("nvenc_") for e in encoders):
        items.append({"title": "NVIDIA NVENC H.265", "args": "-e nvenc_h265", "note": "Needs --gpus all."})
    items.append({"title": "Keep Comb Detect + Decomb", "args": "",
                  "note": "Removing them sped Blu-rays up a lot in testing, but they're needed for interlaced "
                          "discs (most DVDs, some 1080i Blu-rays), so A2RM doesn't offer to drop them."})
    return items


def report():
    """Everything the Hardware check page shows except the test encode"""
    nodes = render_nodes()
    info = {"gpus": gpus(), "nodes": nodes, "handbrake": handbrake_info(), "hb_args": hb_args_status(),
            "vainfo": vainfo(nodes[0]["path"]) if nodes else None, "uid": os.getuid(), "groups": os.getgroups()}
    info["suggestions"] = suggestions(info)
    info["decode_flag"] = decode_flag(info["handbrake"]["version_tuple"])
    wanted = [s["encoder"] for s in info["hb_args"].values() if s["hardware"]]
    info["test_encoder"] = wanted[0] if wanted else ("qsv_h264" if "qsv_h264" in info["handbrake"]["encoders"]
                                                     and nodes else None)
    info["last_test"] = hwcheck.last_result(info["test_encoder"]) if info["test_encoder"] else None
    info["last_decode"] = last_decode_test()
    return info


# --- Hardware decoding for Blu-rays (C23) -------------------------------------------------------

def _decode_cache():
    return os.path.join(os.path.dirname(str(cfg.arm_config.get("DBFILE") or "/tmp/arm.db")), "a2rm-hwdecode.json")


def last_decode_test():
    try:
        with open(_decode_cache(), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def decode_test(timeout=120):
    """
    Transcode a 3-second 1080p H.264 clip with QSV decoding + qsv_h264, as ARM's user.
    Passes when HandBrake exits 0, writes a file and logs no hardware-device errors.
    """
    version = handbrake_info()["version_tuple"]
    flag = decode_flag(version)
    result = {"ok": False, "flag": flag, "checked": time.time(), "cause": "", "log": ""}
    with tempfile.TemporaryDirectory(prefix="a2rm-hwdecode-") as tmp:
        source, target = os.path.join(tmp, "in.mkv"), os.path.join(tmp, "out.mkv")
        code, output = _run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24",
                             "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", source], timeout=60)
        if code != 0:
            result["cause"] = f"Couldn't make the test clip: {output[:200]}"
            return _save_decode(result)
        code, log = _run([hwcheck.handbrake(), "-i", source, "-o", target, "-e", "qsv_h264", "--encoder-preset",
                          "speed"] + flag.split(), timeout=timeout)
        result["log"] = "\n".join(log.splitlines()[-30:])
        errors = re.search(r"failed to create hwdevice|No VA display|hwaccel.*(fail|error)|Unknown option", log, re.I)
        result["ok"] = code == 0 and os.path.exists(target) and os.path.getsize(target) > 0 and not errors
        if not result["ok"]:
            result["cause"] = errors.group(0) if errors else f"HandBrake exited with {code}"
    return _save_decode(result)


def _save_decode(result):
    try:
        with open(_decode_cache(), "w", encoding="utf-8") as handle:
            json.dump(result, handle)
    except OSError as error:
        logging.debug(f"Couldn't save the decode test: {error}")
    return result


def bluray_decode_args(job, args):
    """
    HW_DECODE_BLURAY: add HandBrake's QSV decoding option to Blu-ray transcodes that use a QSV
    encoder, but only when the decode test on this machine passed in the last week.
    """
    if job.disctype != "bluray" or not settings.get("HW_DECODE_BLURAY"):
        return args
    if not str(hwcheck.encoder_in(args) or "").startswith("qsv_") or "decoding" in str(args):
        return args
    test = last_decode_test()
    if not test or not test.get("ok") or time.time() - test.get("checked", 0) > DECODE_CACHE_SECONDS:
        logging.info("Hardware decoding for Blu-rays is on, but there's no recent passing decode test "
                     "(Settings > Automation > Hardware check): decoding on the CPU")
        return args
    logging.info(f"Decoding this Blu-ray on the GPU ({test['flag']})")
    return f"{args} {test['flag']}".strip()
