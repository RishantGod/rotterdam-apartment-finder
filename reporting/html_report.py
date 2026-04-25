"""Generate a self-contained, sortable HTML report from a filtered listings DataFrame.

Uses Jinja2 to render. The output file inlines all CSS / JS so it can be
opened directly in a browser without any external resources. Sorting is
client-side via a small vanilla-JS table sorter.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Any, Dict

import pandas as pd
from jinja2 import Template

logger = logging.getLogger(__name__)


_HTML_TEMPLATE = Template(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Rotterdam Apartment Finder - {{ generated_at }}</title>
<style>
  :root {
    --bg: #0f1419;
    --card: #1a2028;
    --border: #2a3540;
    --text: #e8ecf1;
    --muted: #8b97a4;
    --accent: #4ea1ff;
    --highlight: #2a3540;
    --good: #5fc784;
    --warn: #f5a623;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 24px;
  }
  h1 { margin: 0 0 4px 0; font-size: 24px; }
  .subtitle { color: var(--muted); font-size: 14px; margin-bottom: 20px; }
  .summary {
    display: flex;
    gap: 16px;
    margin-bottom: 20px;
    flex-wrap: wrap;
  }
  .stat {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
    min-width: 140px;
  }
  .stat .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  .stat .value { font-size: 22px; font-weight: 600; margin-top: 4px; }
  .controls { margin-bottom: 12px; }
  .controls input {
    background: var(--card);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 8px 12px;
    border-radius: 6px;
    width: 280px;
    font-size: 14px;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
  th, td {
    padding: 10px 12px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
    vertical-align: middle;
  }
  th {
    background: var(--highlight);
    cursor: pointer;
    user-select: none;
    font-weight: 600;
    white-space: nowrap;
  }
  th:hover { color: var(--accent); }
  tr:last-child td { border-bottom: none; }
  tr:hover { background: var(--highlight); }
  .photo { width: 120px; height: 80px; object-fit: cover; border-radius: 4px; background: var(--border); }
  .price { font-weight: 600; color: var(--good); }
  .source-badge {
    display: inline-block;
    background: var(--border);
    color: var(--muted);
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    margin-right: 4px;
  }
  .source-badge.multi { background: var(--accent); color: white; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .empty { color: var(--muted); font-style: italic; }
  .year-new { color: var(--good); font-weight: 600; }
  .footer { margin-top: 24px; color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<h1>Rotterdam Apartment Finder</h1>
<div class="subtitle">Generated {{ generated_at }}. Click column headers to sort. Use the search box to filter further.</div>

<div class="summary">
  <div class="stat"><div class="label">Matching listings</div><div class="value">{{ total }}</div></div>
  <div class="stat"><div class="label">Funda</div><div class="value">{{ funda_count }}</div></div>
  <div class="stat"><div class="label">Pararius</div><div class="value">{{ pararius_count }}</div></div>
  <div class="stat"><div class="label">On both sites</div><div class="value">{{ multi_source_count }}</div></div>
  <div class="stat"><div class="label">Max price</div><div class="value">EUR {{ max_price }}</div></div>
  <div class="stat"><div class="label">Min year built</div><div class="value">{{ min_year }}</div></div>
</div>

<div class="controls">
  <input type="text" id="filter-input" placeholder="Filter by address, neighborhood, etc..." />
</div>

{% if total == 0 %}
<p class="empty">No listings matched your criteria. Try widening your filters in config.yaml.</p>
{% else %}
<table id="listings">
<thead>
<tr>
  <th>Photo</th>
  <th data-sort="text">Address</th>
  <th data-sort="text">Neighborhood</th>
  <th data-sort="number">Price (EUR)</th>
  <th data-sort="number">m&sup2;</th>
  <th data-sort="number">Rooms</th>
  <th data-sort="number">Year</th>
  <th data-sort="text">Energy</th>
  <th data-sort="text">Source</th>
  <th>Link</th>
</tr>
</thead>
<tbody>
{% for row in rows %}
<tr>
  <td>{% if row.photo_url %}<img class="photo" src="{{ row.photo_url }}" loading="lazy" alt="">{% endif %}</td>
  <td>{{ row.address or '' }}{% if row.zip_code %}<br><small class="empty">{{ row.zip_code }}</small>{% endif %}</td>
  <td>{{ row.neighborhood or '' }}</td>
  <td data-value="{{ row.price or 0 }}" class="price">{% if row.price %}EUR {{ "{:,}".format(row.price|int).replace(",", ".") }}{% endif %}</td>
  <td data-value="{{ row.living_area_m2 or 0 }}">{{ row.living_area_m2 or '' }}</td>
  <td data-value="{{ row.num_rooms or 0 }}">{{ row.num_rooms or '' }}</td>
  <td data-value="{{ row.year_built or 0 }}" class="{% if row.year_built and row.year_built >= 2010 %}year-new{% endif %}">{{ row.year_built or '' }}</td>
  <td>{{ row.energy_label or '' }}</td>
  <td>
    {% set sources = (row.source or '').split(',') %}
    {% for s in sources %}{% if s %}<span class="source-badge {% if sources|length > 1 %}multi{% endif %}">{{ s.strip() }}</span>{% endif %}{% endfor %}
  </td>
  <td><a href="{{ row.url }}" target="_blank" rel="noopener">View</a></td>
</tr>
{% endfor %}
</tbody>
</table>
{% endif %}

<div class="footer">
  Sources scraped under personal-use terms. Always click through to the original
  listing for full details, photos, and to contact the agent.
</div>

<script>
  // Tiny client-side table sorter & filter
  const table = document.getElementById('listings');
  if (table) {
    const tbody = table.tBodies[0];
    document.querySelectorAll('th[data-sort]').forEach((th, idx) => {
      let asc = true;
      th.addEventListener('click', () => {
        const type = th.dataset.sort;
        const rows = Array.from(tbody.querySelectorAll('tr'));
        rows.sort((a, b) => {
          const av = a.cells[idx].dataset.value ?? a.cells[idx].textContent.trim();
          const bv = b.cells[idx].dataset.value ?? b.cells[idx].textContent.trim();
          if (type === 'number') {
            return (asc ? 1 : -1) * (parseFloat(av || 0) - parseFloat(bv || 0));
          }
          return (asc ? 1 : -1) * String(av).localeCompare(String(bv));
        });
        asc = !asc;
        rows.forEach(r => tbody.appendChild(r));
      });
    });
    const input = document.getElementById('filter-input');
    input.addEventListener('input', () => {
      const q = input.value.toLowerCase();
      Array.from(tbody.rows).forEach(row => {
        row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
      });
    });
  }
</script>
</body>
</html>
"""
)


