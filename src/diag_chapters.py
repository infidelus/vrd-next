"""
Read-only chapter diagnostic for VRD Next.

Run from the src/ folder with your project's Python (the .venv), passing the
source video and the .vprj that was imported:

    python3 diag_chapters.py "/path/to/source.ts" "/path/to/project.vprj"

It does NOT modify anything.  It builds the frame index exactly as the app
does, loads the .vprj's kept ranges, and prints each segment measured two ways
- by frame count ((b-a+1)/fps, what the chapters currently use) and by elapsed
timestamp (PTS, the proposed fix) - plus the cumulative chapter positions both
ways.  Paste the whole output back.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from media.frame_index import build_frame_index
from project.vprj import load_vprj


def hms(seconds):
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def main():
    if len(sys.argv) != 3:
        print("usage: python3 diag_chapters.py <source video> <project.vprj>")
        return

    src, vprj = sys.argv[1], sys.argv[2]

    print("Building frame index (this reads the whole file once)…")
    idx = build_frame_index(src)
    fps = idx.fps or 25.0

    pts = list(idx.pts)
    dups = sum(1 for i in range(1, len(pts)) if pts[i] == pts[i - 1])
    nonmono = sum(1 for i in range(1, len(pts)) if pts[i] < pts[i - 1])

    # Where do duplicate PTS cluster?  Report the frame index of the first and
    # last duplicate, and how many fall in the first 10% of the file.
    dup_positions = [i for i in range(1, len(pts)) if pts[i] == pts[i - 1]]
    first_dup = dup_positions[0] if dup_positions else None
    last_dup = dup_positions[-1] if dup_positions else None
    early = sum(1 for i in dup_positions if i < len(pts) * 0.10)

    dur_count = idx.frame_count / fps
    dur_pts = idx.seconds_of(idx.frame_count - 1) + (1.0 / fps)

    print()
    print("=== INDEX ===")
    print(f"fps (average_rate)      : {fps}")
    print(f"frame_count (entries)   : {idx.frame_count}")
    print(f"duration via count/fps  : {hms(dur_count)}  ({dur_count:.2f}s)")
    print(f"duration via PTS span   : {hms(dur_pts)}  ({dur_pts:.2f}s)")
    print(f"duplicate-PTS entries   : {dups}  (in first 10% of file: {early})")
    print(f"non-monotonic PTS       : {nonmono}")
    print(f"first/last duplicate at : {first_dup} / {last_dup}")

    print()
    print("=== AUDIO STREAMS (for the MKV audio question) ===")
    try:
        import av
        c = av.open(src)
        for i, a in enumerate(c.streams.audio):
            cc = a.codec_context
            print(f"  audio[{i}]: codec={cc.name}  rate={cc.sample_rate}  "
                  f"layout={getattr(cc, 'layout', '?')}")
        c.close()
    except Exception as e:
        print("  (could not probe audio:", e, ")")

    data = load_vprj(vprj, idx)
    keep = list(data.keep_ranges)

    print()
    print("=== KEPT SEGMENTS / CHAPTERS ===")
    print(f"segments: {len(keep)}   markers: {len(data.markers)}")
    print()
    print("chap@count = where the OLD code put the chapter mark (frame counts)")
    print("chap@pts   = where the cut ACTUALLY lands / the NEW code puts it")
    print("drift      = how far the old mark sits from the real join, in frames")
    print()
    print(f"{'join':>4}  {'chap@count':>12}  {'chap@pts':>12}  {'drift(frames)':>13}")

    cum_count = 0.0
    cum_pts = 0.0
    for i, (a, b) in enumerate(keep, start=1):
        # The mark for this scene sits at the cumulative position BEFORE it.
        drift_frames = (cum_count - cum_pts) * fps
        print(f"{i:>4}  {hms(cum_count):>12}  {hms(cum_pts):>12}"
              f"  {drift_frames:>+13.1f}")

        dc = (b - a + 1) / fps
        if b + 1 < idx.frame_count:
            dp = idx.seconds_of(b + 1) - idx.seconds_of(a)
        else:
            dp = idx.seconds_of(b) - idx.seconds_of(a) + (1.0 / fps)
        cum_count += dc
        cum_pts += dp

    print()
    print(f"total via count : {hms(cum_count)}")
    print(f"total via PTS   : {hms(cum_pts)}")
    print()
    print("If 'drift(frames)' grows down the joins on the field-coded episode")
    print("(and is ~0 on an SD/progressive one), that's the bug - and chap@pts")
    print("is where the new build now places every mark.")


if __name__ == "__main__":
    main()
