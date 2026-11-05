"""Slows down password guessing from outside, before the database is touched.
  per address: many sign-in attempts from one place in a short time are refused;
  per email:   failures against an email are counted whether or not that account exists, so an attacker cannot tell which emails are real.
In memory, so it is per process. The per-account lockout in the database is the one that survives restarts."""
import time
from collections import defaultdict, deque

from ..errors import ApiError


class LoginGuard:
    def __init__(self):
        self._ip: dict = defaultdict(deque)
        self._fail: dict = defaultdict(deque)

    def reset(self) -> None:
        self._ip.clear()
        self._fail.clear()

    @staticmethod
    def _trim(q: deque, window: float, now: float) -> None:
        while q and now - q[0] > window:
            q.popleft()

    def check(self, ip: str | None, email: str, ip_limit: int, fail_limit: int, fail_window: float) -> None:
        now = time.monotonic()
        if ip:
            q = self._ip[ip]
            self._trim(q, 300, now)
            if len(q) >= ip_limit:
                raise ApiError(429, "too_many_attempts", "Too many attempts. Wait a few minutes and try again.")
            q.append(now)
            if len(self._ip) > 20000:
                self._ip.clear()
        f = self._fail[email.lower()]
        self._trim(f, fail_window, now)
        if len(f) >= fail_limit:
            raise ApiError(429, "too_many_attempts", "Too many attempts. Wait a few minutes and try again.")
        if len(self._fail) > 20000:
            self._fail.clear()

    def failed(self, email: str) -> None:
        self._fail[email.lower()].append(time.monotonic())

    def succeeded(self, email: str) -> None:
        self._fail.pop(email.lower(), None)


guard = LoginGuard()
