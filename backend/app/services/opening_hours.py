"""A customer's opening hours: one opening time and one closing time, in their own time zone, and the days of the week they are closed all day.

"Counting stopped" is only expected while the place is open; "After-hours activity" only matters while it is closed. With no hours set neither can be decided,
so neither fires. Closing after midnight (open 18:00, close 02:00) is understood: the night belongs to the day it started on."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class Hours:
    open: time | None
    close: time | None
    closed_days: tuple = ()

    @property
    def set(self) -> bool:
        return self.open is not None and self.close is not None and self.open != self.close

    @classmethod
    def of(cls, row) -> "Hours":
        return cls(row["open_time"], row["close_time"], tuple(row["closed_days"] or ()))

    def _span(self, day) -> tuple[datetime, datetime]:
        start = datetime.combine(day, self.open)
        end = datetime.combine(day, self.close)
        return start, end + timedelta(days=1) if end <= start else end

    def session(self, local: datetime) -> tuple[datetime, datetime] | None:
        """The opening (start, end) that `local` (a naive local time) falls inside, or None when the place is closed then."""
        if not self.set:
            return None
        for day in (local.date(), local.date() - timedelta(days=1)):          # yesterday's opening can run past midnight
            if day.weekday() in self.closed_days:
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
            if day.weekday() in self.closed_days:
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
            if day.weekday() in self.closed_days:
                continue
            start, _ = self._span(day)
            if start > local:
                return start
        return None
