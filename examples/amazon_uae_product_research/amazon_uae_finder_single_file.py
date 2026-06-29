#!/usr/bin/env python3
"""
Amazon UAE Product Finder — SINGLE-FILE edition (for Koder / a-Shell / Pyto)
===========================================================================

This is the whole web app in ONE file, with the HTML embedded, so you can drop
it into a phone code editor / shell (Koder, a-Shell, Pyto, Pythonista) or any
computer and just run it:

    python amazon_uae_finder_single_file.py

then open the printed URL in a browser.

Two modes — it picks automatically:

  • REAL mode   — if `scrapegraphai` is installed (a computer), it really
                  scrapes Amazon UAE and (optionally) sources from China.
                  Set OPENAI_APIKEY first.

  • DEMO mode   — if `scrapegraphai` is NOT installed (e.g. on iOS, where
                  Playwright/Chromium can't run), it serves the exact same
                  mobile UI with realistic SAMPLE data so you can try it on
                  your phone standalone. No keys, no network, stdlib only.

Nothing to install for DEMO mode — it uses only the Python standard library.
The optional `qrcode` package (pure Python) adds a scan-to-open QR code.

Why can't the real scraper run on the phone? It needs a headless Chromium
(Playwright) plus heavy ML libraries, which iOS doesn't support. Run REAL mode
on a computer and open it from your phone over Wi-Fi (the app shows a QR).
"""

from __future__ import annotations

import json
import math
import os
import random
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

# --- Mode detection: is the real scraping engine available? -----------------
try:
    import scrapegraphai  # noqa: F401

    REAL_MODE = True
except ImportError:
    REAL_MODE = False

# --- Optional QR code (pure-Python, no PIL/lxml) ----------------------------
try:
    import qrcode

    HAVE_QRCODE = True
except ImportError:
    HAVE_QRCODE = False


AED_PER_USD = 3.6725

# In-memory job store. Fine for a single-user, run-it-yourself tool.
JOBS: Dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


# ===========================================================================
# Scoring (shared by both modes; mirrors the full CLI tool's model)
# ===========================================================================


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _demand_score(sales: Optional[int], bsr: Optional[int]) -> float:
    score, has = 0.0, False
    if sales is not None:
        has = True
        score = max(score, _clamp(13.5 * (max(sales, 0) ** 0.27)))
    if bsr:
        has = True
        score = max(score, _clamp(100.0 - 13.0 * math.log10(bsr)))
    return _clamp(score) if has else 25.0


def _competition_score(reviews: Optional[int], sellers: Optional[int]) -> float:
    score, has = 100.0, False
    if reviews is not None:
        has = True
        score = min(score, _clamp(100.0 - 26.0 * math.log10(max(reviews, 0) + 1)))
    if sellers:
        has = True
        score = min(score, _clamp(100.0 - 5.0 * (sellers - 1)))
    return _clamp(score) if has else 50.0


def _margin_score(price_aed: Optional[float], china_usd: Optional[float]):
    if not price_aed or not china_usd:
        return 0.0, None
    price_usd = price_aed / AED_PER_USD
    if price_usd <= 0:
        return 0.0, None
    margin = (price_usd - china_usd) / price_usd
    return _clamp(margin * 140.0), round(margin * 100.0, 1)


def score_record(rec: dict, has_china: bool) -> dict:
    """Fill in demand/competition/margin/opportunity scores on a result dict."""
    rec["demand_score"] = round(_demand_score(rec.get("bought_last_month"), rec.get("bsr_rank")), 1)
    rec["competition_score"] = round(
        _competition_score(rec.get("reviews_count"), rec.get("seller_count")), 1
    )
    m_score, m_pct = _margin_score(rec.get("price"), rec.get("china_unit_price_usd"))
    rec["margin_score"] = round(m_score, 1)
    rec["estimated_margin_pct"] = m_pct

    if has_china:
        blended = 0.45 * rec["demand_score"] + 0.35 * rec["competition_score"] + 0.20 * rec["margin_score"]
    else:
        blended = (0.45 / 0.80) * rec["demand_score"] + (0.35 / 0.80) * rec["competition_score"]
    rec["opportunity_score"] = round(_clamp(blended), 1)
    return rec


# ===========================================================================
# DEMO data (used when scrapegraphai is not installed, e.g. on iOS)
# ===========================================================================

