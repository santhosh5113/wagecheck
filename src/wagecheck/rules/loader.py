"""Load the rule pack that was in force on a given date."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from importlib import resources

import yaml

from wagecheck.rules.schema import RulePack

_PACKAGE = "wagecheck.rules.packs"


@lru_cache(maxsize=32)
def _load_file(filename: str) -> RulePack:
    text = resources.files(_PACKAGE).joinpath(filename).read_text(encoding="utf-8")
    return RulePack.model_validate(yaml.safe_load(text))


def available_packs(instrument_id: str) -> list[tuple[date, str]]:
    out = []
    for entry in resources.files(_PACKAGE).iterdir():
        name = entry.name
        if name.startswith(f"{instrument_id}_") and name.endswith(".yaml"):
            out.append((date.fromisoformat(name[len(instrument_id) + 1 : -5]), name))
    return sorted(out)


def load_pack(instrument_id: str, on: date | None = None) -> RulePack:
    """Return the latest pack for `instrument_id` effective on or before `on`."""
    packs = available_packs(instrument_id)
    if not packs:
        raise LookupError(f"No rule pack for {instrument_id}")
    if on is not None:
        packs = [p for p in packs if p[0] <= on]
        if not packs:
            raise LookupError(f"No {instrument_id} rule pack in force on {on}")
    return _load_file(packs[-1][1])
