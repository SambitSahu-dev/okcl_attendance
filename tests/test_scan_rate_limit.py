import pytest

from app.services.scan_rate_limit import ScanRateLimiter


def test_scan_rate_limiter_uses_sliding_window_per_client_and_scope() -> None:
    now = [100.0]
    limiter = ScanRateLimiter(
        start_limit=2,
        frame_limit=1,
        window_seconds=60,
        clock=lambda: now[0],
    )

    assert limiter.try_acquire("kiosk-a", "start") is None
    assert limiter.try_acquire("kiosk-a", "start") is None
    assert limiter.try_acquire("kiosk-a", "start") == 60
    assert limiter.try_acquire("kiosk-b", "start") is None
    assert limiter.try_acquire("kiosk-a", "frame") is None
    assert limiter.try_acquire("kiosk-a", "frame") == 60

    now[0] = 160.0
    assert limiter.try_acquire("kiosk-a", "start") is None
    assert limiter.try_acquire("kiosk-a", "frame") is None


def test_scan_rate_limiter_rejects_invalid_scope_and_limits() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        ScanRateLimiter(start_limit=0)

    limiter = ScanRateLimiter()
    with pytest.raises(ValueError, match="Unsupported"):
        limiter.try_acquire("kiosk", "cancel")
