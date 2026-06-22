"""
UAE Rent Watch — Web App
========================

A tiny, dependency-free web server (Python stdlib only) that wraps the
``RentWatcher`` so you can manage your watchlist and check rents from a phone
browser instead of the command line.

Why stdlib only? So you can just run::

    python app.py

…with nothing to install beyond ``scrapegraphai`` itself, then open the printed
URL on your phone (same Wi-Fi network as the computer running it).

Endpoints
---------
    GET  /                  -> the mobile-first single-page UI (index.html)
    GET  /api/watchlist     -> the saved watchlist + latest known rents
    POST /api/watchlist     -> save the watchlist (areas you want to track)
    POST /api/check         -> start a background "check rents now" job
    GET  /api/status?id=..  -> job status, live progress, and changes so far

Checking several areas takes a while, so each check runs as a background job and
the page polls ``/api/status`` for live progress — the phone never just hangs.
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
    from uae_rent_watch import RentWatcher, WatchItem, resolve_api_key  # noqa: E402
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Could not import uae_rent_watch. Run this from the "
        "examples/uae_rent_watch/web directory, and make sure scrapegraphai is "
        "installed (`pip install scrapegraphai`)."
    ) from exc


INDEX_HTML = (THIS_DIR / "index.html").read_text(encoding="utf-8")

# Where the web app keeps the user's watchlist and rent history.
WATCHLIST_FILE = os.getenv("WATCHLIST_FILE", str(TOOL_DIR / "web_watchlist.json"))
STATE_FILE = os.getenv("STATE_FILE", str(TOOL_DIR / "rent_state.json"))
DEFAULT_MODEL = os.getenv("RENT_MODEL", "ollama/llama3.2")
DEFAULT_THRESHOLD = float(os.getenv("RENT_THRESHOLD", "3.0"))

# In-memory job store. Fine for a single-user, run-it-on-your-laptop tool.
JOBS: Dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


# ----------------------------------------------------------------------------
# Watchlist persistence
# ----------------------------------------------------------------------------


def read_watchlist() -> List[dict]:
    if not os.path.exists(WATCHLIST_FILE):
        return []
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        items = data.get("watchlist", data) if isinstance(data, dict) else data
        return [i for i in items if isinstance(i, dict) and i.get("area")]
    except (OSError, json.JSONDecodeError):
        return []


def write_watchlist(items: List[dict]) -> None:
    clean: List[dict] = []
    for entry in items:
        if not isinstance(entry, dict) or not str(entry.get("area", "")).strip():
            continue
        clean.append(
            {
                "area": str(entry["area"]).strip(),
                "city": str(entry.get("city", "Dubai")).strip() or "Dubai",
                "property_type": str(entry.get("property_type", "apartment")).strip()
                or "apartment",
                "bedrooms": (str(entry["bedrooms"]).strip() if entry.get("bedrooms") else None),
                "url": (str(entry["url"]).strip() if entry.get("url") else None),
            }
        )
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as handle:
        json.dump({"watchlist": clean}, handle, indent=2, ensure_ascii=False)


def read_state() -> Dict[str, dict]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data.get("areas", {}) if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def watch_items_from(raw: List[dict]) -> List[WatchItem]:
    items: List[WatchItem] = []
    for entry in raw:
        items.append(
            WatchItem(
                area=str(entry["area"]).strip(),
                city=str(entry.get("city", "Dubai")).strip() or "Dubai",
                property_type=str(entry.get("property_type", "apartment")).strip()
                or "apartment",
                bedrooms=(str(entry["bedrooms"]).strip() if entry.get("bedrooms") else None),
                url=(str(entry["url"]).strip() if entry.get("url") else None),
            )
        )
    return items


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------


def build_graph_config(model: str, api_key: Optional[str]) -> dict:
    api_key = resolve_api_key(api_key)
    if not model.startswith("ollama/") and not api_key:
        raise ValueError(
            f"No API key found for model '{model}'. Set OPENAI_API_KEY / "
            "GEMINI_API_KEY / GROQ_API_KEY before starting the server, or use a "
            "free local model (e.g. ollama/llama3.2)."
        )
    config: dict = {"llm": {"model": model}, "verbose": False, "headless": True}
    if api_key:
        config["llm"]["api_key"] = api_key
    return config


# ----------------------------------------------------------------------------
# Background job
# ----------------------------------------------------------------------------


def _set(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(fields)


def run_job(job_id: str, model: str, api_key: Optional[str], threshold: float) -> None:
    try:
        graph_config = build_graph_config(model, api_key)
    except ValueError as exc:
        _set(job_id, status="error", message=str(exc))
        return

    items = watch_items_from(read_watchlist())
    if not items:
        _set(job_id, status="error", message="Your watchlist is empty. Add an area first.")
        return

    def on_progress(message: str) -> None:
        if message:
            _set(job_id, message=message)

    watcher = RentWatcher(
        graph_config=graph_config,
        state_file=STATE_FILE,
        threshold_pct=threshold,
        verbose=True,
        progress_callback=on_progress,
    )

    _set(job_id, status="running", message="Starting…", total=len(items))
    try:
        previous = watcher.load_state()
        snapshots = []
        for item in items:
            snapshot = watcher.scrape_area(item)
            snapshots.append(snapshot)
            change = watcher.diff(previous, snapshot)
            with JOBS_LOCK:
                JOBS[job_id]["results"].append(
                    {**asdict(change), "notable": watcher.is_notable(change)}
                )
                JOBS[job_id]["done_count"] += 1
        watcher.save_state(snapshots)
        _set(
            job_id,
            status="done",
            message=f"Done — checked {len(items)} area(s).",
        )
    except Exception as exc:  # surface the failure to the phone instead of dying
        _set(job_id, status="error", message=f"Failed: {exc}")


# ----------------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "UAERentWatch/1.0"

    def log_message(self, *args) -> None:  # quieter console
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return None

    # -- routing -----------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/watchlist":
            self._send_json(200, {"watchlist": read_watchlist(), "latest": read_state()})
        elif path == "/api/status":
            self._handle_status()
        elif path == "/health":
            self._send_json(200, {"ok": True})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/watchlist":
            self._handle_save_watchlist()
        elif path == "/api/check":
            self._handle_check()
        else:
            self._send_json(404, {"error": "not found"})

    # -- handlers ----------------------------------------------------------
    def _handle_save_watchlist(self) -> None:
        data = self._read_json()
        if data is None:
            self._send_json(400, {"error": "invalid JSON body"})
            return
        items = data.get("watchlist", []) if isinstance(data, dict) else data
        if not isinstance(items, list):
            self._send_json(400, {"error": "watchlist must be a list"})
            return
        write_watchlist(items)
        self._send_json(200, {"watchlist": read_watchlist()})

    def _handle_check(self) -> None:
        data = self._read_json() or {}
        model = str(data.get("model") or DEFAULT_MODEL)
        api_key = data.get("api_key") or None
        threshold = float(data.get("threshold", DEFAULT_THRESHOLD))

        job_id = uuid.uuid4().hex
        with JOBS_LOCK:
            JOBS[job_id] = {
                "status": "queued",
                "message": "Queued…",
                "results": [],
                "total": 0,
                "done_count": 0,
            }
        threading.Thread(
            target=run_job,
            args=(job_id, model, api_key, threshold),
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
    print("\n🏠 UAE Rent Watch — web app")
    print("=" * 48)
    print(f"  On this computer:  http://localhost:{port}")
    print(f"  On your phone:     http://{ip}:{port}")
    print("  (phone must be on the same Wi-Fi network)")
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
