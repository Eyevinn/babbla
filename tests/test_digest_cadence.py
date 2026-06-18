from datetime import datetime, timezone
from babbla.digest.cadence import cadence_bucket, is_due


def _utc(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def test_daily_bucket_is_local_date():
    assert cadence_bucket(_utc(2026, 6, 18), "daily", "UTC") == "2026-06-18"


def test_weekly_bucket_is_iso_year_week():
    assert cadence_bucket(_utc(2026, 6, 18), "weekly", "UTC").startswith("2026-W")


def test_tz_shifts_the_day_boundary():
    # 23:00 UTC is already the next day in Europe/Stockholm (UTC+2 in June).
    assert cadence_bucket(_utc(2026, 6, 18, 23), "daily", "Europe/Stockholm") == "2026-06-19"


def test_due_when_no_prior_digest():
    assert is_due(_utc(2026, 6, 18), None, "weekly", "UTC") is True


def test_not_due_within_same_bucket():
    now = _utc(2026, 6, 18, 15)
    earlier_same_day = _utc(2026, 6, 18, 9).timestamp()
    assert is_due(now, earlier_same_day, "daily", "UTC") is False


def test_due_in_new_bucket():
    now = _utc(2026, 6, 19, 9)
    yesterday = _utc(2026, 6, 18, 9).timestamp()
    assert is_due(now, yesterday, "daily", "UTC") is True
