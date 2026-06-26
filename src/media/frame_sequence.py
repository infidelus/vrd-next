"""
FrameSequence - a list-like view over a FrameFetcher.

The original UI code treats decoded frames as a Python list: it asks for
len(window.frames) and window.frames[index].  Rather than rewrite every
consumer, this object answers both operations by delegating to the fetcher,
so the whole UI keeps working while the frames are decoded on demand instead
of held in RAM.

  len(seq)        -> total frame count (from the index)
  seq[index]      -> decoded VideoFrame at that index (via the fetcher)
  seq[a:b]        -> list of frames (rarely used, supported for safety)
  if not seq:     -> False when there is no media open

Negative indices work like a normal list (seq[-1] is the last frame).
"""


class FrameSequence:

    def __init__(
            self,
            fetcher,
            index,
    ):
        self.fetcher = fetcher
        self.index = index

    def __len__(self):
        return self.index.frame_count

    def __bool__(self):
        return self.index.frame_count > 0

    def __getitem__(self, key):

        if isinstance(key, slice):
            return [
                self.fetcher.frame_at(i)
                for i in range(*key.indices(len(self)))
            ]

        if key < 0:
            key += len(self)

        return self.fetcher.frame_at(key)
