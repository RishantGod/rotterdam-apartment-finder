"""Rotterdam Apartment Finder - CLI entry point.

Reads `config.yaml`, runs the configured scrapers, accumulates results
into a persistent JSON database (`output/listings_db.json`) so listings
are NEVER lost between runs, deduplicates, applies filters, and writes
a CSV + HTML report under `output/`.

Usage:
    python main.py                        # one-shot, uses ./config.yaml
    python main.py --loop 30              # re-run every 30 minutes forever
    python main.py --loop 60 --max-runs 12  # 12 runs, one per hour
    python main.py --skip-funda           # skip a source
    python main.py -v                     # verbose logging
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import yaml

from processing import (
    apply_filters,
    database_to_dataframe,
    deduplicate_listings,
    load_database,
    merge_run_into_database,
    save_database,
)
from reporting import generate_report
from scrapers import scrape_funda, scrape_huislijn, scrape_pararius


_STOP = False


def _install_signal_handlers() -> None:
    def _handler(signum, frame):
        global _STOP
        _STOP = True
        logging.getLogger("apartment-finder").info(
            "Received signal %s; will stop after current run", signum
        )

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _setup_logging(verbose: bool, log_file: Path | None = None) -> None:
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        sys.exit(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _pages_arg(cfg_pages) -> int:
    """A scrape config of `pages: 0` means 'scrape all available'."""
    if cfg_pages is None:
        return 5
    n = int(cfg_pages)
    return 0 if n <= 0 else n


def _run_scrapers(
    config: Dict[str, Any],
    skip_funda: bool,
    skip_pararius: bool,
    skip_huislijn: bool,
) -> List[pd.DataFrame]:
    city = config.get("city", "rotterdam")
    max_price = (config.get("price") or {}).get("max")
    scrape_cfg = config.get("scrape") or {}
    frames: List[pd.DataFrame] = []

    funda_cfg = scrape_cfg.get("funda") or {}
    if not skip_funda and funda_cfg.get("enabled", True):
        pages = _pages_arg(funda_cfg.get("pages", 5))
        frames.append(
            scrape_funda(
                area=city,
                max_price=int(max_price) if max_price else None,
                pages=pages or 50,  # Funda hard cap: 50 pages = ~750 listings
            )
        )

    pararius_cfg = scrape_cfg.get("pararius") or {}
    if not skip_pararius and pararius_cfg.get("enabled", True):
        pages = _pages_arg(pararius_cfg.get("pages", 3))
        frames.append(
            scrape_pararius(
                area=city,
                max_price=int(max_price) if max_price else 600000,
                pages=pages or 30,
                fetch_details=bool(pararius_cfg.get("fetch_details", True)),
            )
        )

    huislijn_cfg = scrape_cfg.get("huislijn") or {}
    if not skip_huislijn and huislijn_cfg.get("enabled", True):
        pages = _pages_arg(huislijn_cfg.get("pages", 0))
        frames.append(
            scrape_huislijn(
                area=city,
                max_price=int(max_price) if max_price else 600000,
                pages=pages,
            )
        )

    return frames


def _run_one_pass(config: Dict[str, Any], args: argparse.Namespace) -> int:
    """Run one full scrape -> dedupe -> merge-to-DB -> filter -> report cycle.

    Returns the number of listings matching the user's filters in this pass.
    """
    log = logging.getLogger("apartment-finder")
    output_cfg = config.get("output") or {}
    csv_path = Path(output_cfg.get("csv_path", "output/listings.csv"))
    report_path = Path(output_cfg.get("report_path", "output/report.html"))
    db_path = Path(output_cfg.get("db_path", "output/listings_db.json"))
    all_csv_path = Path(output_cfg.get("all_csv_path", "output/all_listings.csv"))
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    log.info("Running scrapers...")
    frames = _run_scrapers(config, args.skip_funda, args.skip_pararius, args.skip_huislijn)

    raw_total = sum(len(f) for f in frames if f is not None)
    log.info("Scraped %d total raw listings across %d sources", raw_total, len(frames))

    log.info("Deduplicating in-memory before DB merge...")
    deduped_run = deduplicate_listings(frames)

    log.info("Loading persistent DB from %s...", db_path)
    db = load_database(db_path)
    log.info("DB had %d listings before merge", len(db))
    db = merge_run_into_database(db, deduped_run)
    log.info("DB has %d listings after merge", len(db))
    save_database(db_path, db)

    full = database_to_dataframe(db)
    if full is not None and not full.empty:
        full.to_csv(all_csv_path, index=False)
        log.info("Wrote full DB (%d listings) to %s", len(full), all_csv_path)

    log.info("Applying user filters from config...")
    filtered = apply_filters(full, config)

    if filtered is not None and not filtered.empty:
        filtered.to_csv(csv_path, index=False)
        log.info("Wrote %d filtered listings to %s", len(filtered), csv_path)
    else:
        log.warning("No listings matched filters; CSV not written.")
        if csv_path.exists():
            csv_path.unlink()

    generate_report(filtered, output_path=str(report_path), config=config)
    n_matches = 0 if filtered is None else len(filtered)
    log.info("Pass complete: %d listings match all filters.", n_matches)
    return n_matches


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--skip-funda", action="store_true")
    parser.add_argument("--skip-pararius", action="store_true")
    parser.add_argument("--skip-huislijn", action="store_true")
    parser.add_argument(
        "--loop",
        type=int,
        metavar="MINUTES",
        help="Run continuously, sleeping MINUTES between passes. Press Ctrl-C to stop.",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="When --loop is set, stop after this many passes (default: forever).",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path("output/runs.log"),
        help="File to also write logs to (default: output/runs.log).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _install_signal_handlers()
    _setup_logging(args.verbose, args.log_file)
    log = logging.getLogger("apartment-finder")

    log.info("Loading config from %s", args.config)
    config = _load_config(args.config)

    if not args.loop:
        n = _run_one_pass(config, args)
        log.info("Done. Open %s in your browser.", Path(config.get("output", {}).get("report_path", "output/report.html")).resolve())
        return 0

    interval_seconds = max(60, args.loop * 60)
    log.info(
        "Loop mode: running every %d minutes (max %s passes). Send SIGINT to stop.",
        args.loop,
        args.max_runs if args.max_runs else "infinite",
    )

    pass_idx = 0
    while not _STOP:
        pass_idx += 1
        log.info("=== PASS %d START ===", pass_idx)
        try:
            _run_one_pass(config, args)
        except Exception as exc:  # don't let one failure kill the loop
            log.exception("Pass %d failed: %s", pass_idx, exc)

        if args.max_runs and pass_idx >= args.max_runs:
            log.info("Reached max_runs=%d; exiting.", args.max_runs)
            break

        if _STOP:
            break

        log.info("=== PASS %d DONE; sleeping %d minutes ===", pass_idx, args.loop)
        # Sleep in 5s slices so Ctrl-C is responsive
        slept = 0
        while slept < interval_seconds and not _STOP:
            time.sleep(min(5, interval_seconds - slept))
            slept += 5

    log.info("Loop terminated after %d passes.", pass_idx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
