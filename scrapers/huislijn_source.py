"""Huislijn.nl scraper.

Huislijn embeds full listing data as JSON inside Vue component props
(`<hl-search-object-display :object="{...}">`), so a single search-results
page request gives us everything we need (price, year built, rooms,
bedrooms, photo, lat/lon) with no detail-page fetch required.
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
import time
from typing import List, Optional
from urllib.parse import urljoin

import pandas as pd

from .funda_source import COMMON_COLUMNS, _to_int

logger = logging.getLogger(__name__)

BASE_URL = "https://www.huislijn.nl"


def _http_get(url: str) -> Optional[str]:
    try:
        from curl_cffi import requests as cf_requests
    except ImportError as exc:  # pragma: no cover
        logger.error("curl_cffi not installed: %s", exc)
        return None
    try:
        resp = cf_requests.get(
            url, impersonate="chrome120", timeout=30, allow_redirects=True
        )
    except Exception as exc:
        logger.warning("Huislijn fetch error for %s: %s", url, exc)
        return None
    if resp.status_code != 200:
        logger.warning("Huislijn returned %s for %s", resp.status_code, url)
        return None
    return resp.text


def _build_search_url(area: str, page: int) -> str:
    """Pagination requires the explicit offer-type and nieuwbouw params or
    the server quietly returns page 1 every time."""
    base = f"{BASE_URL}/koopwoning/nederland/zuid-holland/{area.lower()}"
    qs = "_searchOfferType=sale&c-nieuwbouw=alle"
    if page > 1:
        return f"{base}?page={page}&{qs}"
    return f"{base}?{qs}"


_OBJECT_PATTERN = re.compile(r':object="(\{[^"]+\})"')


def _extract_objects(html: str) -> List[dict]:
    """Pull out all Vue `:object="{...}"` JSON payloads from one page."""
    out = []
    for m in _OBJECT_PATTERN.finditer(html):
        try:
            decoded = _html.unescape(m.group(1))
            obj = json.loads(decoded)
            out.append(obj)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug("Huislijn: failed to parse object: %s", exc)
    return out


def _normalize_obj(obj: dict) -> Optional[dict]:
    """Map a Huislijn JSON object to the common listings schema."""
    props = obj.get("properties") or {}
    if not props.get("Buy", True):
        return None  # rentals slipped through

    street = (obj.get("street") or "").strip()
    housenum = (obj.get("housenumber") or "").strip()
    address = f"{street} {housenum}".strip() or None

    photo = None
    p = obj.get("photo") or {}
    formats = (p.get("formats") if isinstance(p, dict) else None) or {}
    photo = formats.get("m") or formats.get("normal") or formats.get("s")

    link = obj.get("link") or ""
    url = urljoin(BASE_URL, link) if link else None

    raw_price = obj.get("price")
    price: Optional[int] = None
    if raw_price is not None:
        try:
            price = int(float(raw_price))
        except (TypeError, ValueError):
            price = _to_int(raw_price)
    year_built = _to_int(props.get("Bouwjaar"))
    rooms = _to_int(props.get("TotAantalKamers"))
    beds = _to_int(props.get("AantalSlaapkamers"))
    living = _to_int(props.get("WoonOpp"))

    return {
        "source": "huislijn",
        "url": url,
        "address": address,
        "zip_code": (obj.get("zipcode") or "").replace(" ", "") or None,
        "city": obj.get("city"),
        "neighborhood": None,  # not in the search-page payload
        "price": price,
        "living_area_m2": living,
        "num_bedrooms": beds,
        "num_rooms": rooms,
        "year_built": year_built,
        "energy_label": None,  # not exposed in the search-page payload
        "photo_url": photo,
        "description": None,
    }


def scrape_huislijn(
    area: str,
    *,
    max_price: Optional[int] = 600000,
    pages: int = 0,
    delay_seconds: float = 0.6,
) -> pd.DataFrame:
    """Scrape Huislijn koopwoningen for the given area.

    `pages=0` means "scrape until we run out of new listings" (recommended).
    Filters by `max_price` client-side (the URL filter is unreliable).
    """
    logger.info(
        "Scraping Huislijn area=%s pages=%s max_price=%s",
        area,
        pages or "ALL",
        max_price,
    )

    rows: List[dict] = []
    seen_ids: set = set()
    page = 1
    consecutive_empty_or_dup_pages = 0
    max_pages = pages if pages > 0 else 200  # hard ceiling so we never loop forever

    while page <= max_pages:
        url = _build_search_url(area, page)
        html = _http_get(url)
        if not html:
            logger.warning("Huislijn: page %d fetch failed; stopping", page)
            break

        objs = _extract_objects(html)
        if not objs:
            logger.info("Huislijn: page %d had no listings, stopping", page)
            break

        new_count = 0
        for obj in objs:
            oid = obj.get("id")
            if oid in seen_ids:
                continue
            seen_ids.add(oid)
            row = _normalize_obj(obj)
            if not row:
                continue
            if max_price and row.get("price") and row["price"] > max_price:
                continue
            rows.append(row)
            new_count += 1

        logger.info("Huislijn: page %d -> %d objects, %d new", page, len(objs), new_count)
        if new_count == 0:
            consecutive_empty_or_dup_pages += 1
            if consecutive_empty_or_dup_pages >= 2:
                logger.info("Huislijn: 2 consecutive pages with no new listings, stopping")
                break
        else:
            consecutive_empty_or_dup_pages = 0

        page += 1
        time.sleep(delay_seconds)

    if not rows:
        return pd.DataFrame(columns=COMMON_COLUMNS)

    df = pd.DataFrame(rows)
    for col in COMMON_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[COMMON_COLUMNS]
    logger.info("Huislijn: returned %d listings (across %d pages)", len(df), page - 1)
    return df
