"""A customer's opening hours: one opening time and one closing time, in their own time zone, and the days of the week they are closed all day.

"Counting stopped" is only expected while the place is open; "After-hours activity" only matters while it is closed. With no hours set neither can be decided,
so neither fires. Closing after midnight (open 18:00, close 02:00) is understood: the night belongs to the day it started on."""
import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class Hours:
    open: time | None
    close: time | None
    closed_days: tuple = ()
    per_day: tuple = ()          # seven (open, close) pairs, Monday first, None for a closed day. Empty: every open day uses `open` and `close`.

    @property
    def set(self) -> bool:
        if self.per_day:
            return any(d is not None and d[0] != d[1] for d in self.per_day)
        return self.open is not None and self.close is not None and self.open != self.close

    @classmethod
    def of(cls, row) -> "Hours":
        per = ()
        raw = row["day_hours"] if "day_hours" in row.keys() else None
        if raw:
            raw = json.loads(raw) if isinstance(raw, str) else raw
            per = tuple((time.fromisoformat(raw[str(i)][0]), time.fromisoformat(raw[str(i)][1])) if str(i) in raw else None for i in range(7))
        return cls(row["open_time"], row["close_time"], tuple(row["closed_days"] or ()), per)

    def _off(self, day) -> bool:
        return day.weekday() in self.closed_days or (bool(self.per_day) and self.per_day[day.weekday()] is None)

    def _span(self, day) -> tuple[datetime, datetime]:
        o, c = self.per_day[day.weekday()] if self.per_day else (self.open, self.close)
        start = datetime.combine(day, o)
        end = datetime.combine(day, c)
        return start, end + timedelta(days=1) if end <= start else end

    def session(self, local: datetime) -> tuple[datetime, datetime] | None:
        """The opening (start, end) that `local` (a naive local time) falls inside, or None when the place is closed then."""
        if not self.set:
            return None
        for day in (local.date(), local.date() - timedelta(days=1)):          # yesterday's opening can run past midnight
            if self._off(day):
                continue
            start, end = self._span(day)
            if start <= local < end:
                return start, end
        return None

    def is_open(self, local: datetime) -> bool:
        return self.session(local) is not None

    def closed_since(self, local: datetime) -> datetime | None:
        """When the place last closed, if it is closed now; None when it is open or no hours are set. Looks back a week (a shop shut for the weekend)."""
        if not self.set or self.is_open(local):
            return None
        for back in range(0, 8):
            day = local.date() - timedelta(days=back)
            if self._off(day):
                continue
            _, end = self._span(day)
            if end <= local:
                return end
        return None

    def opens_next(self, local: datetime) -> datetime | None:
        if not self.set:
            return None
        for ahead in range(0, 8):
            day = local.date() + timedelta(days=ahead)
            if self._off(day):
                continue
            start, _ = self._span(day)
            if start > local:
                return start
        return None
