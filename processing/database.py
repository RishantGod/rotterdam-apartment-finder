"""Persistent listings database.

Accumulates listings across multiple scrape runs. Stored as JSON for
human-readability and easy re-processing.

Each entry is keyed by the dedup key (normalized address + zip prefix)
and tracks:
- when first seen
- when last seen
- which sources have shown it (with all URLs)
- the most complete merged data
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .dedup import _make_key

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _coalesce(*values):
    for v in values:
        if v is None:
            continue
        if isinstance(v, float) and pd.isna(v):
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _safe_value(v):
    """Convert pandas NaN to None for clean JSON serialization."""
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            return v
    return v


def load_database(path: Path) -> Dict[str, dict]:
    """Load the persistent DB from disk. Returns empty dict if missing."""
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load DB %s: %s; starting fresh", path, exc)
        return {}


def save_database(path: Path, db: Dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False, default=str)


def merge_run_into_database(
    db: Dict[str, dict], run_df: pd.DataFrame
) -> Dict[str, dict]:
    """Merge a fresh scrape run into the persistent DB.

    For each new listing:
    - if not in DB, add it with first_seen/last_seen = now
    - if already in DB, update last_seen, merge any newly-discovered fields,
      and union the source/url sets
    """
    if run_df is None or run_df.empty:
        return db

    now = _now_iso()
    seen_count = 0
    new_count = 0
    updated_count = 0

    for _, row in run_df.iterrows():
        seen_count += 1
        key = _make_key(row)
        if not key:
            continue

        new_source = _safe_value(row.get("source"))
        new_url = _safe_value(row.get("url"))

        if key in db:
            entry = db[key]
            entry["last_seen"] = now
            entry["seen_count"] = entry.get("seen_count", 1) + 1

            # Union sources & urls
            if new_source:
                sources = set(entry.get("sources") or [])
                # If source is comma-separated, split
                for s in str(new_source).split(","):
                    s = s.strip()
                    if s:
                        sources.add(s)
                entry["sources"] = sorted(sources)
            if new_url:
                urls = set(entry.get("all_urls") or [])
                urls.add(new_url)
                entry["all_urls"] = sorted(urls)

            # Coalesce missing fields
            for field in [
                "address",
                "zip_code",
                "city",
                "neighborhood",
                "price",
                "living_area_m2",
                "num_bedrooms",
                "num_rooms",
                "year_built",
                "energy_label",
                "photo_url",
                "description",
            ]:
                cur = entry.get(field)
                incoming = _safe_value(row.get(field))
                if cur in (None, "", 0) and incoming not in (None, "", 0):
                    entry[field] = incoming
                    updated_count += 1
                elif field == "price" and incoming and cur and incoming != cur:
                    # Track price changes
                    history = entry.setdefault("price_history", [])
                    if not history or history[-1].get("price") != incoming:
                        history.append({"price": incoming, "seen_at": now})
                    entry["price"] = incoming
        else:
            entry = {
                "key": key,
                "first_seen": now,
                "last_seen": now,
                "seen_count": 1,
                "sources": sorted({s.strip() for s in str(new_source or "").split(",") if s.strip()}),
                "all_urls": [new_url] if new_url else [],
            }
            for field in [
                "address",
                "zip_code",
                "city",
                "neighborhood",
                "price",
                "living_area_m2",
                "num_bedrooms",
                "num_rooms",
                "year_built",
                "energy_label",
                "photo_url",
                "description",
            ]:
                entry[field] = _safe_value(row.get(field))
            db[key] = entry
            new_count += 1

    logger.info(
        "DB merge: processed %d incoming, %d new, %d had fields filled in",
        seen_count,
        new_count,
        updated_count,
    )
    return db


def database_to_dataframe(db: Dict[str, dict]) -> pd.DataFrame:
    """Project the DB back to a DataFrame in the common listings schema
    (with extra `first_seen`, `last_seen`, `seen_count` columns)."""
    if not db:
        return pd.DataFrame()
    rows: List[Dict[str, Any]] = []
    for entry in db.values():
        sources = entry.get("sources") or []
        urls = entry.get("all_urls") or []
        rows.append(
            {
                "source": ",".join(sources),
                "sources_count": len(sources),
                "url": urls[0] if urls else None,
                "all_urls": ";".join(urls),
                "address": entry.get("address"),
                "zip_code": entry.get("zip_code"),
                "city": entry.get("city"),
                "neighborhood": entry.get("neighborhood"),
                "price": entry.get("price"),
                "living_area_m2": entry.get("living_area_m2"),
                "num_bedrooms": entry.get("num_bedrooms"),
                "num_rooms": entry.get("num_rooms"),
                "year_built": entry.get("year_built"),
                "energy_label": entry.get("energy_label"),
                "photo_url": entry.get("photo_url"),
                "description": entry.get("description"),
                "first_seen": entry.get("first_seen"),
                "last_seen": entry.get("last_seen"),
                "seen_count": entry.get("seen_count", 1),
            }
        )
    return pd.DataFrame(rows)
