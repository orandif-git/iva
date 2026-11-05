"""Counting people across a line.

A person counts when their track's centre moves from one side of the line to the other, passing through the line's length
(not around its end). To ignore jitter, a side only counts as "confirmed" once the centre is a small distance from the line."""
from .geometry import segments_intersect, signed_distance


class LineCounter:
    def __init__(self, a, b, in_direction=None, band: float = 0.015):
        self.a, self.b, self.band = tuple(a), tuple(b), band
        if in_direction is None:                           # default: "in" is toward the left of a->b
            dx, dy = b[0] - a[0], b[1] - a[1]
            in_direction = (-dy, dx)
        self.in_dir = tuple(in_direction)
        self._state: dict = {}                             # track id -> (confirmed side, position when confirmed)

    def update(self, track_id: int, pos) -> str | None:
        """pos is the track's centre in 0..1 coordinates. Returns "in", "out" or None."""
        d = signed_distance(pos, self.a, self.b)
        if abs(d) < self.band:
            return None                                    # on the line: undecided
        side = 1 if d > 0 else -1
        prev = self._state.get(track_id)
        if prev is None:
            self._state[track_id] = (side, pos)
            return None
        prev_side, prev_pos = prev
        if side == prev_side:
            self._state[track_id] = (side, pos)
            return None
        crossed = segments_intersect(prev_pos, pos, self.a, self.b)
        self._state[track_id] = (side, pos)
        if not crossed:                                    # went around the end of the line
            return None
        motion = (pos[0] - prev_pos[0], pos[1] - prev_pos[1])
        return "in" if motion[0] * self.in_dir[0] + motion[1] * self.in_dir[1] > 0 else "out"

    def forget(self, track_id: int) -> None:
        self._state.pop(track_id, None)
