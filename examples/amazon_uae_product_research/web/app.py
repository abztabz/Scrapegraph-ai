"""
Amazon UAE Product Research — Web App
=====================================

A tiny, dependency-free web server (Python stdlib only) that wraps the
``AmazonUAEProductFinder`` so you can run product research from a phone
browser instead of the command line.

Why stdlib only? So you can just run::

    export OPENAI_APIKEY="sk-..."
    python app.py

…with nothing to install beyond ``scrapegraphai`` itself, then open the
printed URL on your iPhone (same Wi-Fi network as the computer running it).

Endpoints
---------
    GET  /                 -> the mobile-first single-page UI (index.html)
    POST /api/search       -> start a background research job, returns {id}
    GET  /api/status?id=.. -> job status, live progress, and results so far

Scraping several products takes a while, so each request runs as a
background job and the page polls ``/api/status`` for live progress and
incremental results — the phone never just hangs on a blank screen.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

# Make the parent tool importable whether you run from this dir or the repo root.
THIS_DIR = Path(__file__).resolve().parent
TOOL_DIR = THIS_DIR.parent
sys.path.insert(0, str(TOOL_DIR))

try:
    from amazon_uae_product_finder import (  # noqa: E402
        AmazonUAEProductFinder,
        ProductOpportunity,
        score,
    )
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Could not import amazon_uae_product_finder. Run this from the "
        "examples/amazon_uae_product_research/web directory, and make sure "
        "scrapegraphai is installed (`pip install scrapegraphai`)."
    ) from exc


INDEX_HTML = (THIS_DIR / "index.html").read_text(encoding="utf-8")

# In-memory job store. Fine for a single-user, run-it-on-your-laptop tool.
JOBS: Dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------


def build_graph_config(model: str, api_key: Optional[str]) -> dict:
    api_key = api_key or os.getenv("OPENAI_APIKEY") or os.getenv("OPENAI_API_KEY")
    if model.startswith("openai/") and not api_key:
        raise ValueError(
            "No OpenAI API key found. Set OPENAI_APIKEY / OPENAI_API_KEY before "
            "starting the server, or use an Ollama model (e.g. ollama/llama3.2)."
        )
    config: dict = {"llm": {"model": model}, "verbose": False, "headless": True}
    if api_key:
        config["llm"]["api_key"] = api_key
    serper = os.getenv("SERPER_API_KEY")
    if serper:
        config["serper_api_key"] = serper
        config["search_engine"] = "serper"
    return config


# ----------------------------------------------------------------------------
# Background job
# ----------------------------------------------------------------------------


def _set(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(fields)


def _append_result(job_id: str, opp: ProductOpportunity) -> None:
    with JOBS_LOCK:
        results = JOBS[job_id]["results"]
        results.append(asdict(opp))
        # Keep the list ranked so the phone always shows the best so far on top.
        results.sort(key=lambda r: r["opportunity_score"], reverse=True)
        JOBS[job_id]["done_count"] += 1


def run_job(job_id: str, keywords: List[str], max_products: int,
            source_china: bool, model: str, api_key: Optional[str]) -> None:
    """Mirror AmazonUAEProductFinder.find_products but stream results live."""
    try:
        graph_config = build_graph_config(model, api_key)
    except ValueError as exc:
        _set(job_id, status="error", message=str(exc))
        return

    def on_progress(message: str) -> None:
        if message:
            _set(job_id, message=message)

    finder = AmazonUAEProductFinder(
        graph_config=graph_config,
        source_china=source_china,
        verbose=True,
        progress_callback=on_progress,
    )

    _set(job_id, status="running", message="Starting…")
    try:
        for keyword in keywords:
            products = finder.search_keyword(keyword, max_products)
            with JOBS_LOCK:
                JOBS[job_id]["total"] += len(products)
            for product in products:
                detail = finder.enrich_product(product)
                opp = ProductOpportunity(
                    keyword=keyword,
                    title=detail.title or product.title,
                    url=finder._absolute_url(product.url),
                    asin=product.asin,
                    price=detail.price,
                    currency=product.currency or "AED",
                    rating=detail.rating,
                    reviews_count=detail.reviews_count,
                    bought_last_month=detail.bought_last_month,
                    bsr_rank=detail.bsr_rank,
                    bsr_category=detail.bsr_category,
                    seller_count=detail.seller_count,
                    brand=detail.brand,
                )
                if source_china:
                    supplier = finder.source_from_china(opp.title)
                    if supplier:
                        opp.china_platform = supplier.platform
                        opp.china_supplier = supplier.supplier_name
                        opp.china_url = supplier.product_url
                        opp.china_unit_price_usd = supplier.unit_price_usd
                score(opp, has_china_data=source_china)
                _append_result(job_id, opp)

        with JOBS_LOCK:
            count = len(JOBS[job_id]["results"])
        _set(job_id, status="done", message=f"Done — {count} products ranked.")
    except Exception as exc:  # surface the failure to the phone instead of dying
        _set(job_id, status="error", message=f"Failed: {exc}")


# ----------------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "AmazonUAEResearch/1.0"

    def log_message(self, *args) -> None:  # quieter console
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

    # -- routing -----------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/status":
            self._handle_status()
        elif path == "/health":
            self._send_json(200, {"ok": True})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/search":
            self._handle_search()
        else:
            self._send_json(404, {"error": "not found"})

    # -- handlers ----------------------------------------------------------
    def _handle_search(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        raw_keywords = data.get("keywords", "")
        if isinstance(raw_keywords, str):
            keywords = [k.strip() for k in raw_keywords.replace("\n", ",").split(",") if k.strip()]
        else:
            keywords = [str(k).strip() for k in raw_keywords if str(k).strip()]
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
                "status": "queued",
                "message": "Queued…",
                "results": [],
                "total": 0,
                "done_count": 0,
                "keywords": keywords,
                "source_china": source_china,
            }
        threading.Thread(
            target=run_job,
            args=(job_id, keywords, max_products, source_china, model, api_key),
            daemon=True,
        ).start()
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


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------


def _lan_ip() -> str:
    """Best-effort local network IP so you know what to type on the phone."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main() -> int:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Handler)
    ip = _lan_ip()
    print("\n📱 Amazon UAE Product Research — web app")
    print("=" * 48)
    print(f"  On this computer:  http://localhost:{port}")
    print(f"  On your iPhone:    http://{ip}:{port}")
    print("  (iPhone must be on the same Wi-Fi network)")
    print("=" * 48)
    print("  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
