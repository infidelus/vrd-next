#!/usr/bin/env python3
"""
VRD Next - decode-on-demand viability probe (v3).

Verifies the lazy-decode architecture is frame-accurate, crash-safe on
glitchy broadcast streams, and responsive:

  1. Index build time (demux only, no pixel decode).
  2. EXACT-frame accuracy of random access.
  3. Random-access speed, and how many bad packets had to be skipped.

Run with the SAME Python that runs the app (the one with PyAV):

    python3 index_probe.py /path/to/recording.ts
"""

import sys
import time
import random
import bisect

import av


def build_index(path):
    container = av.open(path)
    stream = container.streams.video[0]
    pts_list, keyframe_pts = [], []
    for packet in container.demux(stream):
        if packet.pts is None:
            continue
        pts_list.append(packet.pts)
        if packet.is_keyframe:
            keyframe_pts.append(packet.pts)
    container.close()
    pts_list.sort()
    keyframe_pts.sort()
    return pts_list, keyframe_pts


def median(values):
    s = sorted(values)
    return s[len(s) // 2] if s else 0


def fetch_frame(container, stream, target_pts, keyframe_pts, median_gap):
    """
    Crash-safe, frame-exact fetch for broadcast MPEG-TS.

    - Decodes packet-by-packet so a corrupt packet is SKIPPED, not fatal.
    - Aims ~1.5 GOPs below the target; if the TS seek overshoots past the
      target (its quirk), widen the margin and retry.
    - Matches on the decoded frame's own PTS.

    Returns (frame, skipped_packet_count).
    """
    margin = median_gap + median_gap // 2
    skipped = 0
    first_over = None
    for attempt in range(4):
        seek_to = max(0, target_pts - margin - attempt * median_gap)
        container.seek(seek_to, stream=stream, backward=True, any_frame=False)
        first_over = None
        hit = None
        for packet in container.demux(stream):
            try:
                frames = packet.decode()
            except av.error.InvalidDataError:
                skipped += 1
                continue
            stop = False
            for frame in frames:
                if frame.pts is None:
                    continue
                if frame.pts == target_pts:
                    hit = frame
                    stop = True
                    break
                if frame.pts > target_pts:
                    first_over = frame
                    stop = True
                    break
            if stop:
                break
        if hit is not None:
            return hit, skipped
        # overshot the GOP without landing on target -> widen and retry
    return first_over, skipped


def main(path):
    print(f"File: {path}\n")

    t0 = time.perf_counter()
    pts_list, keyframe_pts = build_index(path)
    elapsed = time.perf_counter() - t0

    n = len(pts_list)
    if n == 0:
        print("No frames with a PTS were found - unusual stream, stopping.")
        return
    if len(keyframe_pts) < 2:
        print("Too few keyframes to test seeking.")
        return

    rate = n / elapsed if elapsed else 0
    gaps = [keyframe_pts[i + 1] - keyframe_pts[i]
            for i in range(len(keyframe_pts) - 1)]
    median_gap = median(gaps)

    container = av.open(path)
    stream = container.streams.video[0]
    fps = float(stream.average_rate) if stream.average_rate else 25.0
    tb = float(stream.time_base)
    cc = stream.codec_context

    print(f"Index built : {n} frames in {elapsed:.2f}s  ({rate:,.0f} frames/sec)")
    print(f"Keyframes   : {len(keyframe_pts)}")
    print(f"Codec       : {cc.name}  {cc.width}x{cc.height}")
    print(f"Stream      : {fps:.3f} fps, time_base {stream.time_base}")
    print(f"GOP spacing : median ~{median_gap * tb * fps:.1f} frames, "
          f"longest ~{max(gaps) * tb * fps:.1f} frames\n")

    sample = random.sample(range(n), min(40, n))
    times_ms, exact, skipped_total = [], 0, 0
    for idx in sample:
        target = pts_list[idx]
        t0 = time.perf_counter()
        frame, skipped = fetch_frame(container, stream, target,
                                     keyframe_pts, median_gap)
        times_ms.append((time.perf_counter() - t0) * 1000)
        skipped_total += skipped
        if frame is not None and frame.pts == target:
            exact += 1
    container.close()

    print(f"Accuracy    : {exact}/{len(sample)} seeks landed on the EXACT frame")
    print(f"Speed       : median {median(times_ms):.0f} ms, "
          f"worst {max(times_ms):.0f} ms")
    print(f"Bad packets : {skipped_total} skipped during {len(sample)} seeks")

    print("\nWhat to look for:")
    print(f"  - Accuracy should be {len(sample)}/{len(sample)} and it must NOT crash.")
    print("  - 'Bad packets' > 0 just means your recording has transmission")
    print("    glitches we now skip cleanly instead of dying on.")
    print("  - HD median ~200ms on cold seeks is expected; stepping/playback")
    print("    stay fast because they reuse a live decoder.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 index_probe.py /path/to/recording.ts")
        sys.exit(1)
    main(sys.argv[1])
