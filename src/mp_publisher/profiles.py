from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

from .errors import PublisherError


BEIJING = ZoneInfo("Asia/Shanghai")
PACIFIC = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class Profile:
    name: str
    parent_id: str
    source: str
    schedule: str
    title_template: str | None = None


PROFILES = {
    "x": Profile(
        name="x",
        parent_id="4019e605-ccf8-8193-9c2d-dbe20e3112fa",
        source="x-daily",
        schedule="daily 07:30 Asia/Shanghai",
        title_template="X商业情报|{date}",
    ),
    "producthunt": Profile(
        name="producthunt",
        parent_id="4109e605-ccf8-81c4-accd-c19386b4a38d",
        source="producthunt",
        schedule="daily 19:00, 20:00, 21:00 Asia/Shanghai; after producer recovery windows",
    ),
    "trustmrr": Profile(
        name="trustmrr",
        parent_id="4109e605-ccf8-81ed-a46c-f9ab95538f5b",
        source="trustmrr-weekly",
        schedule="Monday 03:00 Asia/Shanghai",
    ),
    "overseas": Profile(
        name="overseas",
        parent_id="4109e605-ccf8-81f8-9094-c5a0023b36b6",
        source="overseas-stories",
        schedule="daily 07:30 Asia/Shanghai; only signed READY stories",
    ),
}


def get_profile(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError:
        raise PublisherError(f"Unknown profile: {name}") from None


def default_period(profile: Profile, now: datetime | None = None) -> str:
    now = now or datetime.now(BEIJING)
    if profile.name == "overseas":
        raise PublisherError("Overseas issues never default to a calendar date")
    if profile.name == "x":
        return now.astimezone(BEIJING).date().isoformat()
    if profile.name == "producthunt":
        return (now.astimezone(PACIFIC).date() - timedelta(days=1)).isoformat()
    monday = now.astimezone(BEIJING).date() - timedelta(days=now.astimezone(BEIJING).weekday())
    return "weekly:" + monday.isoformat()



def scheduled_periods(profile: Profile, now: datetime | None = None) -> tuple[str, ...]:
    """Exact report periods for one scheduled invocation.

    TrustMRR's upstream weekly producer generates a monthly report on the
    first Monday of a month only when four consecutive weekly snapshots
    exist. At 03:00 on that first Monday we check the weekly report and
    independently check the monthly report. A missing monthly READY article
    is an expected skip and must never substitute an older article.
    """
    now = now or datetime.now(BEIJING)
    beijing = now.astimezone(BEIJING)
    primary = default_period(profile, beijing)
    if profile.name == "trustmrr" and beijing.weekday() == 0 and beijing.day <= 7:
        return (primary, "monthly:" + beijing.strftime("%Y-%m"))
    return (primary,)


def validate_period(profile: Profile, period: str) -> str:
    if profile.name == "overseas":
        if not re.fullmatch(r"0[1-9]|[1-9][0-9]{1,4}", period):
            raise PublisherError("Overseas issue must be canonical 01..99999")
        return period
    if profile.name in {"x", "producthunt"}:
        try:
            parsed = date.fromisoformat(period)
        except ValueError:
            raise PublisherError("Period must be canonical YYYY-MM-DD") from None
        if parsed.isoformat() != period:
            raise PublisherError("Period must be canonical YYYY-MM-DD")
        return period
    weekly = re.fullmatch(r"weekly:(\d{4}-\d{2}-\d{2})", period)
    if weekly:
        raw = weekly.group(1)
        try:
            parsed = date.fromisoformat(raw)
        except ValueError:
            raise PublisherError("Invalid TrustMRR weekly period") from None
        if parsed.isoformat() != raw or parsed.weekday() != 0:
            raise PublisherError("TrustMRR weekly period must be a Monday")
        return period
    monthly = re.fullmatch(r"monthly:(\d{4}-\d{2})", period)
    if monthly:
        raw = monthly.group(1)
        try:
            parsed = datetime.strptime(raw, "%Y-%m")
        except ValueError:
            raise PublisherError("Invalid TrustMRR monthly period") from None
        if parsed.strftime("%Y-%m") != raw:
            raise PublisherError("Invalid TrustMRR monthly period")
        return period
    raise PublisherError("TrustMRR period must be weekly:YYYY-MM-DD or monthly:YYYY-MM")


def source_key(profile: Profile, period: str) -> str:
    validate_period(profile, period)
    if profile.name == "trustmrr":
        kind, external = period.split(":", 1)
        return f"trustmrr-{kind}:{external}"
    return f"{profile.source}:{period}"


def expected_title(profile: Profile, period: str) -> tuple[str, bool]:
    """Return title selector and whether it must match exactly."""
    validate_period(profile, period)
    if profile.name == "x":
        return f"{period}｜X 每日商业情报", True
    if profile.name == "producthunt":
        return f"{period}｜", False
    if profile.name == "overseas":
        return f"海外创业志 {period}|", False
    kind, external = period.split(":", 1)
    label = "周报" if kind == "weekly" else "月报"
    return f"{external}｜TrustMRR {label}｜", False


def display_title(profile: Profile, period: str, notion_title: str) -> str:
    selector, exact = expected_title(profile, period)
    if exact:
        if notion_title != selector:
            raise PublisherError("Notion title changed after selection")
        assert profile.title_template is not None
        return profile.title_template.format(date=period)
    if not notion_title.startswith(selector):
        raise PublisherError("Notion title changed after selection")
    title = notion_title[len(selector):].strip()
    if not title or len(title) > 200:
        raise PublisherError("Invalid article title")
    if profile.name == "overseas":
        # Keep the serial and column identity in the actual WeChat draft title.
        # The cover/period are stable editorial metadata, not a disposable date label.
        if len(notion_title.encode("utf-8")) > 64:
            raise PublisherError("Overseas draft title exceeds 64 UTF-8 bytes")
        return notion_title
    return title