_BRANDS = ["AuraFit", "NovaHome", "PeakGear", "UrbanEdge", "ZenLiving", "FlexPro", "—"]
_CATEGORIES = ["Sports", "Home & Kitchen", "Electronics", "Health", "Office", "Automotive"]


def _demo_products(keyword: str, n: int, source_china: bool) -> List[dict]:
    """Synthesize plausible, ranked-ish sample products for a keyword."""
    rng = random.Random(hash(keyword) & 0xFFFFFFFF)
    products = []
    for i in range(n):
        # Earlier items skew toward better opportunities; later ones saturated.
        tilt = i / max(n - 1, 1)
        sales = int(rng.choice([800, 500, 300, 150, 70, 40]) * (1.0 - 0.5 * tilt)) + rng.randint(0, 40)
        reviews = int(rng.choice([30, 80, 220, 900, 4000, 18000]) * (0.4 + 1.6 * tilt)) + rng.randint(0, 30)
        bsr = int(rng.choice([600, 1500, 4000, 12000, 45000, 120000]) * (0.5 + 1.5 * tilt)) + 1
        sellers = rng.randint(1, 3) + int(round(tilt * rng.randint(2, 18)))
        price = round(rng.uniform(45, 220), 2)
        rating = round(rng.uniform(3.8, 4.8), 1)
        rec = {
            "keyword": keyword,
            "title": f"{keyword.strip().title()} — {rng.choice(['Pro', 'Max', 'Lite', 'Plus', 'Classic'])} {chr(65 + i)}",
            "brand": rng.choice(_BRANDS),
            "url": f"https://www.amazon.ae/s?k={keyword.replace(' ', '+')}",
            "price": price,
            "currency": "AED",
            "rating": rating,
            "reviews_count": reviews,
            "bought_last_month": sales,
            "bsr_rank": bsr,
            "bsr_category": rng.choice(_CATEGORIES),
            "seller_count": sellers,
            "china_unit_price_usd": None,
            "china_platform": None,
            "china_supplier": None,
            "china_url": None,
            "asin": None,
            "estimated_margin_pct": None,
            "notes": ["DEMO data — install scrapegraphai for real Amazon results"],
        }
        if source_china:
            rec["china_unit_price_usd"] = round((price / AED_PER_USD) * rng.uniform(0.12, 0.4), 2)
            rec["china_platform"] = rng.choice(["Alibaba", "1688", "DHgate"])
            rec["china_supplier"] = f"{rng.choice(['Shenzhen', 'Yiwu', 'Guangzhou'])} Trading Co."
            rec["china_url"] = "https://www.alibaba.com/"
        products.append(rec)
    return products


def run_job_demo(job_id: str, keywords: List[str], max_products: int, source_china: bool) -> None:
    try:
        for keyword in keywords:
            products = _demo_products(keyword, max_products, source_china)
            with JOBS_LOCK:
                JOBS[job_id]["total"] += len(products)
                JOBS[job_id]["message"] = f"Scanning '{keyword}' (demo)…"
            for rec in products:
                time.sleep(0.35)  # let the phone's progress bar animate
                score_record(rec, source_china)
                with JOBS_LOCK:
                    JOBS[job_id]["results"].append(rec)
                    JOBS[job_id]["results"].sort(key=lambda r: r["opportunity_score"], reverse=True)
                    JOBS[job_id]["done_count"] += 1
                    JOBS[job_id]["message"] = f"Evaluated {JOBS[job_id]['done_count']} products (demo)…"
        with JOBS_LOCK:
            count = len(JOBS[job_id]["results"])
        _set(job_id, status="done", message=f"Done (demo) — {count} sample products ranked.")
    except Exception as exc:  # pragma: no cover
        _set(job_id, status="error", message=f"Demo failed: {exc}")


# ===========================================================================
# REAL scraping (used when scrapegraphai is installed)
# ===========================================================================


