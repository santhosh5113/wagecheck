import time
from datetime import date
from decimal import Decimal

import httpx
import pytest

from wagecheck.mapd import MapdClient, MapdRate, cross_check, current_hourly_rates, current_penalties
from wagecheck.mapd.client import MapdApiError, MapdConfigError
from wagecheck.rules.loader import load_pack


def rec(level, rate, name, code="AD", rtype="Hourly", frm="2026-07-01", to=None):
    return {"classification_fixed_id": level, "classification": name, "classification_level": level,
            "calculated_rate": rate, "calculated_rate_type": rtype, "employee_rate_type_code": code,
            "operative_from": frm, "operative_to": to}


# Shape follows PayRatesModel in the published OpenAPI spec; values are test data.
PAGES = {
    1: {"results": [
        rec(1, 26.55, "Retail Employee Level 1", frm="2025-07-01", to="2026-06-30"),  # last year's rate
        rec(1, 27.81, "Retail Employee Level 1"),
        rec(1, 1056.80, "Retail Employee Level 1", rtype="Weekly"),
    ], "_meta": {"current_page": 1, "page_count": 2, "has_more_results": True}},
    2: {"results": [
        rec(2, 28.40, "Retail Employee Level 2"),
        rec(1, 12.51, "Retail Employee Level 1", code="JR"),
    ], "_meta": {"current_page": 2, "page_count": 2, "has_more_results": False}},
}


def handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Ocp-Apim-Subscription-Key"] == "test-key"
    assert request.url.path == "/api/v1/awards/MA000004/pay-rates"
    return httpx.Response(200, json=PAGES[int(request.url.params["page"])])


def test_selects_rate_in_force_and_flags_differences(pack):
    with MapdClient(api_key="test-key", transport=httpx.MockTransport(handler), cache_dir=None) as c:
        records = c.pay_rates("MA000004")
    rates = current_hourly_rates(records, date(2026, 10, 18))
    assert {(r.level, r.rate) for r in rates} == {(1, Decimal("27.81")), (2, Decimal("28.4"))}
    old = current_hourly_rates(records, date(2026, 3, 1))
    assert [r.rate for r in old] == [Decimal("26.55")]
    diffs = cross_check(pack, rates)
    assert "retail_employee_level_2: pack 28.45 != MAPD 28.4" in diffs
    assert "retail_employee_level_3: missing from MAPD response" in diffs
    assert not any(d.startswith("retail_employee_level_1") for d in diffs)


def test_two_classifications_on_one_level_need_names():
    ff = load_pack("MA000003")
    one = "Fast Food Employee Level 3 - in charge of one or no other person"
    two = "Fast Food Employee Level 3 - in charge of two or more other people"
    rates = [MapdRate(one, 3, Decimal("29.91"), None, None), MapdRate(two, 3, Decimal("30.27"), None, None)]
    level3 = ff.classifications["fast_food_employee_level_3_in_charge_of_one_or_none"].model_copy(
        update={"mapd_level": 3, "mapd_name": None})
    pack = ff.model_copy(update={"classifications": {"l3": level3}})
    assert "ambiguous" in cross_check(pack, rates)[0]
    named = level3.model_copy(update={"mapd_level": None, "mapd_name": rates[0].classification})
    assert cross_check(ff.model_copy(update={"classifications": {"l3": named}}), rates) == []


def test_penalty_rows_filtered_by_date_and_level():
    rows = [
        {"classification_level": 1, "employee_rate_type_code": "CA", "penalty_description": "Sunday",
         "rate": 150, "penalty_calculated_value": 41.72, "clause_description": "Penalty rates",
         "operative_from": "2026-07-01", "operative_to": None},
        {"classification_level": 1, "employee_rate_type_code": "CA", "penalty_description": "Sunday",
         "rate": 150, "penalty_calculated_value": 39.83, "clause_description": "Penalty rates",
         "operative_from": "2025-07-01", "operative_to": "2026-06-30"},
        {"classification_level": 2, "employee_rate_type_code": "CA", "penalty_description": "Sunday",
         "rate": 175, "penalty_calculated_value": 51.54, "clause_description": "Penalty rates",
         "operative_from": "2026-07-01", "operative_to": None},
    ]
    got = current_penalties(rows, date(2026, 10, 18), level=1)
    assert [r["penalty_calculated_value"] for r in got] == [41.72]


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("FWC_API_KEY", raising=False)
    with pytest.raises(MapdConfigError):
        MapdClient()


