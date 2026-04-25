"""Filter the deduplicated listings DataFrame using user criteria from config.yaml."""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _to_int_safe(value) -> Optional[int]:
    if value is None:
        return None
    try:
        if isinstance(value, float) and pd.isna(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _matches_neighborhood(value: Any, allowed: Iterable[str]) -> bool:
    """Case-insensitive substring match against allowed neighborhoods.

    Pararius/Funda return slightly different labels (e.g. "Kralingen West"
    vs "Kralingen-Crooswijk") so substring matching is more robust than
    exact equality.
    """
    if not value or not isinstance(value, str):
        return False
    v = value.lower()
    return any(allowed_name.lower() in v for allowed_name in allowed)


def apply_filters(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """Apply user filters from config.yaml to the deduplicated DataFrame.

    Each filter is logged with its before/after row count so the user can
    see why listings were dropped.
    """
    if df is None or df.empty:
        return df

    work = df.copy()
    initial = len(work)

    price_cfg = config.get("price", {}) or {}
    max_price = price_cfg.get("max")
    min_price = price_cfg.get("min")
    if max_price is not None:
        before = len(work)
        prices = work["price"].apply(_to_int_safe)
        work = work[(prices.notna()) & (prices <= int(max_price))]
        logger.info("Filter price<=%s: %d -> %d", max_price, before, len(work))
    if min_price:
        before = len(work)
        prices = work["price"].apply(_to_int_safe)
        work = work[(prices.notna()) & (prices >= int(min_price))]
        logger.info("Filter price>=%s: %d -> %d", min_price, before, len(work))

    construction_cfg = config.get("construction", {}) or {}
    min_year = construction_cfg.get("min_year_built")
    include_unknown = bool(construction_cfg.get("include_unknown_year", True))
    if min_year is not None:
        before = len(work)
        years = work["year_built"].apply(_to_int_safe)
        if include_unknown:
            mask = years.isna() | (years >= int(min_year))
        else:
            mask = years.notna() & (years >= int(min_year))
        work = work[mask]
        logger.info(
            "Filter year_built>=%s (include_unknown=%s): %d -> %d",
            min_year,
            include_unknown,
            before,
            len(work),
        )

    rooms_cfg = config.get("rooms", {}) or {}
    min_bedrooms = rooms_cfg.get("min_bedrooms")
    if min_bedrooms is not None:
        before = len(work)
        beds = work["num_bedrooms"].apply(_to_int_safe)
        rooms = work["num_rooms"].apply(_to_int_safe)
        # Approx fallback: if bedrooms unknown but total rooms >= min_bedrooms+1, keep.
        # Most Dutch listings count "rooms" as living + bedrooms.
        mask = (
            (beds.notna() & (beds >= int(min_bedrooms)))
            | (beds.isna() & rooms.notna() & (rooms >= int(min_bedrooms) + 1))
            | (beds.isna() & rooms.isna())  # no info -> keep so user can decide
        )
        work = work[mask]
        logger.info(
            "Filter min_bedrooms>=%s (with room fallback): %d -> %d",
            min_bedrooms,
            before,
            len(work),
        )

    size_cfg = config.get("size", {}) or {}
    min_size = size_cfg.get("min_living_area_m2")
    if min_size:
        before = len(work)
        size = work["living_area_m2"].apply(_to_int_safe)
        work = work[size.notna() & (size >= int(min_size))]
        logger.info("Filter living_area>=%s: %d -> %d", min_size, before, len(work))

    neighborhoods = config.get("neighborhoods") or []
    if neighborhoods:
        before = len(work)
        mask = work["neighborhood"].apply(
            lambda v: _matches_neighborhood(v, neighborhoods)
        ) | work["neighborhood"].isna()
        work = work[mask]
        logger.info(
            "Filter neighborhood in %s (kept unknowns): %d -> %d",
            neighborhoods,
            before,
            len(work),
        )

    logger.info("Filters: %d total -> %d matching", initial, len(work))
    return work.reset_index(drop=True)
