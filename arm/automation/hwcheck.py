"""
Does hardware encoding (Intel QuickSync, NVENC, AMD VCE) really work for ARM?

HandBrake prints "qsv: is available on this system" even with no GPU, no driver, or a
user who can't open the device, so the only reliable check is a short real test encode
run as ARM's own user. Used:
- before ripping (HW_ENCODER_CHECK): when HB_ARGS_* ask for a hardware encoder and the
  test fails, warn and (HW_ENCODER_FALLBACK) transcode this job on the CPU instead of
  failing after the whole rip;
- when HandBrake fails anyway with a hardware encoder: retry the title on the CPU;
- by the Hardware check page (Settings > Automation > Hardware check).
"""
import json
import logging
import os
import re
import subprocess
import tempfile
import time

import arm.config.config as cfg
from arm.automation import settings, store

HW_ENCODER = re.compile(r"^(qsv|nvenc|vce|vt|mf)_", re.IGNORECASE)
ENCODER_OPTION = re.compile(r"(?<!\S)(-e|--encoder)(\s+|=)(['\"]?)([\w.-]+)\3")
CACHE_SECONDS = 3600

# Log text -> (cause, fix). From ARM-QSV-FINDINGS section 6; the first match wins.
EXPLANATIONS = (
    (r"This version of libva doesn't support|VA-API 1\.15",
     "HandBrake was compiled against an old libva (VA-API < 1.15)",
     "Rebuild the image with --build-arg INTEL_QSV=1 (HandBrake built against libva 2.22), or use HandBrake 1.9.2."),
    (r"undefined symbol: vaMapBuffer2",
     "HandBrake was built against a newer libva than the one installed",
     "Use the same libva at build and run time (rebuild the image)."),
    (r"MFXVideoCORE_QueryPlatform failed|qsv is not available on the system|Quick Sync Video support: no",
     "No usable GPU: the render device isn't passed in, ARM's user can't open it, or the driver doesn't load",
     "Pass --device /dev/dri/renderD128 and give the arm user the device's group (restart the container: "
     "A2RM's startup adds it, or use --group-add <gid>). Then check vainfo."),
    (r"invalid pointer qsv_data->qsv_info|Error initializing an internal MFX session",
     "No QuickSync runtime or driver in the image",
     "Build the image with --build-arg INTEL_QSV=1 (Intel media driver + oneVPL runtime)."),
    (r"va_openDriver\(\) returns -1|vaInitialize failed",
     "The VA-API driver doesn't load (wrong LIBVA_DRIVER_NAME / LIBVA_DRIVERS_PATH, or no iHD driver)",
     "Unset LIBVA_DRIVER_NAME or set it to iHD; install the iHD driver (INTEL_QSV=1 image)."),
    (r"failed to create hwdevice|No VA display found",
     "HandBrake couldn't open the GPU",
     "Check the render device and its permissions; QSV decoding (--enable-hw-decoding qsv) doesn't work on "
     "HandBrake 1.11.2: remove it from HB_ARGS."),
    (r"Cannot load libnvidia-encode|nvenc.*not available|NVENC.*not supported",
     "NVIDIA encoding isn't available in the container",
     "Pass the GPU in with the NVIDIA container toolkit (--gpus all) or use a CPU encoder."),
    (r"Unknown option|unrecognized option|Invalid encoder",
     "HandBrake doesn't accept one of the options in HB_ARGS",
     "Check HB_ARGS_DVD / HB_ARGS_BD (e.g. qsv_av1 needs an Arc / Meteor Lake or newer GPU)."),
)


def encoder_in(args):
    """The -e/--encoder value in a HandBrake argument string, or None"""
    match = ENCODER_OPTION.search(str(args or ""))
    return match.group(4) if match else None


def is_hardware(encoder):
    return bool(encoder and HW_ENCODER.match(encoder))


def software_encoder(encoder):
    return "x265" if re.search(r"(h265|hevc)", str(encoder or ""), re.IGNORECASE) else "x264"


