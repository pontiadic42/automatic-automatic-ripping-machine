"""
Unfinalized MakeMKV files.

When MakeMKV is interrupted it leaves .mkv files that start with "This file was not properly
finalized" instead of a Matroska (EBML) header, so no player or tool can open them, but the
rest of the file (Info, Tracks, every cluster) is intact. A2RM checks every file after a rip
(and the library health check does too), and rebuild_header() makes a readable copy:
a new EBML header + a Segment of unknown size, followed by the original data from the first
top-level element it finds. Remuxing that copy (ffmpeg -c copy) gives a clean file.
"""
import logging
import os
import subprocess

EBML_MAGIC = b"\x1a\x45\xdf\xa3"
UNFINALIZED_TEXT = b"This file was not properly finalized"
# Top-level Segment children, in the order MakeMKV writes them
SEGMENT_CHILDREN = (b"\x11\x4d\x9b\x74",   # SeekHead
                    b"\x15\x49\xa9\x66",   # Info
                    b"\x16\x54\xae\x6b",   # Tracks
                    b"\x1f\x43\xb6\x75")   # Cluster
SEARCH_BYTES = 1 << 20


def header_state(path):
    """'ok', 'unfinalized' (MakeMKV's marker), 'bad' (some other start) or 'empty'"""
    try:
        with open(path, "rb") as handle:
            head = handle.read(64)
    except OSError:
        return "bad"
    if not head:
        return "empty"
    if head.startswith(EBML_MAGIC):
        return "ok"
    if head.startswith(UNFINALIZED_TEXT):
        return "unfinalized"
    return "bad"


def unfinalized_files(folder):
    """The .mkv files in a folder (not sub-folders) that don't start with a Matroska header"""
    found = []
    try:
        names = sorted(os.listdir(folder or ""))
    except OSError:
        return found
    for name in names:
        path = os.path.join(folder, name)
        if name.lower().endswith(".mkv") and os.path.isfile(path) and header_state(path) != "ok":
            found.append(path)
    return found


def _element(element_id, data):
    size = len(data)
    if size >= 127:
        raise ValueError("element too large for a 1-byte size")
    return element_id + bytes([0x80 | size]) + data


def _uint(value):
    return value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")


def ebml_header():
    body = b"".join((
        _element(b"\x42\x86", _uint(1)),          # EBMLVersion
        _element(b"\x42\xf7", _uint(1)),          # EBMLReadVersion
        _element(b"\x42\xf2", _uint(4)),          # EBMLMaxIDLength
        _element(b"\x42\xf3", _uint(8)),          # EBMLMaxSizeLength
        _element(b"\x42\x82", b"matroska"),       # DocType
        _element(b"\x42\x87", _uint(4)),          # DocTypeVersion
        _element(b"\x42\x85", _uint(2)),          # DocTypeReadVersion
    ))
    return _element(EBML_MAGIC, body)


SEGMENT_UNKNOWN_SIZE = b"\x18\x53\x80\x67" + b"\x01\xff\xff\xff\xff\xff\xff\xff"


def data_offset(path):
    """Where the first Segment child (SeekHead/Info/Tracks/Cluster) starts, or None"""
    with open(path, "rb") as handle:
        head = handle.read(SEARCH_BYTES)
    offsets = [head.find(element) for element in SEGMENT_CHILDREN]
    offsets = [offset for offset in offsets if offset >= 0]
    return min(offsets) if offsets else None


def rebuild_header(source, target, chunk=1 << 24):
    """
    Write a copy of an unfinalized file with a valid header. The original is not changed.
    :return: the offset the original data was copied from
    """
    offset = data_offset(source)
    if offset is None:
        raise ValueError(f"No Matroska data found in the first {SEARCH_BYTES // 1024} KB of {source}")
    with open(source, "rb") as src, open(target, "wb") as out:
        out.write(ebml_header() + SEGMENT_UNKNOWN_SIZE)
        src.seek(offset)
        while True:
            block = src.read(chunk)
            if not block:
                break
            out.write(block)
    return offset


def repair(source, target):
    """Rebuild the header, then remux with ffmpeg into a clean file (both next to target)"""
    fixed = target + ".header-fixed.mkv"
    rebuild_header(source, fixed)
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", fixed, "-map", "0", "-c", "copy", target],
                       check=True, timeout=6 * 3600)
    finally:
        try:
            os.remove(fixed)
        except OSError as error:
            logging.debug(f"Couldn't remove {fixed}: {error}")
    return target