def run_job_real(job_id: str, keywords: List[str], max_products: int,
                 source_china: bool, model: str, api_key: Optional[str]) -> None:
    # Import the full CLI engine that lives next to this file.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from amazon_uae_product_finder import (
            AmazonUAEProductFinder,
            ProductOpportunity,
            score as score_opp,
        )
        from dataclasses import asdict
    except ImportError as exc:
        _set(job_id, status="error",
             message=f"Real engine unavailable ({exc}). Keep amazon_uae_product_finder.py next to this file.")
        return

    api_key = api_key or os.getenv("OPENAI_APIKEY") or os.getenv("OPENAI_API_KEY")
    if model.startswith("openai/") and not api_key:
        _set(job_id, status="error",
             message="No OpenAI API key. Set OPENAI_APIKEY or use an ollama/ model.")
        return

    graph_config = {"llm": {"model": model}, "verbose": False, "headless": True}
    if api_key:
        graph_config["llm"]["api_key"] = api_key
    if os.getenv("SERPER_API_KEY"):
        graph_config["serper_api_key"] = os.getenv("SERPER_API_KEY")
        graph_config["search_engine"] = "serper"

    def on_progress(message: str) -> None:
        if message:
            _set(job_id, message=message)

    finder = AmazonUAEProductFinder(
        graph_config=graph_config, source_china=source_china,
        verbose=True, progress_callback=on_progress,
    )
    try:
        for keyword in keywords:
            products = finder.search_keyword(keyword, max_products)
            with JOBS_LOCK:
                JOBS[job_id]["total"] += len(products)
            for product in products:
                detail = finder.enrich_product(product)
                opp = ProductOpportunity(
                    keyword=keyword, title=detail.title or product.title,
                    url=finder._absolute_url(product.url), asin=product.asin,
                    price=detail.price, currency=product.currency or "AED",
                    rating=detail.rating, reviews_count=detail.reviews_count,
                    bought_last_month=detail.bought_last_month, bsr_rank=detail.bsr_rank,
                    bsr_category=detail.bsr_category, seller_count=detail.seller_count,
                    brand=detail.brand,
                )
                if source_china:
                    supplier = finder.source_from_china(opp.title)
                    if supplier:
                        opp.china_platform = supplier.platform
                        opp.china_supplier = supplier.supplier_name
                        opp.china_url = supplier.product_url
                        opp.china_unit_price_usd = supplier.unit_price_usd
                score_opp(opp, has_china_data=source_china)
                with JOBS_LOCK:
                    JOBS[job_id]["results"].append(asdict(opp))
                    JOBS[job_id]["results"].sort(key=lambda r: r["opportunity_score"], reverse=True)
                    JOBS[job_id]["done_count"] += 1
        with JOBS_LOCK:
            count = len(JOBS[job_id]["results"])
        _set(job_id, status="done", message=f"Done — {count} products ranked.")
    except Exception as exc:
        _set(job_id, status="error", message=f"Failed: {exc}")


def _set(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(fields)


# ===========================================================================
# QR + LAN helpers
# ===========================================================================


def qr_svg(data: str) -> Optional[str]:
    if not HAVE_QRCODE:
        return None
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    rects = []
    for y, row in enumerate(matrix):
        x = 0
        while x < n:
            if row[x]:
                start = x
                while x < n and row[x]:
                    x += 1
                rects.append(f'<rect x="{start}" y="{y}" width="{x - start}" height="1"/>')
            else:
                x += 1
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" '
        f'shape-rendering="crispEdges" width="180" height="180">'
        f'<rect width="{n}" height="{n}" fill="#ffffff"/>'
        f'<g fill="#0b1020">{"".join(rects)}</g></svg>'
    )


def _lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


# ===========================================================================
# HTTP server
# ===========================================================================


