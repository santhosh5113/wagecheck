"""Hospitality Industry (General) Award [MA000009], general stream, from 1 July 2026. Expected dollar
figures are the values MAPD returned on 2026-10-18 for 'Adult full-time and part-time employees' and
'Adult casual employees' (`wagecheck mapd penalties MA000009 --rate-type AD`)."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from wagecheck.engine import Calculator, round_cents
from wagecheck.mapd import MapdRate, cross_check
from wagecheck.models import EmployeeProfile, EmploymentType, Shift
from wagecheck.rules.loader import load_pack

INTRO, L2 = "hospitality_introductory", "hospitality_level_2"


@pytest.fixture(scope="module")
def pack():
    return load_pack("MA000009", date(2026, 10, 18))


@pytest.fixture
def calc(pack):
    return Calculator(pack, public_holidays=[date(2026, 12, 25)])


def prof(level=INTRO, emp="casual"):
    return EmployeeProfile(classification=level, employment_type=EmploymentType(emp))


def s(a, b):
    return Shift(start=datetime.fromisoformat(a), end=datetime.fromisoformat(b))


def bands(r):
    return [(li.band, li.minutes, str(li.rate), str(li.amount)) for li in r.lines]


def test_pack_is_consistent(pack):
    assert pack.integrity_errors() == []


@pytest.mark.parametrize(("level", "pct", "dollars"), [
    (INTRO, 125, "32.18"), (INTRO, 150, "38.61"), (INTRO, 175, "45.05"), (INTRO, 200, "51.48"),
    (INTRO, 225, "57.92"), (INTRO, 250, "64.35"), (L2, 175, "47.39"), ("hospitality_level_6", 250, "80.33"),
])
def test_percentages_reproduce_mapd_dollars(pack, level, pct, dollars):
    assert round_cents(pack.classifications[level].hourly * pct / 100) == Decimal(dollars)


def test_casual_sunday_is_175_at_every_level(calc):
    # unlike the Restaurant award, where Introductory to Level 2 casuals get 150% on Sunday
    r = calc.calculate(s("2026-10-25T10:00", "2026-10-25T12:00"), prof())
    assert bands(r) == [("sunday", 120, "45.05", "90.10")]


def test_evening_amount_starts_at_7pm(calc):
    r = calc.calculate(s("2026-10-19T18:00", "2026-10-19T20:00"), prof())
    assert bands(r) == [("ordinary", 60, "32.18", "32.18"), ("evening_7pm_to_midnight", 60, "35.13", "35.13")]


def test_night_amount_runs_until_7am(calc):
    r = calc.calculate(s("2026-10-20T05:00", "2026-10-20T08:00"), prof())
    assert bands(r) == [("night_midnight_to_7am", 120, "36.60", "73.20"), ("ordinary", 60, "32.18", "32.18")]


def test_saturday_overtime_is_200_from_the_first_hour(calc):
    r = calc.calculate(s("2026-10-24T07:00", "2026-10-24T20:00"), prof(emp="full_time"))
    assert bands(r)[-1] == ("overtime_saturday_first", 90, "51.48", "77.22")
    assert r.total == round_cents(Decimal("32.18") * Decimal("11.5")) + Decimal("77.22")


def test_casual_weekday_overtime_after_12_hours_is_150(calc):
    r = calc.calculate(s("2026-10-21T07:00", "2026-10-21T20:00"), prof())
    assert bands(r)[-1][:3] == ("overtime_first", 60, "38.61")


def test_casual_public_holiday_overtime_is_250(calc):
    r = calc.calculate(s("2026-12-25T07:00", "2026-12-25T20:00"), prof())
    assert bands(r)[-1][:3] == ("overtime_public_holiday", 60, "64.35")
    assert r.total == Decimal("64.35") * 13


def test_cross_check_needs_stream_when_names_repeat(pack):
    live = [MapdRate("Level 3", 4, Decimal("27.97"), "2026-07-01", None, "Hospitality"),
            MapdRate("Level 3", 4, Decimal("29.45"), "2026-07-01", None, "Casino gaming")]
    l3 = pack.classifications["hospitality_level_3"].model_copy(update={"mapd_parent": None, "mapd_fixed_id": None})
    one = pack.model_copy(update={"classifications": {"hospitality_level_3": l3}})
    assert "ambiguous" in cross_check(one, live)[0]
    fixed = one.model_copy(update={"classifications": {
        "hospitality_level_3": l3.model_copy(update={"mapd_parent": "Hospitality"})}})
    assert cross_check(fixed, live) == []


# The 15 adult hourly records MAPD returned for MA000009 on 2026-10-18 (general + casino streams).
LIVE = [
    ("Introductory level", 1, "26.13", None), ("Introductory level", 1, "25.74", None),
    ("Level 1", 2, "27.64", "Casino electronic gaming employee grade 1"),
    ("Level 1", 2, "26.44", "Food and beverage attendant grade 1; Guest service grade 1; Kitchen attendant grade 1"),
    ("Level 2", 3, "27.08", "Clerical grade 1; Cook grade 1; Door person/security officer grade 1; Food and beverage "
     "attendant grade 2; Front office grade 1; Gardener grade 1; Guest service grade 2; Kitchen attendant grade 2; "
     "Leisure attendant grade 1; Storeperson grade 1"),
    ("Level 2", 3, "28.53", "Casino electronic gaming employee grade 2; Casino equipment technician grade 1; Casino "
     "table gaming employee grade 1; Customer liaison officer; Gaming finance employee grade 1"),
    ("Level 3", 4, "27.97", "Clerical grade 2; Cook grade 2; Food and beverage attendant grade 3; Fork-lift driver; "
     "Front office grade 2; Gardener grade 2; Guest service grade 3; Handyperson; Kitchen attendant grade 3; Leisure "
     "attendant grade 2; Storeperson grade 2; Timekeeper/security officer grade 2"),
    ("Level 3", 4, "29.45", "Casino equipment technician grade 2; Gaming finance employee grade 2; Security officer "
     "grade 1"),
    ("Level 3A", 4, "30.83", "Casino table gaming employee grade 2"),
    ("Level 4", 5, "29.45", "Clerical grade 3; Cook (tradesperson) grade 3; Food and beverage attendant (tradesperson) "
     "grade 4; Front office grade 3; Gardener grade 3 (tradesperson); Guest service grade 4; Leisure attendant grade 3;"
     " Storeperson grade 3"),
    ("Level 4", 6, "31.3", "Casino equipment technician grade 3; Gaming finance employee grade 3; Security officer "
     "grade 2"),
    ("Level 5", 6, "31.3", "Clerical supervisor; Cook (tradesperson) grade 4; Food and beverage supervisor; Front "
     "office supervisor; Gardener grade 4 (tradesperson); Guest service supervisor"),
    ("Level 5", 7, "32.23", "Casino table gaming employee grade 3; Gaming finance employee grade 4"),
    ("Level 6", 7, "32.13", "Cook (tradesperson) grade 5"),
    ("Level 6", 8, "33.16", "Casino table gaming employee grade 4; Gaming finance employee grade 5; Surveillance "
     "operator"),
]


# classification_fixed_id for each LIVE row, in the same order, as printed by `mapd check` on 2026-10-18
LIVE_IDS = [292, 242, 293, 243, 246, 294, 257, 299, 302, 268, 303, 276, 306, 282, 308]


def test_live_mapd_output_confirms_all_seven_levels(pack):
    live = [MapdRate(n, lvl, Decimal(r), "2026-07-01", None, parent, fid)
            for (n, lvl, r, parent), fid in zip(LIVE, LIVE_IDS, strict=True)]
    assert cross_check(pack, live) == []


def test_name_and_stream_alone_leave_introductory_ambiguous(pack):
    live = [MapdRate(n, lvl, Decimal(r), "2026-07-01", None, parent) for n, lvl, r, parent in LIVE]
    stripped = pack.model_copy(update={"classifications": {
        k: c.model_copy(update={"mapd_fixed_id": None}) for k, c in pack.classifications.items()}})
    diffs = cross_check(stripped, live)
    assert len(diffs) == 1 and diffs[0].startswith("hospitality_introductory: ambiguous")
