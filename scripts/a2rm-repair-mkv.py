#!/usr/bin/env python3
"""
Repair an unfinalized MakeMKV file ("This file was not properly finalized" at the start).

    docker exec -u arm arm-rippers python3 /opt/arm/scripts/a2rm-repair-mkv.py <broken.mkv> [repaired.mkv]

Writes a new, readable file (default: "<name> (repaired).mkv" next to the original) and
never changes the original. Needs free space for one more copy of the file, plus a temporary
copy while it works. Check the new file plays before deleting the old one.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from arm.automation import mkvcheck  # noqa: E402


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__.strip())
        return 2
    source = argv[1]
    target = argv[2] if len(argv) == 3 else os.path.splitext(source.replace(".unfinalized", ""))[0] + " (repaired).mkv"
    state = mkvcheck.header_state(source)
    if state == "ok":
        print(f"{source} already starts with a Matroska header; nothing to repair.")
        return 0
    if os.path.exists(target):
        print(f"{target} already exists; not overwriting it.")
        return 1
    print(f"{source}: {state}. Writing {target} ...")
    mkvcheck.repair(source, target)
    print(f"Done: {target} ({mkvcheck.header_state(target)}). The original was not changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
