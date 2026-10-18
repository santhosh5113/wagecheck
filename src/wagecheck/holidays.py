"""NSW public holidays (source: https://nsw.gov.au/about-nsw/public-holidays, checked 2026-10-18).

Only NSW for now. Other states differ (e.g. Melbourne Cup Day in Victoria), so a state-aware calendar
is a known gap.
"""

from __future__ import annotations

from datetime import date

NSW: dict[date, str] = {
    date(2026, 1, 1): "New Year's Day",
    date(2026, 1, 26): "Australia Day",
    date(2026, 4, 3): "Good Friday",
    date(2026, 4, 4): "Easter Saturday",
    date(2026, 4, 5): "Easter Sunday",
    date(2026, 4, 6): "Easter Monday",
    date(2026, 4, 25): "Anzac Day",
    date(2026, 4, 27): "Additional Day (Anzac Day)",
    date(2026, 6, 8): "King's Birthday",
    date(2026, 10, 5): "Labour Day",
    date(2026, 12, 25): "Christmas Day",
    date(2026, 12, 26): "Boxing Day",
    date(2026, 12, 28): "Additional Day (Boxing Day)",
    date(2027, 1, 1): "New Year's Day",
    date(2027, 1, 26): "Australia Day",
    date(2027, 3, 26): "Good Friday",
    date(2027, 3, 27): "Easter Saturday",
    date(2027, 3, 28): "Easter Sunday",
    date(2027, 3, 29): "Easter Monday",
    date(2027, 4, 25): "Anzac Day",
    date(2027, 4, 26): "Additional Day (Anzac Day)",
    date(2027, 6, 14): "King's Birthday",
}


def nsw_holidays() -> frozenset[date]:
    return frozenset(NSW)
