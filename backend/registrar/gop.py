"""Remembers the video since the last keyframe, so a viewer who joins part-way starts at a keyframe straight away.

Without this a new viewer receives bytes from "now", which is usually the middle of a group of pictures. FFmpeg cannot show anything until the
next keyframe, which recorders send only every one to four seconds, and it spends longer than that probing the stream. With it, the viewer
is first given everything from the latest keyframe up to now, then the live bytes, so the stream is continuous and starts at once.

The stream is an MPEG program stream carrying H.264. A keyframe starts where an SPS (or, if a recorder never repeats it, an IDR slice)
appears; the cache starts at the pack header just before it. A new keyframe is only recognised after a normal (non-IDR) slice has been
seen, so a picture sent as several IDR slices is not cut in the middle. Other codecs (H.265) are not recognised: the cache then stays
empty and viewers simply join live, as before. A keyframe interval longer than the cache limit empties the cache the same way, so a viewer
is never given a replay that starts in the middle of a group of pictures."""

START = b"\x00\x00\x01"
PACK = b"\x00\x00\x01\xba"


class GopCache:
    def __init__(self, max_bytes: int):
        self.max = max_bytes
        self.buf = bytearray()
        self.valid = False
        self.armed = True            # ready to recognise a keyframe
        self._from = 0               # where to resume scanning (a start code can be split across two chunks)
        self.keyframes = 0
        self.overflows = 0

    def feed(self, data: bytes) -> None:
        if self.max <= 0 or not data:
            return
        self.buf += data
        self._scan()
        if len(self.buf) > self.max:
            self.buf.clear()
            self.valid, self.armed, self._from = False, True, 0
            self.overflows += 1

    def _scan(self) -> None:
        buf, i = self.buf, self._from
        while True:
            i = buf.find(START, i)
            if i < 0:
                self._from = max(0, len(buf) - 2)          # keep the last two bytes: they may begin a start code
                return
            if i + 3 >= len(buf):
                self._from = i                              # the code byte has not arrived yet
                return
            c = buf[i + 3]
            if c < 0x80:                                    # a video NAL unit (PS and PES codes are 0xB9 and above)
                t = c & 0x1F
                if t == 1:
                    self.armed = True
                elif t in (5, 7) and self.armed:
                    pack = buf.rfind(PACK, 0, i)
                    if pack >= 0:
                        del buf[:pack]
                        i -= pack
                        self.valid, self.armed = True, False
                        self.keyframes += 1
            i += 3

    def snapshot(self) -> bytes:
        return bytes(self.buf) if self.valid else b""