def test_awards_endpoint_and_page_count_fallback():
    seen = []

    def h(req):
        seen.append(dict(req.url.params))
        page = int(req.url.params["page"])
        # some responses may omit has_more_results; page_count must still stop the loop
        return httpx.Response(200, json={"results": [{"code": f"MA00000{page}"}],
                                         "_meta": {"current_page": page, "page_count": 3}})

    with MapdClient(api_key="test-key", transport=httpx.MockTransport(h), cache_dir=None) as c:
        got = c.awards(name="retail")
    assert [a["code"] for a in got] == ["MA000001", "MA000002", "MA000003"]
    assert seen[0]["name"] == "retail" and seen[0]["limit"] == "100"


def one_page(code):
    return {"results": [{"code": code}], "_meta": {"has_more_results": False}}


def test_throttling_is_retried_with_backoff():
    calls, waits = [], []

    def h(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "2"} if len(calls) == 1 else {})
        return httpx.Response(200, json=one_page("MA000004"))

    with MapdClient(api_key="k", transport=httpx.MockTransport(h), cache_dir=None, sleep=waits.append) as c:
        assert c.awards()[0]["code"] == "MA000004"
    assert waits == [2.0, 2.0]  # Retry-After honoured, then exponential backoff from 1s


def test_bad_request_raises_with_api_error_detail():
    def h(req):
        return httpx.Response(400, json={"errors": [{"id": "x", "code": "BadRequest", "detail": "bad sort"}]})

    with MapdClient(api_key="k", transport=httpx.MockTransport(h), cache_dir=None) as c, \
            pytest.raises(MapdApiError, match="BadRequest: bad sort"):
        c.awards()


def test_responses_cached_for_24_hours(tmp_path):
    calls, now = [], [time.time()]

    def h(req):
        calls.append(1)
        return httpx.Response(200, json=one_page("MA000003"))

    c = MapdClient(api_key="k", transport=httpx.MockTransport(h), cache_dir=tmp_path, clock=lambda: now[0])
    c.awards()
    c.awards()
    assert len(calls) == 1
    now[0] += 25 * 3600   # cache file mtime is real time; jump the clock past the TTL
    c.awards()
    assert len(calls) == 2


def test_fast_food_pack_matches_mapd_names_and_levels():
    # Values and names exactly as returned by the live MAPD API on 2026-10-18.
    live = [
        MapdRate("Fast food employee level 1", 1, Decimal("27.81"), "2026-07-01", None),
        MapdRate("Fast food employee level 2", 2, Decimal("29.45"), "2026-07-01", None),
        MapdRate("Fast food employee level 3—in charge of one or no person", 3, Decimal("29.91"), "2026-07-01", None),
        MapdRate("Fast food employee level 3—in charge of 2 or more people", 4, Decimal("30.27"), "2026-07-01", None),
    ]
    assert cross_check(load_pack("MA000003"), live) == []


def test_penalties_filtered_server_side_and_paced():
    seen, waits = [], []

    def h(req):
        seen.append(dict(req.url.params))
        page = int(req.url.params["page"])
        return httpx.Response(200, json={"results": [{"rate": page}],
                                         "_meta": {"has_more_results": page < 3}})

    with MapdClient(api_key="k", transport=httpx.MockTransport(h), cache_dir=None, sleep=waits.append) as c:
        assert len(c.penalties("MA000009", rate_type_code="AD")) == 3
    assert all(p["employee_rate_type_code"] == "AD" for p in seen)
    assert waits == [0.5, 0.5]


def test_gateway_403_explains_what_to_do():
    def h(req):
        return httpx.Response(403, text="403: Access Denied")

    with MapdClient(api_key="k", transport=httpx.MockTransport(h), cache_dir=None) as c, \
            pytest.raises(MapdApiError, match="Wait a few minutes"):
        c.awards()
