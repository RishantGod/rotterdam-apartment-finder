# Rotterdam Apartment Finder

A small Python CLI that scrapes multiple Dutch real estate websites (Funda
and Pararius), deduplicates listings, filters by your personal criteria,
and generates a clean HTML report you can open in a browser.

Built for the use-case of finding a 2+ bedroom apartment to **buy** in
Rotterdam under EUR 600k, in newer construction (2005+), in specific
neighborhoods.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
open output/report.html
```

## How it works

```
config.yaml -> main.py -> [Funda scraper, Pararius scraper]
                       -> deduplicate by normalized address + zip
                       -> apply filters (price, year, rooms, neighborhood)
                       -> output/listings.csv + output/report.html
```

- **Funda** is scraped via the open-source `funda-scraper` Python package.
- **Pararius** is scraped directly using `curl_cffi` (impersonates Chrome's
  TLS fingerprint to bypass Cloudflare's JS challenge) plus BeautifulSoup.
- Listings appearing on both sites are merged into one row that records
  both source URLs, so you can compare prices/photos across them.

## Customizing your search

Edit [config.yaml](config.yaml). The defaults are:

- **City**: Rotterdam
- **Max price**: EUR 600,000
- **Min bedrooms**: 2
- **Min year built**: 2005 (listings with unknown year are kept so you can
  inspect them manually)
- **Neighborhoods**: Centrum, Kralingen, Crooswijk, Charlois, Katendrecht,
  Kop van Zuid

CLI flags:

```bash
python main.py --skip-funda          # Pararius only
python main.py --skip-pararius       # Funda only
python main.py --config other.yaml   # Use a different config
python main.py -v                    # Verbose logging
```

## Output

- `output/listings.csv` - all matching deduplicated listings
- `output/report.html` - sortable, filterable HTML report with photos and
  direct links to each listing on its source site

## Notes & limits

- Scraping is for **personal use only** per Funda's and Pararius's terms.
  Do not use this for any commercial purpose.
- Year-built and bedroom-count fields are sometimes missing from
  listings. The filters keep listings with unknown values so you can
  inspect them manually rather than silently dropping them.
- Pararius search-result cards only show total `rooms`, not bedrooms
  separately; the filter falls back to `rooms >= bedrooms+1`.
- Re-run periodically (e.g. daily) to catch new listings.
