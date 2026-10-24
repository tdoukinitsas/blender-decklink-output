# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Thomas Doukinitsas

"""Exercise bdo_decklink.dll without Blender.

    python test_output.py                          list devices and output modes
    python test_output.py DEVICE MODE [SECONDS]    send moving colour bars, print stats
                                                    (MODE is a name from the list, e.g. 1080p25)
    python test_output.py DEVICE MODE SECONDS --push-fps 7
                                                    push slower than the card to prove the
                                                    card repeats frames instead of stalling
"""

import argparse
import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "blender_decklink_output"))
import native  # noqa: E402  (the add-on's ctypes wrapper; it does not import bpy)


def bars(width, height, phase):
    """BGRA top-down colour bars with a moving white column."""
    colours = [(191, 191, 191), (0, 191, 191), (191, 191, 0), (0, 191, 0),
               (191, 0, 191), (0, 0, 191), (191, 0, 0)]  # BGR
    row = bytearray(width * 4)
    for x in range(width):
        b, g, r = colours[x * len(colours) // width]
        row[x * 4:x * 4 + 4] = bytes((b, g, r, 255))
    column = int(phase * width) % width
    marker = max(width // 64, 4)
    for x in range(column, min(column + marker, width)):
        row[x * 4:x * 4 + 4] = b"\xff\xff\xff\xff"
    return bytes(row) * height


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("device", nargs="?")
    parser.add_argument("mode", nargs="?")
    parser.add_argument("seconds", nargs="?", type=float, default=5.0)
    parser.add_argument("--push-fps", type=float, default=0.0)
    args = parser.parse_args()

    data = native.Devices.refresh()
    print("available: %s  api: %s  %s" % (data["available"], data["api_version"], data["error"]))
    for device in data["devices"]:
        print("- %s (%s) playback=%s busy=%d" % (device["name"], device["model"],
                                                 device["playback"], device["busy"]))
        if not args.device:
            for mode in device["modes"]:
                print("    %-14s %s" % (mode["name"], native.mode_description(mode)))
    if not args.device:
        return

    mode = native.Devices.mode(args.device, args.mode)
    if mode is None:
        raise SystemExit("no mode %r on %r" % (args.mode, args.device))
    output = native.NativeOutput(args.device, mode["code"], 3)
    print("open: %d x %d @ %.3f, pixel format %08x" % (output.width, output.height, output.fps,
                                                       output.pixel_format))
    push_fps = args.push_fps or output.fps
    started = time.perf_counter()
    pushes = 0
    try:
        while time.perf_counter() - started < args.seconds:
            frame = bars(output.width, output.height, pushes / push_fps / 4.0)
            buffer = ctypes.create_string_buffer(frame, len(frame))
            result = output.push(ctypes.addressof(buffer), output.width, output.height,
                                 output.width * 4, 0)
            if result != 0:
                print("push returned %d" % result)
            pushes += 1
            time.sleep(max(started + pushes / push_fps - time.perf_counter(), 0.0))
        s = output.stats()
        print("pushed %d  scheduled %d  completed %d  late %d  dropped %d  resyncs %d  buffered %d"
              % (s.pushed, s.scheduled, s.completed, s.late, s.dropped, s.resyncs, s.buffered))
    finally:
        output.close()
    print("closed")


if __name__ == "__main__":
    main()