def _safe_int(v):
    try:
        if v is None:
            return None
        if isinstance(v, float) and pd.isna(v):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _row_to_dict(row: pd.Series) -> Dict[str, Any]:
    return {
        "address": row.get("address") or "",
        "zip_code": row.get("zip_code") or "",
        "neighborhood": row.get("neighborhood") or "",
        "price": _safe_int(row.get("price")),
        "living_area_m2": _safe_int(row.get("living_area_m2")),
        "num_rooms": _safe_int(row.get("num_rooms")),
        "year_built": _safe_int(row.get("year_built")),
        "energy_label": row.get("energy_label") or "",
        "source": row.get("source") or "",
        "photo_url": row.get("photo_url") or "",
        "url": row.get("url") or "#",
    }


def generate_report(
    df: pd.DataFrame,
    *,
    output_path: str,
    config: Dict[str, Any],
) -> str:
    """Render the HTML report and write to `output_path`. Returns the path."""
    rows = []
    funda_count = 0
    pararius_count = 0
    multi_count = 0

    if df is not None and not df.empty:
        sorted_df = df.copy()
        if "price" in sorted_df.columns:
            sorted_df = sorted_df.sort_values("price", ascending=True, na_position="last")
        for _, r in sorted_df.iterrows():
            d = _row_to_dict(r)
            rows.append(d)
            sources = [s.strip() for s in (d.get("source") or "").split(",") if s.strip()]
            if "funda" in sources:
                funda_count += 1
            if "pararius" in sources:
                pararius_count += 1
            if len(sources) > 1:
                multi_count += 1

    price_cfg = (config.get("price") or {})
    construction_cfg = (config.get("construction") or {})

    html = _HTML_TEMPLATE.render(
        generated_at=_dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        rows=rows,
        total=len(rows),
        funda_count=funda_count,
        pararius_count=pararius_count,
        multi_source_count=multi_count,
        max_price="{:,}".format(int(price_cfg.get("max", 0))).replace(",", "."),
        min_year=construction_cfg.get("min_year_built", "any"),
    )

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("Wrote HTML report to %s (%d listings)", output_path, len(rows))
    return output_path
