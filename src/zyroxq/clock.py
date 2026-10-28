from datetime import UTC, datetime, timedelta


class Clock:
    """Wall clock with an offset (simulated escrow only), so the UI can fast-forward review windows and T+7d payouts."""

    def __init__(self) -> None:
        self._offset = timedelta()

    def now(self) -> datetime:
        return datetime.now(UTC) + self._offset

    def advance(self, delta: timedelta) -> None:
        self._offset += delta

    @property
    def offset_hours(self) -> float:
        return self._offset.total_seconds() / 3600