def software_args(args):
    """The same HandBrake arguments with the hardware encoder and its options swapped for the CPU ones"""
    text = str(args or "")

    def swap(match):
        if not is_hardware(match.group(4)):
            return match.group(0)
        return f"{match.group(1)} {software_encoder(match.group(4))}"
    text = ENCODER_OPTION.sub(swap, text)
    text = re.sub(r"(?<!\S)--enable-qsv-decoding(?!\S)", "", text)
    text = re.sub(r"(?<!\S)--enable-hw-decoding(\s+|=)\S+", "", text)
    text = re.sub(r"(?<!\S)--qsv-[\w-]+(=\S+|\s+\d+(?!\S))?", "", text)
    text = re.sub(r"(?<!\S)--encoder-preset(\s+|=)['\"]?[\w-]+['\"]?", "--encoder-preset veryfast", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def explain(log):
    """(cause, fix) for a failed test or transcode log"""
    for pattern, cause, fix in EXPLANATIONS:
        if re.search(pattern, log or "", re.IGNORECASE):
            return cause, fix
    return "HandBrake failed with this encoder", "See the HandBrake output below and docs/quicksync.md."


def handbrake():
    return str(cfg.arm_config.get("HANDBRAKE_CLI") or "HandBrakeCLI")


def used_hardware(encoder, log):
    """True when HandBrake's log shows the hardware encoder really ran"""
    if encoder.lower().startswith("qsv_"):
        return bool(re.search(r"qsv_enc_init: using 'hardware|encoder: .*\(Intel (QSV|Media SDK)\)", log))
    if encoder.lower().startswith("nvenc_"):
        return bool(re.search(r"nvenc", log, re.IGNORECASE))
    return True


def test_encode(encoder, timeout=90):
    """
    Encode a 3-second test clip with this encoder as the current user (ARM's user).
    :return: dict ok, encoder, seconds, cause, fix, log (last lines)
    """
    started = time.monotonic()
    result = {"encoder": encoder, "ok": False, "seconds": None, "cause": "", "fix": "", "log": "",
              "checked": time.time()}
    with tempfile.TemporaryDirectory(prefix="a2rm-hwtest-") as tmp:
        source, target = os.path.join(tmp, "in.mkv"), os.path.join(tmp, "out.mkv")
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=720x480:rate=30",
                            "-t", "3", "-c:v", "mpeg2video", "-q:v", "4", source],
                           capture_output=True, timeout=60, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            result.update(cause=f"Couldn't make the test clip with ffmpeg: {error}", fix="Check ffmpeg is installed.")
            return result
        command = [handbrake(), "-i", source, "-o", target, "-e", encoder]
        if encoder.lower().startswith("qsv_"):
            command += ["--encoder-preset", "speed"]
        try:
            process = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=timeout)
            log = (process.stdout or "") + (process.stderr or "")
            code = process.returncode
        except subprocess.TimeoutExpired:
            log, code = f"HandBrake didn't finish within {timeout} s", -1
        except OSError as error:
            log, code = str(error), -1
        result["seconds"] = round(time.monotonic() - started, 1)
        result["log"] = "\n".join(line for line in log.splitlines()[-40:])
        made_file = os.path.exists(target) and os.path.getsize(target) > 0
        result["ok"] = code == 0 and made_file and used_hardware(encoder, log)
        if not result["ok"]:
            result["cause"], result["fix"] = explain(log)
            if code == 0 and made_file:
                result["cause"] = "HandBrake finished, but its log doesn't show the hardware encoder running"
    return result


def _cache_path():
    return os.path.join(os.path.dirname(str(cfg.arm_config.get("DBFILE") or "/tmp/arm.db")), "a2rm-hwcheck.json")


def cached_test(encoder, max_age=CACHE_SECONDS, fresh=False):
    """test_encode() with the result kept for max_age seconds (shared by the ripper and the web UI)"""
    path = _cache_path()
    try:
        with open(path, encoding="utf-8") as handle:
            cache = json.load(handle)
    except (OSError, ValueError):
        cache = {}
    entry = cache.get(encoder)
    if not fresh and entry and time.time() - entry.get("checked", 0) < max_age:
        return entry
    entry = test_encode(encoder)
    cache[encoder] = entry
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(cache, handle)
        os.replace(tmp, path)
    except OSError as error:
        logging.debug(f"Couldn't save the hardware check result: {error}")
    return entry


def last_result(encoder):
    """The cached test result without running a test, or None"""
    try:
        with open(_cache_path(), encoding="utf-8") as handle:
            return json.load(handle).get(encoder)
    except (OSError, ValueError):
        return None


def job_args(job):
    """HB_ARGS for this disc type from the job's config"""
    config = getattr(job, "config", None)
    if job.disctype == "bluray":
        return getattr(config, "HB_ARGS_BD", "") or ""
    return getattr(config, "HB_ARGS_DVD", "") or ""


def precheck(job):
    """
    Before ripping: if HB_ARGS ask for a hardware encoder, make sure it works. On failure warn,
    and with HW_ENCODER_FALLBACK record that this job transcodes on the CPU.
    :return: the test result, or None when nothing was checked
    """
    if not settings.get("HW_ENCODER_CHECK") or job.disctype not in ("dvd", "bluray"):
        return None
    if getattr(job.config, "SKIP_TRANSCODE", False):
        return None
    encoder = encoder_in(job_args(job))
    if not is_hardware(encoder):
        return None
    result = cached_test(encoder, max_age=600)
    if result["ok"]:
        logging.info(f"Hardware encoder {encoder} works (test encode {result['seconds']} s)")
        return result
    from arm.automation import notify
    fallback = settings.get("HW_ENCODER_FALLBACK")
    what = (f"This disc will be transcoded on the CPU with {software_encoder(encoder)} (slower)." if fallback else
            "The transcode will fail after the rip unless you fix it now or turn on the CPU fallback.")
    message = f"The {encoder} test encode failed: {result['cause']}. {what} Fix: {result['fix']}"
    logging.warning(message)
    if fallback:
        store.update_job(job.job_id, hw_fallback={"encoder": encoder, "reason": result["cause"], "when": "before"})
    notify.in_app("Hardware encoding doesn't work", message)
    notify.send("Hardware encoding doesn't work", message, priority="high", tags=["warning"])
    return result


