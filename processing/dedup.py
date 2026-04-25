"""Deduplicate listings collected across multiple real estate sources.

Same physical apartment often appears on Funda and Pararius (and elsewhere)
with slightly different formatting. We normalize the address + zip code,
group by that key, and merge each group into a single row that records all
sources it appeared on.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, List

import pandas as pd

logger = logging.getLogger(__name__)


# Common Dutch street suffixes mapped to a canonical form. Helps match
# "Kerkstr." with "Kerkstraat", etc.
_STREET_SUFFIX_MAP = {
    r"\bstr\.?\b": "straat",
    r"\bln\.?\b": "laan",
    r"\bwg\.?\b": "weg",
    r"\bpl\.?\b": "plein",
    r"\bdk\.?\b": "dijk",
    r"\bpk\.?\b": "park",
    r"\bkd\.?\b": "kade",
}


def _normalize_address(address: str) -> str:
    """Lowercase, collapse whitespace, expand street suffixes, strip punctuation."""
    if not isinstance(address, str):
        return ""
    s = address.lower().strip()
    s = re.sub(r"[,;]", " ", s)
    s = re.sub(r"\s+", " ", s)
    for pattern, replacement in _STREET_SUFFIX_MAP.items():
        s = re.sub(pattern, replacement, s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _normalize_zip(zip_code) -> str:
    """Keep only digits + letters of a Dutch postal code (e.g. '3078BS')."""
    if not isinstance(zip_code, str):
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", zip_code).upper()


def _make_key(row: pd.Series) -> str:
    """Build a dedup key from normalized address + zip prefix."""
    addr = _normalize_address(row.get("address", ""))
    zip_clean = _normalize_zip(row.get("zip_code", ""))
    if zip_clean:
        return f"{addr}|{zip_clean[:6]}"
    return addr


def _coalesce(values: Iterable):
    """Return first non-null/non-empty value from an iterable."""
    for v in values:
        if v is None:
            continue
        if isinstance(v, float) and pd.isna(v):
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _merge_group(group: pd.DataFrame) -> pd.Series:
    """Merge a group of duplicate listings into a single row."""
    sources = sorted({s for s in group["source"].dropna().unique() if s})

    merged = {
        "source": ",".join(sources) if sources else None,
        "sources_count": len(sources),
        "url": _coalesce(group["url"]),
        "all_urls": ";".join(sorted({u for u in group["url"].dropna().unique() if u})),
        "address": _coalesce(group["address"]),
        "zip_code": _coalesce(group["zip_code"]),
        "city": _coalesce(group["city"]),
        "neighborhood": _coalesce(group["neighborhood"]),
        "price": _coalesce(group["price"]),
        "living_area_m2": _coalesce(group["living_area_m2"]),
        "num_bedrooms": _coalesce(group["num_bedrooms"]),
        "num_rooms": _coalesce(group["num_rooms"]),
        "year_built": _coalesce(group["year_built"]),
        "energy_label": _coalesce(group["energy_label"]),
        "photo_url": _coalesce(group["photo_url"]),
        "description": _coalesce(group["description"]),
    }
    return pd.Series(merged)


def deduplicate_listings(frames: List[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate listings from multiple sources and deduplicate them."""
    non_empty = [f for f in frames if f is not None and not f.empty]
    if not non_empty:
        return pd.DataFrame()

    combined = pd.concat(non_empty, ignore_index=True)
    combined["_dedup_key"] = combined.apply(_make_key, axis=1)

    before = len(combined)
    merged = (
        combined.groupby("_dedup_key", dropna=False, group_keys=False)
        .apply(_merge_group, include_groups=False)
        .reset_index(drop=True)
    )
    after = len(merged)
    logger.info(
        "Deduplicated %d raw listings -> %d unique (removed %d duplicates)",
        before,
        after,
        before - after,
    )
    return merged