class Handler(BaseHTTPRequestHandler):
    server_version = "AmazonUAEResearch/1.0-single"

    def log_message(self, *args) -> None:
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            banner = "" if REAL_MODE else DEMO_BANNER_HTML
            html = INDEX_HTML.replace("<!--DEMO_BANNER-->", banner)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/status":
            self._handle_status()
        elif path == "/api/info":
            port = self.server.server_address[1]
            lan_url = f"http://{_lan_ip()}:{port}"
            self._send_json(200, {
                "lan_url": lan_url, "qr_svg": qr_svg(lan_url),
                "qr_available": HAVE_QRCODE, "demo": not REAL_MODE,
            })
        elif path == "/health":
            self._send_json(200, {"ok": True, "mode": "real" if REAL_MODE else "demo"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path == "/api/search":
            self._handle_search()
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_search(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        raw = data.get("keywords", "")
        if isinstance(raw, str):
            keywords = [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]
        else:
            keywords = [str(k).strip() for k in raw if str(k).strip()]
        if not keywords:
            self._send_json(400, {"error": "Please enter at least one keyword."})
            return

        max_products = max(1, min(int(data.get("max_products", 5)), 20))
        source_china = bool(data.get("source_china", False))
        model = str(data.get("model") or "openai/gpt-4o-mini")
        api_key = data.get("api_key") or None

        job_id = uuid.uuid4().hex
        with JOBS_LOCK:
            JOBS[job_id] = {
                "status": "queued", "message": "Queued…", "results": [],
                "total": 0, "done_count": 0,
            }
        if REAL_MODE:
            target, args = run_job_real, (job_id, keywords, max_products, source_china, model, api_key)
        else:
            target, args = run_job_demo, (job_id, keywords, max_products, source_china)
        threading.Thread(target=target, args=args, daemon=True).start()
        self._send_json(200, {"id": job_id})

    def _handle_status(self) -> None:
        params = parse_qs(urlparse(self.path).query)
        job_id = (params.get("id") or [""])[0]
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            payload = None if job is None else dict(job)
        if payload is None:
            self._send_json(404, {"error": "unknown job id"})
            return
        self._send_json(200, payload)


def main() -> int:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Handler)
    ip = _lan_ip()
    mode = "REAL (live Amazon UAE scraping)" if REAL_MODE else "DEMO (sample data — install scrapegraphai for live results)"
    print("\n📱 Amazon UAE Product Research — single-file web app")
    print("=" * 56)
    print(f"  Mode:    {mode}")
    print(f"  Local:   http://localhost:{port}")
    print(f"  Wi-Fi:   http://{ip}:{port}   (open this on your phone)")
    print("=" * 56)
    print("  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
    return 0


# ===========================================================================
# Embedded front-end (single source of truth — keep in sync with web/index.html)
# ===========================================================================

DEMO_BANNER_HTML = (
    '<div class="card" style="border-color:#fbbf24;background:rgba(251,191,36,.08)">'
    '<b style="color:#fbbf24">⚠️ Demo mode</b><div style="color:#9aa6c7;font-size:13px;margin-top:4px">'
    'Showing realistic <b>sample</b> data. The live Amazon UAE scraper needs '
    '<code>scrapegraphai</code> + Chromium, which can\'t run on iOS — run this file '
    'on a computer (with <code>OPENAI_APIKEY</code> set) for real results.</div></div>'
)

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
<meta name="theme-color" content="#0b1020" />
<title>Amazon UAE Product Finder</title>
<style>
  :root {
    --bg: #0b1020; --card: #161c30; --card2: #1d2540; --text: #eef2ff;
    --muted: #9aa6c7; --accent: #ff9900; --accent2: #34d399; --bad: #f87171;
    --mid: #fbbf24; --line: #2a3252; --radius: 16px;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--text);
    font: 16px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: env(safe-area-inset-top) 0 calc(env(safe-area-inset-bottom) + 24px);
    -webkit-text-size-adjust: 100%;
  }
  header {
    position: sticky; top: 0; z-index: 10;
    background: linear-gradient(180deg, #0b1020 70%, rgba(11,16,32,0));
    padding: 18px 18px 12px;
  }
  header h1 { margin: 0; font-size: 20px; letter-spacing: .2px; }
  header h1 span { color: var(--accent); }
  header p { margin: 4px 0 0; color: var(--muted); font-size: 13px; }
  main { padding: 0 16px; max-width: 720px; margin: 0 auto; }
  .card { background: var(--card); border: 1px solid var(--line);
          border-radius: var(--radius); padding: 16px; margin: 14px 0; }
  label { display: block; font-size: 13px; color: var(--muted); margin: 12px 0 6px; }
  textarea, input[type=number], select {
    width: 100%; background: var(--card2); color: var(--text);
    border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px;
    font-size: 16px; outline: none;
  }
  textarea { resize: vertical; min-height: 74px; }
  .row { display: flex; gap: 12px; }
  .row > div { flex: 1; }
  .toggle { display: flex; align-items: center; justify-content: space-between; margin-top: 14px; }
  .toggle span { font-size: 14px; }
  .switch { position: relative; width: 52px; height: 30px; flex: none; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .slider { position: absolute; inset: 0; background: var(--card2);
            border: 1px solid var(--line); border-radius: 30px; transition: .2s; }
  .slider::before { content: ""; position: absolute; height: 22px; width: 22px;
            left: 3px; top: 3px; background: var(--muted); border-radius: 50%; transition: .2s; }
  .switch input:checked + .slider { background: rgba(52,211,153,.25); border-color: var(--accent2); }
  .switch input:checked + .slider::before { transform: translateX(22px); background: var(--accent2); }
  button.go {
    width: 100%; margin-top: 18px; padding: 15px; font-size: 17px; font-weight: 700;
    color: #1a1200; background: var(--accent); border: none; border-radius: 14px;
    cursor: pointer; transition: transform .05s, opacity .2s;
  }
  button.go:active { transform: scale(.985); }
  button.go:disabled { opacity: .5; }
  .status { display: none; align-items: center; gap: 12px; }
  .status.show { display: flex; }
  .spinner { width: 22px; height: 22px; border: 3px solid var(--line);
             border-top-color: var(--accent); border-radius: 50%;
             animation: spin .8s linear infinite; flex: none; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .status .msg { font-size: 14px; color: var(--muted); word-break: break-word; }
  .progress-bar { height: 6px; background: var(--card2); border-radius: 6px;
                  overflow: hidden; margin-top: 10px; }
  .progress-bar > i { display: block; height: 100%; width: 0;
                  background: var(--accent2); transition: width .4s; }
  .result {
    background: var(--card); border: 1px solid var(--line);
    border-radius: var(--radius); padding: 14px; margin: 12px 0;
    position: relative; overflow: hidden;
  }
  .result .rank { position: absolute; top: 0; left: 0; background: var(--card2);
    color: var(--muted); font-size: 12px; font-weight: 700; padding: 4px 10px;
    border-bottom-right-radius: 12px; }
  .result h3 { margin: 22px 0 4px; font-size: 16px; line-height: 1.3; padding-right: 64px; }
  .result .kw { color: var(--muted); font-size: 12px; }
  .score-badge { position: absolute; top: 12px; right: 12px; text-align: center; width: 54px; }
  .score-badge .num { font-size: 22px; font-weight: 800; line-height: 1; }
  .score-badge .lbl { font-size: 10px; color: var(--muted); text-transform: uppercase; }
  .metrics { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-top: 12px; }
  .metric { background: var(--card2); border-radius: 10px; padding: 8px 10px; }
  .metric .m-lbl { font-size: 11px; color: var(--muted); }
  .metric .m-val { font-size: 15px; font-weight: 700; margin-top: 2px; }
  .bars { margin-top: 12px; display: grid; gap: 6px; }
  .bar-row { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--muted); }
  .bar-row .b-lbl { width: 78px; flex: none; }
  .bar-track { flex: 1; height: 7px; background: var(--card2); border-radius: 7px; overflow: hidden; }
  .bar-track > i { display: block; height: 100%; border-radius: 7px; }
  .bar-row .b-num { width: 30px; text-align: right; color: var(--text); }
  .china { margin-top: 12px; font-size: 13px; color: var(--accent2);
           background: rgba(52,211,153,.08); border: 1px solid rgba(52,211,153,.25);
           border-radius: 10px; padding: 8px 10px; }
  .links { margin-top: 12px; display: flex; gap: 10px; flex-wrap: wrap; }
  .links a { font-size: 13px; color: var(--accent); text-decoration: none;
             border: 1px solid var(--line); padding: 7px 12px; border-radius: 10px; }
  .notes { margin-top: 8px; font-size: 11px; color: var(--muted); font-style: italic; }
  .phone-card { padding: 14px; }
  .phone-row { display: flex; gap: 14px; align-items: center; }
  .qr { flex: none; width: 96px; height: 96px; background: #fff; border-radius: 12px;
        padding: 6px; display: flex; align-items: center; justify-content: center; }
  .qr svg { width: 100%; height: 100%; border-radius: 6px; }
  .phone-text { min-width: 0; }
  .phone-title { font-weight: 700; font-size: 15px; }
  .phone-sub { color: var(--muted); font-size: 12px; margin: 4px 0 8px; }
  .phone-url { display: inline-block; color: var(--accent); font-size: 15px;
               font-weight: 700; text-decoration: none; word-break: break-all; }
  .empty { color: var(--muted); text-align: center; padding: 30px 10px; }
  .error { color: var(--bad); }
  footer { text-align: center; color: var(--muted); font-size: 11px; margin-top: 18px; padding: 0 16px; }
</style>
</head>
<body>
<header>
  <h1>🛒 Amazon <span>UAE</span> Product Finder</h1>
  <p>Find products worth selling: demand, competition, reviews, BSR &amp; China sourcing.</p>
</header>

<main>
  <!--DEMO_BANNER-->
  <div class="card phone-card" id="phoneCard" style="display:none">
    <div class="phone-row">
      <div class="qr" id="qrBox"></div>
      <div class="phone-text">
        <div class="phone-title">📱 Open on your iPhone</div>
        <div class="phone-sub" id="phoneSub">Scan with the Camera app, or type the address below.</div>
        <a class="phone-url" id="phoneUrl" href="#"></a>
      </div>
    </div>
  </div>

  <form id="form" class="card">
    <label for="keywords">Keywords / niches (comma or new line)</label>
    <textarea id="keywords" placeholder="yoga mat, resistance bands, car phone holder"></textarea>
    <div class="row">
      <div>
        <label for="max">Products / keyword</label>
        <input type="number" id="max" min="1" max="20" value="5" />
      </div>
      <div>
        <label for="model">Model</label>
        <select id="model">
          <option value="openai/gpt-4o-mini">openai/gpt-4o-mini</option>
          <option value="openai/gpt-4o">openai/gpt-4o</option>
          <option value="ollama/llama3.2">ollama/llama3.2 (local)</option>
        </select>
      </div>
    </div>
    <div class="toggle">
      <span>🏭 Find China supplier &amp; estimate margin</span>
      <label class="switch"><input type="checkbox" id="china" /><span class="slider"></span></label>
    </div>
    <button class="go" id="go" type="submit">Find products</button>
    <div class="status" id="status" style="margin-top:16px">
      <div class="spinner"></div>
      <div class="msg" id="statusMsg">Working…</div>
    </div>
    <div class="progress-bar" id="progressWrap" style="display:none"><i id="progressBar"></i></div>
  </form>

  <div id="results"></div>
  <footer>Estimates for research only — validate before sourcing. Respect Amazon's Terms of Service.</footer>
</main>

<script>
const $ = (id) => document.getElementById(id);
let pollTimer = null;
function scoreColor(s) { if (s >= 70) return 'var(--accent2)'; if (s >= 45) return 'var(--mid)'; return 'var(--bad)'; }
function fmt(n) { return (n === null || n === undefined) ? '—' : n; }
function fmtInt(n) { return (n === null || n === undefined) ? '—' : Number(n).toLocaleString(); }
function bar(label, val) {
  const v = Math.max(0, Math.min(100, val || 0));
  return `<div class="bar-row"><span class="b-lbl">${label}</span>
    <span class="bar-track"><i style="width:${v}%;background:${scoreColor(v)}"></i></span>
    <span class="b-num">${Math.round(v)}</span></div>`;
}
function resultCard(r, i) {
  const sc = r.opportunity_score || 0;
  const bsr = r.bsr_rank ? ('#' + Number(r.bsr_rank).toLocaleString() + (r.bsr_category ? ' · ' + r.bsr_category : '')) : '—';
  const price = (r.price !== null && r.price !== undefined) ? (r.price + ' ' + (r.currency || 'AED')) : '—';
  let china = '';
  if (r.china_unit_price_usd !== null && r.china_unit_price_usd !== undefined) {
    china = `<div class="china">🏭 China: $${r.china_unit_price_usd} ${r.china_platform ? 'via ' + r.china_platform : ''}` +
            (r.estimated_margin_pct !== null && r.estimated_margin_pct !== undefined ? ` · est. margin ${r.estimated_margin_pct}%` : '') + `</div>`;
  }
  const links = [];
  if (r.url) links.push(`<a href="${r.url}" target="_blank" rel="noopener">View on Amazon</a>`);
  if (r.china_url) links.push(`<a href="${r.china_url}" target="_blank" rel="noopener">Supplier</a>`);
  const notes = (r.notes && r.notes.length) ? `<div class="notes">⚠️ ${r.notes.join('; ')}</div>` : '';
  return `<div class="result">
    <div class="rank">#${i + 1}</div>
    <div class="score-badge"><div class="num" style="color:${scoreColor(sc)}">${sc}</div><div class="lbl">score</div></div>
    <h3>${r.title || 'Untitled'}</h3>
    <div class="kw">🔎 ${r.keyword}${r.brand && r.brand !== '—' ? ' · ' + r.brand : ''}</div>
    <div class="metrics">
      <div class="metric"><div class="m-lbl">Price</div><div class="m-val">${price}</div></div>
      <div class="metric"><div class="m-lbl">Monthly sales</div><div class="m-val">${fmtInt(r.bought_last_month)}</div></div>
      <div class="metric"><div class="m-lbl">Reviews</div><div class="m-val">${fmtInt(r.reviews_count)}</div></div>
      <div class="metric"><div class="m-lbl">Rating</div><div class="m-val">${fmt(r.rating)}${r.rating ? '★' : ''}</div></div>
      <div class="metric"><div class="m-lbl">BSR</div><div class="m-val" style="font-size:13px">${bsr}</div></div>
      <div class="metric"><div class="m-lbl">Competitors</div><div class="m-val">${fmtInt(r.seller_count)}</div></div>
    </div>
    <div class="bars">${bar('Demand', r.demand_score)}${bar('Low compet.', r.competition_score)}${bar('Margin', r.margin_score)}</div>
    ${china}
    ${links.length ? `<div class="links">${links.join('')}</div>` : ''}
    ${notes}
  </div>`;
}
function render(job) {
  const results = job.results || [];
  const wrap = $('results');
  if (job.status === 'error') { wrap.innerHTML = `<div class="card empty error">⚠️ ${job.message || 'Something went wrong.'}</div>`; return; }
  if (!results.length) { wrap.innerHTML = job.status === 'done' ? `<div class="card empty">No products found. Try different keywords.</div>` : ''; return; }
  wrap.innerHTML = results.map(resultCard).join('');
}
function setBusy(busy, msg) {
  $('go').disabled = busy;
  $('status').classList.toggle('show', busy);
  $('progressWrap').style.display = busy ? 'block' : 'none';
  if (msg) $('statusMsg').textContent = msg;
}
async function poll(id) {
  try {
    const res = await fetch('/api/status?id=' + id);
    const job = await res.json();
    const total = job.total || 0, done = job.done_count || 0;
    const pct = total ? Math.round((done / total) * 100) : (job.status === 'running' ? 8 : 0);
    $('progressBar').style.width = pct + '%';
    $('statusMsg').textContent = (total ? `(${done}/${total}) ` : '') + (job.message || 'Working…');
    render(job);
    if (job.status === 'done' || job.status === 'error') { setBusy(false); clearInterval(pollTimer); }
  } catch (e) { $('statusMsg').textContent = 'Connection lost, retrying…'; }
}
async function loadPhoneInfo() {
  try {
    const info = await (await fetch('/api/info')).json();
    if (!info.lan_url) return;
    const lanHost = info.lan_url.replace(/^https?:\/\//, '').split(':')[0];
    if (location.hostname === lanHost) return;
    $('phoneUrl').textContent = info.lan_url;
    $('phoneUrl').href = info.lan_url;
    if (info.qr_svg) { $('qrBox').innerHTML = info.qr_svg; }
    else { $('qrBox').style.display = 'none'; $('phoneSub').textContent = 'Type this address in Safari. (Tip: pip install qrcode for a scannable code.)'; }
    $('phoneCard').style.display = 'block';
  } catch (e) { /* convenience only */ }
}
loadPhoneInfo();
$('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const keywords = $('keywords').value.trim();
  if (!keywords) { $('keywords').focus(); return; }
  setBusy(true, 'Queued…');
  $('results').innerHTML = '';
  try {
    const res = await fetch('/api/search', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keywords, max_products: parseInt($('max').value || '5', 10), source_china: $('china').checked, model: $('model').value }),
    });
    const data = await res.json();
    if (!res.ok) { setBusy(false); $('results').innerHTML = `<div class="card empty error">⚠️ ${data.error || 'Request failed.'}</div>`; return; }
    clearInterval(pollTimer);
    pollTimer = setInterval(() => poll(data.id), 1500);
    poll(data.id);
  } catch (err) { setBusy(false); $('results').innerHTML = `<div class="card empty error">⚠️ ${err.message}</div>`; }
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
