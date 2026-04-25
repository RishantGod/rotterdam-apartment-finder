"""Pararius.nl scraper for koopwoningen (apartments/houses for sale).

Pararius is fronted by Cloudflare, so we use `curl_cffi` to impersonate a
real Chrome TLS fingerprint. Plain `httpx`/`requests` get blocked by the
JS challenge.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List, Optional
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup, Tag

from .funda_source import COMMON_COLUMNS, _to_int

logger = logging.getLogger(__name__)

BASE_URL = "https://www.pararius.nl"


def _build_search_url(area: str, max_price: int, page: int) -> str:
    """Build a Pararius koopwoningen search URL for the given area/price/page."""
    base = f"{BASE_URL}/koopwoningen/{area.lower()}/0-{max_price}"
    if page > 1:
        return f"{base}/page-{page}"
    return base


def _parse_subtitle(text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse a Pararius sub-title like '3078 BS Rotterdam (Groot IJsselmonde)'.

    Returns (zip_code, city, neighborhood).
    """
    if not text:
        return None, None, None
    text = text.strip()
    zip_match = re.match(r"^(\d{4}\s?[A-Z]{2})\s+(.+)$", text)
    if not zip_match:
        return None, None, text or None
    zip_code = zip_match.group(1).replace(" ", "")
    rest = zip_match.group(2)
    nb_match = re.match(r"^(.+?)\s*\((.+)\)\s*$", rest)
    if nb_match:
        return zip_code, nb_match.group(1).strip(), nb_match.group(2).strip()
    return zip_code, rest.strip(), None


def _extract_listing(card: Tag) -> Optional[dict]:
    """Extract one listing dict from a Pararius search-result card."""
    title_link = card.select_one("a.listing-search-item__link--title")
    if not title_link:
        return None

    href = title_link.get("href") or ""
    url = urljoin(BASE_URL, href)
    address = title_link.get_text(strip=True)

    sub_title = card.select_one(".listing-search-item__sub-title")
    zip_code, city, neighborhood = _parse_subtitle(
        sub_title.get_text(strip=True) if sub_title else ""
    )

    price_el = card.select_one(".listing-search-item__price-main") or card.select_one(
        ".listing-search-item__price"
    )
    price = _to_int(price_el.get_text(strip=True)) if price_el else None

    area_el = card.select_one(".illustrated-features__item--surface-area")
    living_area = _to_int(area_el.get_text(strip=True)) if area_el else None

    rooms_el = card.select_one(".illustrated-features__item--number-of-rooms")
    num_rooms = _to_int(rooms_el.get_text(strip=True)) if rooms_el else None

    year_el = card.select_one(".illustrated-features__item--construction-period")
    year_built = _to_int(year_el.get_text(strip=True)) if year_el else None

    img = card.select_one("img.picture__image")
    photo_url = img.get("src") if img else None

    return {
        "source": "pararius",
        "url": url,
        "address": address,
        "zip_code": zip_code,
        "city": city,
        "neighborhood": neighborhood,
        "price": price,
        "living_area_m2": living_area,
        "num_bedrooms": None,  # not on the search card; only num_rooms is shown
        "num_rooms": num_rooms,
        "year_built": year_built,
        "energy_label": None,
        "photo_url": photo_url,
        "description": None,
    }


def _fetch_html(url: str) -> Optional[str]:
    """Fetch a Pararius page using curl_cffi to bypass Cloudflare."""
    try:
        from curl_cffi import requests as cf_requests
    except Exception as exc:  # pragma: no cover
        logger.error("curl_cffi not installed: %s", exc)
        return None

    try:
        resp = cf_requests.get(
            url,
            impersonate="chrome120",
            timeout=30,
            allow_redirects=True,
        )
    except Exception as exc:
        logger.warning("Pararius fetch error for %s: %s", url, exc)
        return None

    if resp.status_code != 200:
        logger.warning("Pararius returned %s for %s", resp.status_code, url)
        return None
    return resp.text


def scrape_pararius(
    area: str,
    *,
    max_price: int = 600000,
    pages: int = 3,
    delay_seconds: float = 1.5,
) -> pd.DataFrame:
    """Scrape Pararius koopwoningen for the given area / max price.

    Returns a DataFrame in the common listings schema.
    """
    logger.info("Scraping Pararius area=%s pages=%d max_price=%d", area, pages, max_price)

    rows: List[dict] = []
    for page in range(1, pages + 1):
        url = _build_search_url(area, max_price, page)
        html = _fetch_html(url)
        if not html:
            logger.warning("Stopping Pararius scrape at page %d (fetch failed)", page)
            break

        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("section.listing-search-item")
        if not cards:
            logger.info("No more Pararius cards on page %d, stopping", page)
            break

        for card in cards:
            try:
                row = _extract_listing(card)
                if row:
                    rows.append(row)
            except Exception as exc:
                logger.debug("Skipping a Pararius card due to parse error: %s", exc)

        if page < pages:
            time.sleep(delay_seconds)

    if not rows:
        return pd.DataFrame(columns=COMMON_COLUMNS)

    df = pd.DataFrame(rows)
    for col in COMMON_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[COMMON_COLUMNS]
    logger.info("Pararius returned %d listings", len(df))
    return df
