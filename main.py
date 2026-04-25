"""Rotterdam Apartment Finder - CLI entry point.

Reads `config.yaml`, runs the configured scrapers, deduplicates results,
applies filters, and writes a CSV + HTML report under `output/`.

Usage:
    python main.py                # uses ./config.yaml
    python main.py --config other.yaml
    python main.py --skip-funda   # only scrape Pararius
    python main.py --skip-pararius
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import yaml

from processing import apply_filters, deduplicate_listings
from reporting import generate_report
from scrapers import scrape_funda, scrape_pararius


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy third-party loggers.
    logging.getLogger("funda_scraper").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        sys.exit(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _run_scrapers(
    config: Dict[str, Any],
    skip_funda: bool,
    skip_pararius: bool,
) -> List[pd.DataFrame]:
    city = config.get("city", "rotterdam")
    max_price = (config.get("price") or {}).get("max")
    scrape_cfg = config.get("scrape") or {}
    frames: List[pd.DataFrame] = []

    funda_cfg = scrape_cfg.get("funda") or {}
    if not skip_funda and funda_cfg.get("enabled", True):
        df = scrape_funda(
            area=city,
            max_price=int(max_price) if max_price else None,
            pages=int(funda_cfg.get("pages", 5)),
        )
        frames.append(df)

    pararius_cfg = scrape_cfg.get("pararius") or {}
    if not skip_pararius and pararius_cfg.get("enabled", True):
        df = scrape_pararius(
            area=city,
            max_price=int(max_price) if max_price else 600000,
            pages=int(pararius_cfg.get("pages", 3)),
        )
        frames.append(df)

    return frames


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--skip-funda", action="store_true")
    parser.add_argument("--skip-pararius", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("apartment-finder")

    log.info("Loading config from %s", args.config)
    config = _load_config(args.config)

    log.info("Running scrapers...")
    frames = _run_scrapers(config, args.skip_funda, args.skip_pararius)

    total_raw = sum(len(f) for f in frames if f is not None)
    log.info("Scraped %d total raw listings across %d sources", total_raw, len(frames))
    if total_raw == 0:
        log.warning("No listings scraped from any source. Check network / scraper logs.")

    log.info("Deduplicating...")
    deduped = deduplicate_listings(frames)

    log.info("Applying filters from config...")
    filtered = apply_filters(deduped, config)

    output_cfg = config.get("output") or {}
    csv_path = Path(output_cfg.get("csv_path", "output/listings.csv"))
    report_path = Path(output_cfg.get("report_path", "output/report.html"))
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    if filtered is not None and not filtered.empty:
        filtered.to_csv(csv_path, index=False)
        log.info("Wrote %d filtered listings to %s", len(filtered), csv_path)
    else:
        log.warning("No listings matched filters; CSV not written.")

    generate_report(filtered, output_path=str(report_path), config=config)
    log.info("Done. Open %s in your browser.", report_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
