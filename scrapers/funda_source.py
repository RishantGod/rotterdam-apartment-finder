"""Funda.nl scraper.

The third-party `funda-scraper` package (and plain `requests`) get blocked
by Funda's bot protection, so we use `curl_cffi` to impersonate Chrome's
TLS fingerprint. The flow is:

1. Hit the search results page, extract listing URLs from the JSON-LD
   `itemListElement` block (resilient to layout changes).
2. For each listing URL, fetch the detail page and parse the address,
   postal code, price (from JSON-LD), plus year built / rooms / m² / energy
   label / neighborhood from the page HTML using narrow regexes.

Personal-use scraping only, per Funda's Terms and Conditions.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import List, Optional

import pandas as pd
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


COMMON_COLUMNS = [
    "source",
    "url",
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
]


def _to_int(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not pd.isna(value):
        return int(value)
    if not isinstance(value, str):
        return None
    digits = re.sub(r"[^0-9]", "", value)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _http_get(url: str) -> Optional[str]:
    """Fetch a URL using curl_cffi (Chrome TLS impersonation)."""
    try:
        from curl_cffi import requests as cf_requests
    except ImportError as exc:  # pragma: no cover
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
        logger.warning("Funda fetch error for %s: %s", url, exc)
        return None
    if resp.status_code != 200:
        logger.warning("Funda returned %s for %s", resp.status_code, url)
        return None
    return resp.text


def _build_search_url(area: str, max_price: Optional[int], page: int) -> str:
    base = (
        f"https://www.funda.nl/zoeken/koop?selected_area=%5B%22{area.lower()}%22%5D"
    )
    if max_price:
        base += f'&price=%22-{int(max_price)}%22'
    if page > 1:
        base += f"&search_result={page}"
    return base


def _extract_listing_urls(html: str) -> List[str]:
    """Pull listing URLs out of the JSON-LD `itemListElement` block."""
    soup = BeautifulSoup(html, "lxml")
    urls: List[str] = []
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.text or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        items = data.get("itemListElement")
        if isinstance(items, list):
            for it in items:
                u = it.get("url") if isinstance(it, dict) else None
                if isinstance(u, str) and "/detail/koop/" in u:
                    urls.append(u)
    seen, deduped = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _parse_detail(html: str, url: str) -> Optional[dict]:
    """Extract listing fields from a Funda detail page's HTML."""
    soup = BeautifulSoup(html, "lxml")

    # Pull the JSON-LD product block (gives address, price, photos).
    address = None
    city = None
    region = None
    price = None
    photo = None
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.text or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        types = data.get("@type")
        if (
            (isinstance(types, list) and "Product" in types)
            or types == "Product"
            or "Huis" in (types if isinstance(types, list) else [types])
            or "Appartement" in (types if isinstance(types, list) else [types])
        ):
            addr = data.get("address") or {}
            if isinstance(addr, dict):
                address = address or addr.get("streetAddress")
                city = city or addr.get("addressLocality")
                region = region or addr.get("addressRegion")
            offers = data.get("offers") or {}
            if isinstance(offers, dict):
                price = price or _to_int(offers.get("price"))
            photo = photo or data.get("image")
            if isinstance(photo, list) and photo:
                photo = photo[0]

    text = html  # raw text fallback for regex-based fields

    # Postal code is on a custom element attribute: `postcode="3068SM"`.
    zip_code = None
    pc = re.search(r'postcode="(\d{4}\s?[A-Z]{2})"', text)
    if pc:
        zip_code = pc.group(1).replace(" ", "")

    # Year built: "Bouwjaar 1972" or in <dd> after Bouwjaar label.
    year_built = None
    yb = re.search(r"Bouwjaar[^0-9]{0,30}((?:19|20)\d{2})", text)
    if yb:
        year_built = int(yb.group(1))

    # Number of rooms / bedrooms: "6 kamers (5 slaapkamers)"
    num_rooms = None
    num_bedrooms = None
    rk = re.search(r"(\d+)\s*kamers?\s*\((\d+)\s*slaapkamer", text, re.IGNORECASE)
    if rk:
        num_rooms = int(rk.group(1))
        num_bedrooms = int(rk.group(2))
    else:
        rk2 = re.search(r"(\d+)\s*kamers", text, re.IGNORECASE)
        if rk2:
            num_rooms = int(rk2.group(1))

    # Living area: look in the description text for "wonen" m² or in features.
    living_area = None
    la = re.search(r"(\d{2,4})\s*m[²2]\s*</span>\s*<span[^>]*>\s*wonen", text, re.IGNORECASE)
    if la:
        living_area = int(la.group(1))
    else:
        la2 = re.search(r"woonoppervlakte\D{0,30}(\d{2,4})\s*m", text, re.IGNORECASE)
        if la2:
            living_area = int(la2.group(1))

    # Energy label: pattern is `<span class="md:font-bold">B</span><span ...>energielabel</span>`.
    energy_label = None
    el = re.search(
        r'<span[^>]*font-bold[^>]*>([A-G][+]{0,4})</span>\s*<span[^>]*>\s*energielabel',
        text,
        re.IGNORECASE,
    )
    if el:
        energy_label = el.group(1)
    else:
        el2 = re.search(r"Energielabel[^<]*</dt>\s*<dd[^>]*>\s*<span[^>]*>\s*([A-G][+]{0,4})", text)
        if el2:
            energy_label = el2.group(1)

    # Neighborhood: from BreadcrumbList JSON-LD (last non-listing crumb).
    neighborhood = None
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.text or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "BreadcrumbList":
            items = data.get("itemListElement") or []
            crumbs = []
            for it in items:
                if isinstance(it, dict):
                    nested = it.get("item") or {}
                    name = nested.get("name") if isinstance(nested, dict) else None
                    if name and name.lower() not in {"home", "rotterdam", "amsterdam", "utrecht", "den haag"}:
                        crumbs.append(name)
            if len(crumbs) >= 2:
                # Last crumb is the listing's own name; the one before is the neighborhood.
                neighborhood = crumbs[-2]
            elif len(crumbs) == 1:
                neighborhood = crumbs[0]
            break

    if not address and not price:
        return None

    return {
        "source": "funda",
        "url": url,
        "address": address,
        "zip_code": zip_code,
        "city": city or "Rotterdam",
        "neighborhood": neighborhood,
        "price": price,
        "living_area_m2": living_area,
        "num_bedrooms": num_bedrooms,
        "num_rooms": num_rooms,
        "year_built": year_built,
        "energy_label": energy_label,
        "photo_url": photo if isinstance(photo, str) else None,
        "description": None,
    }