def planned_fallback(job):
    """True when the pre-rip check decided this job transcodes on the CPU"""
    try:
        return bool(store.load_job(job.job_id).get("hw_fallback"))
    except Exception:  # noqa: BLE001
        return False


def adjust_args(job, args):
    """HB_ARGS to use for this job: the CPU version when the pre-rip check failed"""
    if is_hardware(encoder_in(args)) and planned_fallback(job):
        new_args = software_args(args)
        logging.info(f"Using the CPU encoder instead of the hardware one: {new_args}")
        return new_args
    return args


def retry_command(command):
    """A HandBrake command line with a hardware encoder switched to the CPU, or None if it has none"""
    if not settings.get("HW_ENCODER_FALLBACK") or not is_hardware(encoder_in(command)):
        return None
    return software_args(command)


def record_runtime_fallback(job, command):
    from arm.automation import notify
    encoder = encoder_in(command)
    store.update_job(job.job_id, hw_fallback={"encoder": encoder, "reason": "HandBrake failed", "when": "transcode"})
    message = (f"HandBrake failed with {encoder}; trying again on the CPU with {software_encoder(encoder)}. "
               "Run Settings > Automation > Hardware check to see why.")
    logging.warning(message)
    notify.in_app(f"{job.title}: hardware encoding failed", message)
    notify.send(f"{job.title}: hardware encoding failed", message, priority="default", tags=["warning"])


# HandBrake's job summary: "+ encoder: H.264 (Intel QSV)" (video and audio), and
# "work: average encoding speed for job is 1255.561035 fps" (0 for the scan pass)
ENCODER_LINE = re.compile(r"\+ encoder: ((?:H\.264|H\.265|HEVC|AV1|MPEG-[24]|VP[89]|Theora)[^\n]*)")
SPEED_LINE = re.compile(r"average encoding speed for job is ([0-9.]+) fps")
ENCODER_NAMES = (
    (r"Intel (QSV|Media SDK)", "QSV", True), (r"NVENC", "NVENC", True), (r"(AMD )?(VCE|VCN|AMF)", "VCE", True),
    (r"VideoToolbox", "VideoToolbox", True), (r"libx264|x264", "x264", False), (r"libx265|x265", "x265", False),
    (r"SVT-AV1", "SVT-AV1", False), (r"libvpx", "VP9", False),
)


def describe_encoder(text):
    """"H.264 (Intel QSV)" -> ("QSV H.264 (GPU)", True)"""
    codec = text.split(" (")[0].strip()
    for pattern, name, gpu in ENCODER_NAMES:
        if re.search(pattern, text, re.IGNORECASE):
            if name in ("x264", "x265", "SVT-AV1", "VP9"):
                return f"{name} (CPU)", False
            return f"{name} {codec} (GPU)", gpu
    return text.strip(), False


def encoder_used(log_text):
    """
    What HandBrake really used, from its output in the job log.
    :return: {"label", "gpu", "encoders", "fps"} or None when there was no HandBrake run
    """
    found = []
    for match in ENCODER_LINE.finditer(log_text or ""):
        label, gpu = describe_encoder(match.group(1))
        if (label, gpu) not in found:
            found.append((label, gpu))
    if not found:
        return None
    speeds = [float(value) for value in SPEED_LINE.findall(log_text or "") if float(value) > 0]
    return {"label": " + ".join(label for label, _gpu in found), "gpu": any(gpu for _label, gpu in found),
            "all_gpu": all(gpu for _label, gpu in found), "encoders": [label for label, _gpu in found],
            "fps": round(sum(speeds) / len(speeds)) if speeds else None}


def record_encoder(job, logfile=None):
    """After the transcode: store the encoder the job used, and warn when it isn't the one HB_ARGS asked for"""
    if not settings.get("RECORD_ENCODER") or job.disctype not in ("dvd", "bluray"):
        return None
    path = logfile or os.path.join(str(cfg.arm_config.get("LOGPATH") or ""), str(job.logfile or ""))
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            used = encoder_used(handle.read())
    except OSError as error:
        logging.debug(f"Couldn't read the job log for the encoder: {error}")
        return None
    if used is None:
        return None
    asked = encoder_in(job_args(job))
    used["asked"] = asked
    used["mismatch"] = bool(is_hardware(asked) and not used["all_gpu"])
    store.update_job(job.job_id, encoder=used)
    logging.info(f"Transcoded with {used['label']}" + (f", {used['fps']} fps on average" if used["fps"] else ""))
    if used["mismatch"]:
        from arm.automation import notify
        notify.in_app(f"{job.title}: not transcoded on the GPU",
                      f"HB_ARGS ask for {asked}, but HandBrake used {used['label']}. "
                      "Run Settings > Automation > Hardware check to see why.")
    return used
