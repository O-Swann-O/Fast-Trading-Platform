from datetime import datetime, timezone


class Clock:

    def now(self) -> datetime:
        raise NotImplementedError

    def timestamp(self) -> int:
        return int(self.now().timestamp())


class WallClock(Clock):

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SimClock(Clock):

    def __init__(self):
        self._now = None

    def now(self) -> datetime:
        return self._now

    def timestamp(self) -> int:
        t = self._now
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return int(t.timestamp())

    def advance(self, ts: datetime) -> None:
        if self._now is None or ts > self._now:
            self._now = ts