def scrape_funda(
    area: str,
    *,
    max_price: Optional[int] = None,
    min_price: Optional[int] = None,  # accepted for API compatibility, not currently used
    pages: int = 5,
    page_start: int = 1,
    delay_seconds: float = 0.4,
) -> pd.DataFrame:
    """Scrape Funda for `koop` (buy) listings in the given area.

    Returns a DataFrame in the common listings schema.
    """
    logger.info(
        "Scraping Funda area=%s pages=%d max_price=%s",
        area,
        pages,
        max_price,
    )

    all_urls: List[str] = []
    for page in range(page_start, page_start + pages):
        search_url = _build_search_url(area, max_price, page)
        html = _http_get(search_url)
        if not html:
            logger.warning("Funda: search page %d fetch failed; stopping", page)
            break
        urls = _extract_listing_urls(html)
        if not urls:
            logger.info("Funda: no listings on page %d, stopping", page)
            break
        all_urls.extend(urls)
        if page < page_start + pages - 1:
            time.sleep(delay_seconds)

    seen, unique_urls = set(), []
    for u in all_urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    logger.info("Funda: collected %d unique listing URLs, fetching details...", len(unique_urls))

    rows: List[dict] = []
    for i, url in enumerate(unique_urls, start=1):
        html = _http_get(url)
        if not html:
            continue
        try:
            row = _parse_detail(html, url)
            if row:
                rows.append(row)
        except Exception as exc:
            logger.debug("Funda: failed to parse %s: %s", url, exc)
        if i % 25 == 0:
            logger.info("Funda: parsed %d/%d listings...", i, len(unique_urls))
        time.sleep(delay_seconds)

    if not rows:
        return pd.DataFrame(columns=COMMON_COLUMNS)

    df = pd.DataFrame(rows)
    for col in COMMON_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[COMMON_COLUMNS]
    logger.info("Funda: returned %d listings", len(df))
    return df
