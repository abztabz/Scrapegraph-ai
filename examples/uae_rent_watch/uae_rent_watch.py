"""
UAE Rent Watch
==============

A simple "let me know when the rent changes" tool for the UAE, built on top of
ScrapeGraphAI.

You keep a **watchlist** of the areas you care about (e.g. "Dubai Marina",
"Jumeirah Village Circle", "Abu Dhabi - Al Reem Island"), optionally narrowed by
property type and number of bedrooms. On every run this tool:

1. Scrapes a UAE property portal's *to-rent* page for each watched area and asks
   ScrapeGraphAI to read back the **typical asking rent** (average + median),
   the cheapest/most-expensive listing, and how many listings it saw.
2. Compares that against the last time it ran (a small JSON "state" file it keeps
   on disk) and works out the **change in AED and %**.
3. **Notifies you** about anything that moved more than your threshold — printed
   to the console, written to a report file, and (optionally) emailed to you.

It is designed to cost **nothing to run**:

* the LLM can be a **free local Ollama model** (no API key, no bill);
* notifications go over **email** (a free Gmail "app password" works);
* and it can be scheduled for free with **GitHub Actions** (see README.md), so
  there is no server to pay for.

The HTML -> structured-data step is done by ScrapeGraphAI's ``SmartScraperGraph``.
This module only adds the UAE-rent schema, the change detection, the watchlist
format and the notification glue.

Usage
-----
    # one-off check using the example watchlist and a free local model
    python uae_rent_watch.py --watchlist watchlist.example.json --model ollama/llama3.2

    # email me the changes (Gmail app password in env vars, see README.md)
    export SMTP_HOST=smtp.gmail.com SMTP_PORT=587
    export SMTP_USER="you@gmail.com" SMTP_PASSWORD="your-app-password"
    export NOTIFY_EMAIL="you@gmail.com"
    python uae_rent_watch.py --watchlist my_watchlist.json --email

See README.md for the full, no-coding-required setup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import smtplib
import socket
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Callable, Dict, List, Optional
from urllib.parse import quote_plus

from pydantic import BaseModel, Field

# scrapegraphai is imported lazily by _load_smart_scraper() below — NOT at module
# load time — so that `--demo` mode and `--help` work even when scrapegraphai or
# its heavy dependencies can't be imported. Only live scraping needs it.


def _load_smart_scraper():
    """Import SmartScraperGraph on demand, surfacing the *real* error if it fails."""
    try:
        from scrapegraphai.graphs import SmartScraperGraph

        return SmartScraperGraph
    except Exception as exc:  # ImportError or a dependency-version error
        raise SystemExit(
            "Could not import scrapegraphai, which is needed for live scraping "
            f"(it is NOT needed for --demo).\n  Underlying error: {exc!r}\n"
            "Fix: install it with `pip install scrapegraphai` (or `pip install -e .` "
            "from the repo root) and run `playwright install`."
        ) from exc


def _load_sgai_client(api_key: str):
    """Import the ScrapeGraphAI hosted-API client (scrapegraph-py v2) on demand."""
    if api_key:
        os.environ["SGAI_API_KEY"] = api_key  # v2 client also reads this env var
    try:
        from scrapegraph_py import ScrapeGraphAI
    except Exception as exc:
        raise SystemExit(
            "Could not import the ScrapeGraphAI client.\n"
            f"  Underlying error: {exc!r}\n"
            "Fix: `pip install scrapegraph-py` and set SGAI_API_KEY "
            "(get a free key at https://scrapegraphai.com)."
        ) from exc
    try:
        return ScrapeGraphAI(api_key=api_key)
    except TypeError:
        return ScrapeGraphAI()  # signature variant: reads SGAI_API_KEY from env


def _sgai_extract(client, url: str):
    """Call the v2 extract() method, tolerant of the exact schema kwarg name."""
    attempts = (
        {"prompt": SCRAPE_PROMPT, "url": url, "output_schema": AreaRent},
        {"prompt": SCRAPE_PROMPT, "url": url, "schema": AreaRent.model_json_schema()},
        {"prompt": SCRAPE_PROMPT, "url": url},
    )
    last_exc = None
    for kwargs in attempts:
        try:
            return client.extract(**kwargs)
        except TypeError as exc:  # unexpected/unknown kwarg -> try the next shape
            last_exc = exc
    raise last_exc


def _sgai_result_data(result):
    """Pull the extracted dict out of a v2 ApiResult (or dict); surface API errors."""
    status = getattr(result, "status", None)
    if status is not None and str(status).lower() not in (
        "success",
        "completed",
        "ok",
        "done",
    ):
        err = getattr(result, "error", None) or status
        raise RuntimeError(f"hosted API returned status {status!r}: {err}")
    data = getattr(result, "data", None)
    if data is not None:
        return data
    if isinstance(result, dict):
        return result.get("data", result.get("result", result))
    return result



# The single instruction both the local scraper and the hosted API use.
SCRAPE_PROMPT = (
    "This is a UAE property portal page listing apartments/villas for rent in one "
    "area. Read the rental prices shown on the page and report the typical YEARLY "
    "asking rent for this area in AED. Return: the average yearly rent, the median "
    "(most typical) yearly rent, the cheapest and most expensive yearly rent "
    "visible, how many listings you counted, the currency (AED) and the period. "
    "All money values must be plain numbers with no commas or currency symbols. "
    "If prices are shown per month, convert them to yearly by multiplying by 12. "
    "If you cannot find prices, return nulls."
)



# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

DEFAULT_CURRENCY = "AED"
BAYUT_BASE = "https://www.bayut.com"
PROPERTYFINDER_BASE = "https://www.propertyfinder.ae"
DUBIZZLE_BASE = "https://uae.dubizzle.com"
DEFAULT_PORTAL_NAME = "bayut"
SUPPORTED_PORTALS = ("bayut", "propertyfinder", "dubizzle")
DEFAULT_STATE_FILE = "rent_state.json"
# Only shout about changes bigger than this (percent), to cut through the noise.
DEFAULT_THRESHOLD_PCT = 3.0


# ----------------------------------------------------------------------------
# Watchlist + extraction schema
# ----------------------------------------------------------------------------


@dataclass
class WatchItem:
    """One area the user wants to keep an eye on."""

    area: str
    city: str = "Dubai"
    property_type: str = "apartment"  # apartment | villa | townhouse | studio ...
    bedrooms: Optional[str] = None  # e.g. "1", "2", "studio"; None = any
    # Which property portal to read: "bayut" or "propertyfinder".
    portal: str = DEFAULT_PORTAL_NAME
    # Optional explicit URL. If set it wins over the portal builder.
    url: Optional[str] = None

    def label(self) -> str:
        bits = [self.area, self.city]
        extra = []
        if self.bedrooms:
            extra.append(f"{self.bedrooms} bed")
        if self.property_type:
            extra.append(self.property_type)
        suffix = f" ({', '.join(extra)})" if extra else ""
        return f"{' — '.join(bits)}{suffix} · {portal_display_name(self.portal)}"

    def key(self) -> str:
        """Stable identity used to match this item across runs in the state file."""
        return "|".join(
            [
                (self.portal or DEFAULT_PORTAL_NAME).strip().lower(),
                self.city.strip().lower(),
                self.area.strip().lower(),
                (self.property_type or "any").strip().lower(),
                (self.bedrooms or "any").strip().lower(),
            ]
        )

    def search_url(self) -> str:
        if self.url:
            return self.url
        builder = PORTAL_BUILDERS.get(
            (self.portal or DEFAULT_PORTAL_NAME).strip().lower(), _bayut_search_url
        )
        return builder(self)


class AreaRent(BaseModel):
    """The typical asking-rent picture ScrapeGraphAI reads off a to-rent page."""

    average_rent: Optional[float] = Field(
        default=None,
        description="Average yearly asking rent across the listings, as a plain "
        "number in AED (no currency symbol or commas).",
    )
    median_rent: Optional[float] = Field(
        default=None,
        description="Median / most typical yearly asking rent, as a number in AED.",
    )
    min_rent: Optional[float] = Field(
        default=None, description="Cheapest yearly asking rent on the page, in AED."
    )
    max_rent: Optional[float] = Field(
        default=None, description="Most expensive yearly asking rent on the page, in AED."
    )
    listings_count: Optional[int] = Field(
        default=None,
        description="How many rental listings were visible / counted on the page.",
    )
    currency: Optional[str] = Field(
        default=DEFAULT_CURRENCY, description="Currency code, almost always AED."
    )
    period: Optional[str] = Field(
        default="yearly",
        description="Rent period the figures refer to: 'yearly' or 'monthly'.",
    )


# ----------------------------------------------------------------------------
# Snapshot + change records
# ----------------------------------------------------------------------------


@dataclass
class RentSnapshot:
    """A single observation for one watched area at one point in time."""

    key: str
    label: str
    area: str
    city: str
    property_type: str
    bedrooms: Optional[str]
    portal: str
    url: str
    average_rent: Optional[float] = None
    median_rent: Optional[float] = None
    min_rent: Optional[float] = None
    max_rent: Optional[float] = None
    listings_count: Optional[int] = None
    currency: str = DEFAULT_CURRENCY
    period: str = "yearly"
    checked_at: str = ""


@dataclass
class RentChange:
    """The difference between the previous snapshot and the current one."""

    label: str
    url: str
    currency: str
    period: str
    previous_rent: Optional[float]
    current_rent: Optional[float]
    delta: Optional[float] = None
    delta_pct: Optional[float] = None
    direction: str = "new"  # up | down | flat | new
    is_first_seen: bool = False
    notes: List[str] = field(default_factory=list)

    def headline(self) -> str:
        cur = _fmt_money(self.current_rent, self.currency)
        if self.is_first_seen:
            return f"🆕 {self.label}: now ~{cur}/{self.period} (first reading)"
        arrow = {"up": "🔺", "down": "🔻", "flat": "▶️"}.get(self.direction, "•")
        prev = _fmt_money(self.previous_rent, self.currency)
        pct = f"{self.delta_pct:+.1f}%" if self.delta_pct is not None else "n/a"
        return (
            f"{arrow} {self.label}: {prev} → {cur}/{self.period}  ({pct})"
        )


# ----------------------------------------------------------------------------
# The watcher
# ----------------------------------------------------------------------------


class RentWatcher:
    """Scrape -> compare -> report for a UAE rent watchlist."""

    def __init__(
        self,
        graph_config: dict,
        state_file: str = DEFAULT_STATE_FILE,
        threshold_pct: float = DEFAULT_THRESHOLD_PCT,
        verbose: bool = True,
        progress_callback: Optional[Callable[[str], None]] = None,
        reader: str = "local",
        sgai_api_key: Optional[str] = None,
    ):
        self.graph_config = graph_config
        self.state_file = state_file
        self.threshold_pct = threshold_pct
        self.reader = reader  # "local" (SmartScraperGraph) or "sgai" (hosted API)
        self.sgai_api_key = sgai_api_key
        self._sgai_client = None
        self.verbose = verbose
        self.progress_callback = progress_callback

    # -- logging -----------------------------------------------------------
    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)
        if self.progress_callback is not None:
            self.progress_callback(message.strip())

    # -- scraping ----------------------------------------------------------
    def scrape_area(self, item: WatchItem) -> RentSnapshot:
        url = item.search_url()
        self._log(f"\n🏠 Checking {item.label()}\n     {url}")

        try:
            if self.reader == "sgai":
                with open("DEBUG.txt", "a") as f:
                    f.write(f"About to call _scrape_via_sgai for {url}\n")
                rent = self._scrape_via_sgai(url)
                with open("DEBUG.txt", "a") as f:
                    f.write(f"_scrape_via_sgai returned: {rent}\n")
            else:
                rent = self._scrape_via_local(url)
        except Exception as exc:  # network / parsing hiccup -> degrade gracefully
            self._log(f"     ⚠️  could not read this area ({exc})")
            with open("DEBUG.txt", "a") as f:
                f.write(f"Exception caught: {exc!r}\n")
            rent = AreaRent()

        snapshot = RentSnapshot(
            key=item.key(),
            label=item.label(),
            area=item.area,
            city=item.city,
            property_type=item.property_type,
            bedrooms=item.bedrooms,
            portal=item.portal,
            url=url,
            average_rent=rent.average_rent,
            median_rent=rent.median_rent,
            min_rent=rent.min_rent,
            max_rent=rent.max_rent,
            listings_count=rent.listings_count,
            currency=rent.currency or DEFAULT_CURRENCY,
            period=rent.period or "yearly",
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        headline_rent = _representative_rent(snapshot)
        self._log(
            f"     read {_fmt_money(headline_rent, snapshot.currency)}/"
            f"{snapshot.period}"
            + (
                f" from {snapshot.listings_count} listings"
                if snapshot.listings_count
                else ""
            )
        )
        return snapshot

    # -- the two readers ---------------------------------------------------
    def _scrape_via_local(self, url: str) -> AreaRent:
        """Read the page with the local SmartScraperGraph (your own LLM)."""
        SmartScraperGraph = _load_smart_scraper()
        graph = SmartScraperGraph(
            prompt=SCRAPE_PROMPT,
            source=url,
            config=self.graph_config,
            schema=AreaRent,
        )
        return self._coerce_area_rent(graph.run())

    def _scrape_via_sgai(self, url: str) -> AreaRent:
        """Read the page with the ScrapeGraphAI hosted API (renders JS server-side)."""
        if self._sgai_client is None:
            self._sgai_client = _load_sgai_client(self.sgai_api_key)
        result = _sgai_extract(self._sgai_client, url)
        import sys
        print(f"     [DEBUG] raw API result: {result!r}", flush=True, file=sys.stderr)
        return self._coerce_area_rent(_sgai_result_data(result))

    @staticmethod
    def _coerce_area_rent(result) -> AreaRent:
        if isinstance(result, AreaRent):
            return result
        if isinstance(result, BaseModel):
            result = result.model_dump()
        if isinstance(result, dict):
            payload = result.get("content", result)
            if isinstance(payload, dict):
                try:
                    return AreaRent(**payload)
                except Exception:
                    return AreaRent()
        return AreaRent()

    # -- state -------------------------------------------------------------
    def load_state(self) -> Dict[str, dict]:
        if not os.path.exists(self.state_file):
            return {}
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data.get("areas", {}) if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_state(self, snapshots: List[RentSnapshot]) -> None:
        previous = self.load_state()
        for snap in snapshots:
            # Only overwrite the stored figure when we actually read a price,
            # so a one-off scrape failure doesn't wipe the history.
            if _representative_rent(snap) is not None:
                previous[snap.key] = asdict(snap)
            elif snap.key not in previous:
                previous[snap.key] = asdict(snap)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "areas": previous,
        }
        with open(self.state_file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

    # -- change detection --------------------------------------------------
    def diff(self, previous: Dict[str, dict], snapshot: RentSnapshot) -> RentChange:
        current_rent = _representative_rent(snapshot)
        prev_raw = previous.get(snapshot.key)

        if prev_raw is None:
            change = RentChange(
                label=snapshot.label,
                url=snapshot.url,
                currency=snapshot.currency,
                period=snapshot.period,
                previous_rent=None,
                current_rent=current_rent,
                direction="new",
                is_first_seen=True,
            )
            if current_rent is None:
                change.notes.append("no price could be read yet")
            return change

        previous_rent = _representative_rent_from_dict(prev_raw)
        change = RentChange(
            label=snapshot.label,
            url=snapshot.url,
            currency=snapshot.currency,
            period=snapshot.period,
            previous_rent=previous_rent,
            current_rent=current_rent,
        )

        if current_rent is None:
            change.direction = "flat"
            change.notes.append("could not read a price this time; kept last value")
            change.current_rent = previous_rent
            return change

        if previous_rent is None or previous_rent == 0:
            change.direction = "new"
            change.is_first_seen = True
            return change

        change.delta = round(current_rent - previous_rent, 2)
        change.delta_pct = round((change.delta / previous_rent) * 100.0, 2)
        if abs(change.delta_pct) < 0.01:
            change.direction = "flat"
        else:
            change.direction = "up" if change.delta > 0 else "down"
        return change

    def is_notable(self, change: RentChange) -> bool:
        if change.is_first_seen:
            return True
        if change.delta_pct is None:
            return False
        return abs(change.delta_pct) >= self.threshold_pct

    # -- orchestration -----------------------------------------------------
    def run(self, watchlist: List[WatchItem]) -> List[RentChange]:
        previous = self.load_state()
        snapshots: List[RentSnapshot] = []
        changes: List[RentChange] = []

        for item in watchlist:
            snapshot = self.scrape_area(item)
            snapshots.append(snapshot)
            changes.append(self.diff(previous, snapshot))

        self.save_state(snapshots)
        return changes


# ----------------------------------------------------------------------------
# Demo mode — run the whole pipeline on sample data (no network, no API key)
# ----------------------------------------------------------------------------

# Indicative yearly asking rents (AED) for a few popular communities, so `--demo`
# shows believable numbers out of the box. Anything not listed is estimated from
# its city. These are illustrative samples, not live figures.
SAMPLE_BASE_RENTS = {
    "dubai marina": 115000,
    "downtown dubai": 145000,
    "jumeirah village circle": 75000,
    "business bay": 105000,
    "jumeirah lake towers": 95000,
    "al reem island": 80000,
    "al nahda": 42000,
}
CITY_BASELINE = {
    "dubai": 95000,
    "abu dhabi": 80000,
    "sharjah": 45000,
    "ajman": 32000,
    "ras al khaimah": 42000,
    "fujairah": 36000,
    "umm al quwain": 30000,
}
BEDROOM_FACTOR = {"studio": 0.55, "0": 0.55, "1": 1.0, "2": 1.45, "3": 1.95, "4": 2.6}


def _round_k(value: float) -> float:
    """Round to the nearest 1,000 AED, the way listings are usually quoted."""
    return float(round(value / 1000.0) * 1000)


def sample_base_rent(item: WatchItem) -> float:
    """A plausible yearly rent for a watch item, used only by --demo."""
    base = SAMPLE_BASE_RENTS.get(item.area.strip().lower())
    if base is None:
        city_base = CITY_BASELINE.get(item.city.strip().lower(), 70000)
        # Stable per-area nudge so different areas get different numbers.
        h = int(hashlib.md5(item.area.lower().encode()).hexdigest()[:6], 16)
        base = city_base * (0.85 + (h % 30) / 100.0)
        base *= BEDROOM_FACTOR.get((item.bedrooms or "1").strip().lower(), 1.0)
    return _round_k(base)


class DemoRentWatcher(RentWatcher):
    """Drop-in RentWatcher that fabricates sample readings instead of scraping.

    The first run establishes a baseline; later runs random-walk from the last
    stored value, so you can run it twice and watch real changes get flagged —
    all without a network connection or an LLM key.
    """

    def scrape_area(self, item: WatchItem) -> RentSnapshot:
        prev = self.load_state().get(item.key())
        if prev and prev.get("median_rent"):
            median = _round_k(prev["median_rent"] * (1 + random.uniform(-0.06, 0.08)))
        else:
            median = sample_base_rent(item)
        snapshot = RentSnapshot(
            key=item.key(),
            label=item.label(),
            area=item.area,
            city=item.city,
            property_type=item.property_type,
            bedrooms=item.bedrooms,
            portal=item.portal,
            url=item.search_url(),
            average_rent=_round_k(median * 1.03),
            median_rent=median,
            min_rent=_round_k(median * 0.80),
            max_rent=_round_k(median * 1.35),
            listings_count=random.randint(18, 120),
            currency=DEFAULT_CURRENCY,
            period="yearly",
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self._log(
            f"\n🏠 [demo] {item.label()}\n     read {_fmt_money(median, 'AED')}/yearly "
            f"from {snapshot.listings_count} listings"
        )
        return snapshot


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _representative_rent(snap: RentSnapshot) -> Optional[float]:
    """Prefer median (robust to outliers), then average, then mid of min/max."""
    if snap.median_rent:
        return snap.median_rent
    if snap.average_rent:
        return snap.average_rent
    if snap.min_rent and snap.max_rent:
        return round((snap.min_rent + snap.max_rent) / 2.0, 2)
    return snap.average_rent or snap.median_rent or snap.min_rent or snap.max_rent


def _representative_rent_from_dict(raw: dict) -> Optional[float]:
    return _representative_rent(RentSnapshot(**{k: raw.get(k) for k in _SNAPSHOT_FIELDS}))


_SNAPSHOT_FIELDS = {f for f in RentSnapshot.__dataclass_fields__}  # type: ignore[attr-defined]


def _fmt_money(value: Optional[float], currency: str) -> str:
    if value is None:
        return "n/a"
    return f"{currency} {value:,.0f}"


def _slug(text: str) -> str:
    return text.strip().lower().replace(" ", "-")


def _beds_param(bedrooms: Optional[str]) -> Optional[str]:
    """Normalise a bedroom value to the digit portals expect ('studio' -> 0)."""
    if not bedrooms:
        return None
    value = bedrooms.strip().lower()
    if value in ("any", ""):
        return None
    if value == "studio":
        return "0"
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or None


def portal_display_name(portal: Optional[str]) -> str:
    return {
        "bayut": "Bayut",
        "propertyfinder": "Property Finder",
        "dubizzle": "Dubizzle",
    }.get((portal or DEFAULT_PORTAL_NAME).strip().lower(), portal or DEFAULT_PORTAL_NAME)


# -- per-portal "to-rent" search URL builders --------------------------------
# These are best-effort: portals change their URL schemes, and area pages use
# slugged paths we can't always guess. Keyword search is more forgiving, and a
# watch item can always set an explicit `url` to override the builder entirely.


def _bayut_property_segment(property_type: str) -> str:
    mapping = {
        "apartment": "apartments",
        "flat": "apartments",
        "studio": "apartments",
        "villa": "villas",
        "townhouse": "townhouses",
    }
    return mapping.get((property_type or "").strip().lower(), "property")


def _bayut_search_url(item: "WatchItem") -> str:
    query = f"{item.area} {item.city}".strip()
    path = f"/to-rent/{_bayut_property_segment(item.property_type)}/{_slug(item.city)}/"
    url = f"{BAYUT_BASE}{path}?query={quote_plus(query)}"
    beds = _beds_param(item.bedrooms)
    if beds and beds != "0":
        url += f"&beds={beds}"
    return url


def _propertyfinder_search_url(item: "WatchItem") -> str:
    # Property Finder uses c=1 for buy and c=2 for rent. Free-text `q` drives the
    # location search, which is forgiving of spelling; bedrooms go in `bdr[]`.
    query = f"{item.area} {item.city}".strip()
    url = f"{PROPERTYFINDER_BASE}/en/search?c=2&q={quote_plus(query)}"
    beds = _beds_param(item.bedrooms)
    if beds is not None:
        url += f"&bdr[]={beds}"
    return url


def _dubizzle_property_segment(property_type: str) -> str:
    mapping = {
        "apartment": "apartments",
        "flat": "apartments",
        "studio": "apartments",
        "villa": "villas",
        "townhouse": "townhouses",
    }
    return mapping.get((property_type or "").strip().lower(), "residential")


def _dubizzle_search_url(item: "WatchItem") -> str:
    # Dubizzle (uae.dubizzle.com) groups rentals under /property-for-rent/. The
    # free-text `keywords` param handles the location; bedrooms go in `bedrooms`.
    query = f"{item.area} {item.city}".strip()
    path = f"/property-for-rent/residential/{_dubizzle_property_segment(item.property_type)}/"
    url = f"{DUBIZZLE_BASE}{path}?keywords={quote_plus(query)}"
    beds = _beds_param(item.bedrooms)
    if beds and beds != "0":
        url += f"&bedrooms={beds}"
    return url


PORTAL_BUILDERS = {
    "bayut": _bayut_search_url,
    "propertyfinder": _propertyfinder_search_url,
    "dubizzle": _dubizzle_search_url,
}


# ----------------------------------------------------------------------------
# Watchlist loading
# ----------------------------------------------------------------------------


def normalize_portal(value: Optional[str], default: str = DEFAULT_PORTAL_NAME) -> str:
    portal = (value or default).strip().lower().replace(" ", "")
    aliases = {"property_finder": "propertyfinder", "pf": "propertyfinder"}
    portal = aliases.get(portal, portal)
    return portal if portal in SUPPORTED_PORTALS else default


def load_watchlist(path: str, default_portal: str = DEFAULT_PORTAL_NAME) -> List[WatchItem]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    raw_items = data.get("watchlist", data) if isinstance(data, dict) else data
    items: List[WatchItem] = []
    for entry in raw_items or []:
        if not isinstance(entry, dict) or not entry.get("area"):
            continue
        items.append(
            WatchItem(
                area=str(entry["area"]).strip(),
                city=str(entry.get("city", "Dubai")).strip() or "Dubai",
                property_type=str(entry.get("property_type", "apartment")).strip()
                or "apartment",
                bedrooms=(str(entry["bedrooms"]).strip() if entry.get("bedrooms") else None),
                portal=normalize_portal(entry.get("portal"), default_portal),
                url=(str(entry["url"]).strip() if entry.get("url") else None),
            )
        )
    return items


# ----------------------------------------------------------------------------
# Reporting + notifications
# ----------------------------------------------------------------------------


def build_report(changes: List[RentChange], threshold_pct: float) -> str:
    notable = [c for c in changes if c.is_first_seen or _abs_pct(c) >= threshold_pct]
    lines: List[str] = []
    lines.append("UAE RENT WATCH — your watchlist update")
    lines.append(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    lines.append("=" * 52)

    if not changes:
        lines.append("Your watchlist is empty. Add some areas and run again.")
        return "\n".join(lines)

    moved = [c for c in notable if not c.is_first_seen]
    new = [c for c in notable if c.is_first_seen]

    if moved:
        lines.append(f"\nRent changes over {threshold_pct:g}%:")
        for c in sorted(moved, key=lambda x: abs(x.delta_pct or 0), reverse=True):
            lines.append(f"  {c.headline()}")
            lines.append(f"      {c.url}")
    if new:
        lines.append("\nNow tracking (first reading):")
        for c in new:
            lines.append(f"  {c.headline()}")

    quiet = [c for c in changes if c not in notable]
    if quiet:
        lines.append(f"\nNo significant change in {len(quiet)} other area(s):")
        for c in quiet:
            lines.append(f"  {c.headline()}")

    return "\n".join(lines)


def _abs_pct(change: RentChange) -> float:
    return abs(change.delta_pct) if change.delta_pct is not None else 0.0


def send_email(subject: str, body: str) -> None:
    """Send the report over SMTP using env vars. A free Gmail app password works."""

    def _clean(val: Optional[str]) -> Optional[str]:
        # Strip stray spaces/newlines that creep in when pasting secrets on a phone.
        return val.strip() if isinstance(val, str) else val

    host = _clean(os.getenv("SMTP_HOST"))
    port_raw = _clean(os.getenv("SMTP_PORT")) or "587"
    user = _clean(os.getenv("SMTP_USER"))
    password = os.getenv("SMTP_PASSWORD")
    # Gmail shows app passwords with spaces ("abcd efgh ..."); the real value has none.
    password = password.strip().replace(" ", "") if password else password
    to_addr = _clean(os.getenv("NOTIFY_EMAIL")) or user

    missing = [
        name
        for name, val in (
            ("SMTP_HOST", host),
            ("SMTP_USER", user),
            ("SMTP_PASSWORD", password),
            ("NOTIFY_EMAIL", to_addr),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            "Email not configured. Missing env var(s): " + ", ".join(missing)
        )

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RuntimeError(f"SMTP_PORT must be a number, got {port_raw!r}") from exc

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [to_addr], msg.as_string())
    except socket.gaierror as exc:
        raise RuntimeError(
            f"Could not reach mail server {host!r}:{port}. Check the SMTP_HOST "
            "secret is exactly 'smtp.gmail.com' (no spaces/quotes). "
            f"[{exc}]"
        ) from exc
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            "Mail server rejected the login. For Gmail, SMTP_USER must be your full "
            "address and SMTP_PASSWORD must be a 16-character App Password (not your "
            f"normal password). [{exc}]"
        ) from exc


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def resolve_api_key(explicit: Optional[str] = None) -> Optional[str]:
    """First non-empty of an explicit key or any common provider env var."""
    candidates = [
        explicit,
        os.getenv("OPENAI_APIKEY"),
        os.getenv("OPENAI_API_KEY"),
        os.getenv("GEMINI_API_KEY"),
        os.getenv("GOOGLE_API_KEY"),
        os.getenv("GROQ_API_KEY"),
        os.getenv("ANTHROPIC_API_KEY"),
        os.getenv("LLM_API_KEY"),
    ]
    return next((c for c in candidates if c), None)


def build_graph_config(args: argparse.Namespace) -> dict:
    api_key = resolve_api_key(args.api_key)
    if not args.model.startswith("ollama/") and not api_key:
        raise SystemExit(
            f"No API key found for model '{args.model}'. Pass --api-key or set one "
            "of OPENAI_API_KEY / GEMINI_API_KEY / GROQ_API_KEY. (Or run a free "
            "local model with --model ollama/llama3.2.)"
        )
    config: dict = {
        "llm": {"model": args.model},
        "verbose": args.verbose,
        "headless": True,
    }
    if api_key:
        config["llm"]["api_key"] = api_key
    return config


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch UAE rents by area and get notified when they change.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--watchlist",
        default="watchlist.example.json",
        help="Path to your watchlist JSON file.",
    )
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help="Where to remember previous rents (created automatically).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD_PCT,
        help="Only flag changes at least this %% big.",
    )
    parser.add_argument(
        "--portal",
        choices=SUPPORTED_PORTALS,
        default=DEFAULT_PORTAL_NAME,
        help="Default property portal for areas that don't set their own.",
    )
    parser.add_argument(
        "--model",
        default="ollama/llama3.2",
        help="LLM to use, e.g. ollama/llama3.2 (free, local) or openai/gpt-4o-mini.",
    )
    parser.add_argument("--api-key", help="LLM API key (else read from env).")
    parser.add_argument(
        "--sgai",
        action="store_true",
        help="Read pages with the ScrapeGraphAI hosted API (renders JS + handles "
        "anti-bot). Needs SGAI_API_KEY (or --api-key). Most reliable for big portals.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Try it with built-in sample data — no network or API key needed.",
    )
    parser.add_argument(
        "--email",
        action="store_true",
        help="Also email the report (configure SMTP_* env vars; see README).",
    )
    parser.add_argument(
        "--report",
        default="rent_report.txt",
        help="Where to write the text report.",
    )
    parser.add_argument(
        "--quiet", dest="verbose", action="store_false", help="Reduce logging."
    )
    parser.set_defaults(verbose=True)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if not os.path.exists(args.watchlist):
        raise SystemExit(
            f"Watchlist file not found: {args.watchlist}\n"
            "Copy watchlist.example.json and edit it with the areas you care about."
        )

    watchlist = load_watchlist(args.watchlist, default_portal=args.portal)
    if not watchlist:
        raise SystemExit(
            f"No areas found in {args.watchlist}. Add at least one area and retry."
        )

    if args.demo:
        print("🧪 Demo mode: using built-in sample data (no network / API key).")
        watcher: RentWatcher = DemoRentWatcher(
            graph_config={},
            state_file=args.state_file,
            threshold_pct=args.threshold,
            verbose=args.verbose,
        )
    elif args.sgai:
        sgai_key = args.api_key or os.getenv("SGAI_API_KEY")
        if not sgai_key:
            raise SystemExit(
                "No ScrapeGraphAI API key found. Set SGAI_API_KEY (or pass --api-key). "
                "Get a free key at https://scrapegraphai.com."
            )
        print("🌐 Using the ScrapeGraphAI hosted API to read the pages.")
        watcher = RentWatcher(
            graph_config={},
            state_file=args.state_file,
            threshold_pct=args.threshold,
            verbose=args.verbose,
            reader="sgai",
            sgai_api_key=sgai_key,
        )
    else:
        graph_config = build_graph_config(args)
        watcher = RentWatcher(
            graph_config=graph_config,
            state_file=args.state_file,
            threshold_pct=args.threshold,
            verbose=args.verbose,
        )

    changes = watcher.run(watchlist)
    report = build_report(changes, args.threshold)

    print("\n" + report)
    with open(args.report, "w", encoding="utf-8") as handle:
        handle.write(report + "\n")
    print(f"\n💾 Report written to {args.report}")

    if args.email:
        notable = [c for c in changes if c.is_first_seen or _abs_pct(c) >= args.threshold]
        moved = [c for c in notable if not c.is_first_seen]
        try:
            subject = (
                f"UAE Rent Watch: {len(moved)} change(s) in your areas"
                if moved
                else "UAE Rent Watch: your daily update"
            )
            send_email(subject, report)
            print("📧 Emailed the report.")
        except Exception as exc:
            print(f"⚠️  Could not send email: {exc}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
