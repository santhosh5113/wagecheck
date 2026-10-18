"""Thin client for the Fair Work Commission Modern Awards Pay Database (MAPD) API.

Used to cross-check (and later auto-refresh) the hand-verified rule packs. It is a
validator, not the source of truth for rules: MAPD gives dollar rates, but not the
conditions (span of hours, shiftwork definition, overtime triggers) — those come
from the award text.

Access: register at https://developer.fwc.gov.au, subscribe to the MAPD API, and
export the subscription key as FWC_API_KEY (never commit it).

Follows the FWC "MAPD API Integration Best Practices Guide" (11 May 2026):
- key sent in the Ocp-Apim-Subscription-Key header on every call;
- responses cached locally for 24 hours (the guide asks for at least 24h for repeated calls);
- HTTP 429 (throttled) and 5xx retried with backoff; other errors raised with the API's
  standard error object (errors[].id/code/detail);
- paging via _meta.has_more_results / page_count.
Webhooks (the guide's preferred change feed) need a public HTTPS callback, so a laptop
tool uses the cache plus `sort=last_modified_datetime desc` refreshes instead.
Status page: https://api.fwc.gov.au/status

Routes and field names match the published OpenAPI 3.0.1 spec (fwc-maapi-v1). Awards can be
addressed by code (MA000004) or award_fixed_id. Pay-rate and penalty endpoints return every
historical version, so callers select the record in force on a date.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from wagecheck.rules.schema import RulePack

DEFAULT_BASE_URL = "https://api.fwc.gov.au/api/v1"


CACHE_TTL_S = 24 * 3600
RETRY_STATUSES = {429, 500, 502, 503, 504}


class MapdConfigError(RuntimeError):
    pass


class MapdApiError(RuntimeError):
    """Non-retryable API error, carrying the API's standard error object."""

    def __init__(self, status: int, errors: list[dict[str, Any]], body: str = "") -> None:
        self.status, self.errors = status, errors
        detail = "; ".join(f"{e.get('code', '')}: {e.get('detail', '')}" for e in errors) or body[:200] or "no detail"
        hint = ""
        if status == 401:
            hint = " Check FWC_API_KEY is set and the subscription is Active."
        elif status == 403:
            hint = (" The gateway refused the request (often after many calls in a short time). Wait a few"
                    " minutes, check https://api.fwc.gov.au/status, then retry: pages already fetched are cached.")
        super().__init__(f"MAPD API HTTP {status}: {detail}.{hint}")


class MapdClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
        cache_dir: Path | None = Path("data/mapd_cache"),
        max_retries: int = 4,
        page_pause_s: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries, self._sleep, self._clock = max_retries, sleep, clock
        self.page_pause_s = page_pause_s
        key = api_key or os.environ.get("FWC_API_KEY")
        if not key:
            raise MapdConfigError("Set FWC_API_KEY (subscription key from developer.fwc.gov.au)")
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Ocp-Apim-Subscription-Key": key, "Accept": "application/json"},
            transport=transport,
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> MapdClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _cache_file(self, path: str, params: dict[str, Any]) -> Path | None:
        if not self.cache_dir:
            return None
        key = hashlib.sha1(json.dumps([path, sorted(params.items())], default=str).encode()).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        cf = self._cache_file(path, params)
        if cf and cf.exists() and self._clock() - cf.stat().st_mtime < CACHE_TTL_S:
            return json.loads(cf.read_text())
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            resp = self._http.get(path, params=params)
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                retry_after = resp.headers.get("retry-after", "")
                self._sleep(float(retry_after) if retry_after.isdigit() else delay)
                delay *= 2
                continue
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                    errors = body.get("errors", []) if isinstance(body, dict) else []
                except ValueError:
                    errors = []
                raise MapdApiError(resp.status_code, errors, resp.text)
            data = resp.json()
            if cf:
                cf.write_text(json.dumps(data))
            return data
        raise AssertionError("unreachable")

    def _get_all(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = {"limit": 100, **(params or {})}
        page, out = 1, list[dict[str, Any]]()
        while True:
            data = self._get_json(path, {**params, "page": page})
            if isinstance(data, list):
                return out + data
            out.extend(data.get("results", []))
            # PagedResultMetaModel: current_page, page_count, limit, result_count, has_more_results, ...
            meta = data.get("_meta", {}) or {}
            more = meta.get("has_more_results")
            if more is None:
                more = page < int(meta.get("page_count", 1) or 1)
            if not more:
                return out
            page += 1
            self._sleep(self.page_pause_s)  # pace multi-page downloads

    def awards(self, name: str | None = None) -> list[dict[str, Any]]:
        """GET /awards. `name` matches title words; join keywords with '+' to AND them."""
        return self._get_all("/awards", {"name": name} if name else None)

    def pay_rates(self, award_code: str, operative_from: date | None = None) -> list[dict[str, Any]]:
        params = {"operative_from": operative_from.isoformat()} if operative_from else None
        return self._get_all(f"/awards/{award_code}/pay-rates", params)

    def classifications(self, award_code: str) -> list[dict[str, Any]]:
        return self._get_all(f"/awards/{award_code}/classifications")

    def wage_allowances(self, award_code: str) -> list[dict[str, Any]]:
        """Wage-related allowances, including flat $/hour loadings some awards pay for late work."""
        return self._get_all(f"/awards/{award_code}/wage-allowances")

    def expense_allowances(self, award_code: str) -> list[dict[str, Any]]:
        return self._get_all(f"/awards/{award_code}/expense-allowances")

    def penalties(self, award_code: str, classification_level: int | None = None,
                  rate_type_code: str | None = None) -> list[dict[str, Any]]:
        """Filter on the server: large awards have thousands of penalty rows across ages and grades."""
        params: dict[str, Any] = {}
        if classification_level:
            params["classification_level"] = classification_level
        if rate_type_code:
            params["employee_rate_type_code"] = rate_type_code
        return self._get_all(f"/awards/{award_code}/penalties", params)


@dataclass(frozen=True)
class MapdRate:
    classification: str
    level: int | None
    rate: Decimal
    operative_from: str | None
    operative_to: str | None
    parent: str | None = None  # parent_classification_name: separates streams (e.g. casino gaming)
    fixed_id: int | None = None  # classification_fixed_id: stable across years


def _in_force(rec: dict[str, Any], on: date) -> bool:
    """MAPD returns every historical rate; keep the one operative on `on`."""
    start = (rec.get("operative_from") or "")[:10]
    end = (rec.get("operative_to") or "")[:10]
    return (not start or start <= on.isoformat()) and (not end or on.isoformat() <= end)


def norm_classification(name: str) -> str:
    return " ".join(name.lower().replace("-", " ").split())


def current_hourly_rates(records: list[dict[str, Any]], on: date, rate_type_code: str = "AD") -> list[MapdRate]:
    """Hourly calculated rates in force on `on` for one employee rate type.

    `rate_type_code` defaults to "AD" (adult). The spec only documents the codes on the portal's
    Documentation Guide, so confirm it with `wagecheck mapd check`, which prints the codes returned.
    """
    out = []
    for r in records:
        if r.get("employee_rate_type_code") != rate_type_code:
            continue
        if str(r.get("calculated_rate_type") or "").lower() != "hourly" or r.get("calculated_rate") is None:
            continue
        if not _in_force(r, on):
            continue
        out.append(MapdRate(str(r.get("classification") or ""), r.get("classification_level"),
                            Decimal(str(r["calculated_rate"])), r.get("operative_from"), r.get("operative_to"),
                            r.get("parent_classification_name"), r.get("classification_fixed_id")))
    return out


def cross_check(pack: RulePack, rates: list[MapdRate]) -> list[str]:
    """Differences between a rule pack's hourly rates and MAPD's, matched by classification
    name when the pack gives `mapd_name`, otherwise by `mapd_level` (must be unambiguous)."""
    diffs = []
    for name, c in pack.classifications.items():
        if c.mapd_fixed_id is not None:
            hits = [r for r in rates if r.fixed_id == c.mapd_fixed_id]
        elif c.mapd_name:
            hits = [r for r in rates if norm_classification(r.classification) == norm_classification(c.mapd_name)]
        elif c.mapd_level is not None:
            hits = [r for r in rates if r.level == c.mapd_level]
        else:
            diffs.append(f"{name}: no mapd_name or mapd_level set, cannot cross-check")
            continue
        if c.mapd_parent and c.mapd_fixed_id is None:
            hits = [r for r in hits if norm_classification(r.parent or "") == norm_classification(c.mapd_parent)]
        if not hits:
            diffs.append(f"{name}: missing from MAPD response")
        elif len({h.rate for h in hits}) > 1:
            diffs.append(f"{name}: ambiguous, MAPD has {len(hits)} matches "
                         f"({'; '.join(f'{h.classification} [{h.parent}] {h.rate}' for h in hits)}); "
                         "set mapd_name / mapd_parent")
        elif hits[0].rate != c.hourly:
            diffs.append(f"{name}: pack {c.hourly} != MAPD {hits[0].rate}")
    return diffs


def current_penalties(records: list[dict[str, Any]], on: date, level: int | None = None,
                      rate_type_code: str | None = None) -> list[dict[str, Any]]:
    """Penalty rows in force on `on`: description, % rate and the calculated dollar value."""
    rows = []
    for r in records:
        if not _in_force(r, on):
            continue
        if level is not None and r.get("classification_level") != level:
            continue
        if rate_type_code is not None and r.get("employee_rate_type_code") != rate_type_code:
            continue
        rows.append({k: r.get(k) for k in ("classification_level", "employee_rate_type_code", "clause_description",
                                           "penalty_description", "rate", "penalty_calculated_value")})
    return rows


def current_allowances(records: list[dict[str, Any]], on: date) -> list[dict[str, Any]]:
    keys = ("allowance", "parent_allowance", "clauses", "rate", "rate_unit", "allowance_amount",
            "payment_frequency", "is_all_purpose")
    return [{k: r.get(k) for k in keys} for r in records if _in_force(r, on)]
