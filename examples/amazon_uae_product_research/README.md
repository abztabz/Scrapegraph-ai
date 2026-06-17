# Amazon UAE Product Research Tool

A product-research / product-sourcing tool built on top of **ScrapeGraphAI**.

It answers the questions you actually ask before deciding what to sell on
**Amazon UAE (amazon.ae)**:

- **How many sales a month?** → `bought in past month` units
- **How many competitors?** → number of sellers sharing the listing
- **How many reviews?** → how entrenched the incumbents are
- **What's the BSR / rating?** → demand within the category + the quality gap

…and then, because *“once you've found an item, buying it in China is the
easiest thing to do”*, it can search **Alibaba / 1688 / DHgate** for a supplier
and estimate your gross margin.

Every candidate gets a single **opportunity score (0–100)** so the best
products to sell float to the top.

---

## How it works

```
seed keyword ──► Amazon UAE search page ──► candidate products
                                              │
                                              ▼
                              product detail page (BSR, sellers,
                                  monthly sales, reviews, rating)
                                              │
                       (optional) ──► Alibaba/1688/DHgate supplier
                                              │
                                              ▼
                          opportunity score  ──►  ranked shortlist
                                                   (console + CSV + JSON)
```

The HTML→structured-data extraction is done by ScrapeGraphAI's
`SmartScraperGraph` (per page) and `SearchGraph` (China sourcing). This tool
adds the Amazon-specific extraction schemas, the scoring model, and the CLI.

### The opportunity score

| Sub-score      | Rewards                                  | Built from                |
| -------------- | ---------------------------------------- | ------------------------- |
| **Demand**     | high sales, strong (small) BSR           | `bought_last_month`, `bsr_rank` |
| **Competition**| *few* reviews and *few* sellers          | `reviews_count`, `seller_count` (inverted) |
| **Margin**     | big gap between AED price and China cost  | Amazon price vs `unit_price_usd` |

```
score = 0.45·demand + 0.35·competition + 0.20·margin
```

The idea: a great product to sell has **proven demand** but is **not yet
crowded** with reviewed competitors — and ideally leaves room for a healthy
markup over China cost. When `--source-china` is off, the margin weight is
redistributed across demand + competition. Weights live at the top of
`amazon_uae_product_finder.py` if you want to tune them.

---

## Install

```bash
pip install scrapegraphai
playwright install            # needed to fetch pages
```

## Configure credentials

```bash
export OPENAI_APIKEY="sk-..."          # or OPENAI_API_KEY
# optional, for better China sourcing search:
export SERPER_API_KEY="..."
```

You can also run fully local with Ollama (`--model ollama/llama3.2`) and no key.

## Run

```bash
# Basic: research two niches, 5 products each
python amazon_uae_product_finder.py --keywords "yoga mat" "resistance bands"

# Full run with China sourcing + margin estimate
python amazon_uae_product_finder.py \
    --keywords "car phone holder" "led strip lights" \
    --max-products 8 \
    --source-china \
    --output my_shortlist

# Drive it from a file of niches (one per line, '#' for comments)
python amazon_uae_product_finder.py --keywords-file keywords.example.txt --source-china
```

### Output

- A ranked table printed to the console (top `--top`, default 10).
- `<output>.csv` and `<output>.json` with every evaluated product and all
  sub-scores, for sorting/filtering in a spreadsheet.

---

## 📱 Web app (use it from your iPhone)

Prefer tapping a button to typing CLI flags? There's a mobile-first web UI in
[`web/`](web/) that wraps the same finder. It needs **no extra dependencies** —
just Python's standard library and `scrapegraphai`.

```bash
export OPENAI_APIKEY="sk-..."        # or run a local ollama model
cd web
python app.py
```

It prints two URLs:

```
On this computer:  http://localhost:8000
On your iPhone:    http://192.168.x.x:8000   <-- open this on your phone
```

Open the second URL in Safari on your iPhone (the phone must be on the **same
Wi-Fi network** as the computer running the server). Tip: in Safari use
*Share → Add to Home Screen* to get an app-like icon.

#### 📷 Scan-to-open (QR code)

So you don't have to type the IP, the landing page shows an **“Open on your
iPhone” card with a QR code** — point your Camera app at it and tap the
notification. The QR is generated locally and rendered as a crisp SVG.

This uses the tiny, pure-Python [`qrcode`](https://pypi.org/project/qrcode/)
package. It's **optional** — the app runs fine without it and just shows the
URL to type. To enable the QR:

```bash
pip install qrcode
```

### What you get

- A touch-friendly form: keywords, products-per-keyword, model, and a
  “find China supplier & estimate margin” toggle.
- A **live progress bar** while it scrapes (research takes a while, so the page
  polls in the background instead of hanging).
- Ranked **result cards** with a colour-coded opportunity score, the key
  metrics (price, monthly sales, reviews, rating, BSR, competitors), the
  demand / low-competition / margin sub-score bars, the China sourcing line,
  and links to the Amazon listing and supplier.

### How it works

```
iPhone (Safari)  ──HTTP──►  app.py (stdlib http.server)
                               │  POST /api/search  -> starts a background job
                               │  GET  /api/status  -> live progress + results
                               ▼
                     AmazonUAEProductFinder  (the same engine as the CLI)
```

Jobs run in a background thread and results stream in ranked order, so the best
products surface on top as they're found. The server binds to `0.0.0.0:8000` by
default; override with `HOST` / `PORT` env vars.

> **Heads-up:** this is a lightweight single-user tool meant to run on your own
> machine/LAN. It has no authentication — don't expose it to the public
> internet. For sharing, put it behind a reverse proxy with auth, or use the
> hosted ScrapeGraphAI API.

## CLI options

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--keywords` | — | One or more seed keywords (mutually exclusive with `--keywords-file`). |
| `--keywords-file` | — | Text file, one keyword per line. |
| `--max-products` | 5 | Products evaluated per keyword. |
| `--source-china` | off | Also search Alibaba/1688/DHgate and estimate margin. |
| `--model` | `openai/gpt-4o-mini` | Any ScrapeGraphAI-supported model. |
| `--api-key` | env | LLM API key (else from `OPENAI_APIKEY`/`OPENAI_API_KEY`). |
| `--serper-api-key` | env | Serper.dev key for sourcing search. |
| `--output` | `amazon_uae_shortlist` | Output basename for `.csv`/`.json`. |
| `--top` | 10 | Rows printed to console. |
| `--quiet` | off | Reduce logging. |

---

## Notes & caveats

- **Be polite & legal.** Respect Amazon's Terms of Service and `robots.txt`,
  keep request volume low, and use the data for research only. Consider
  ScrapeGraphAI's hosted API or an official product-data API for scale.
- Amazon does not always expose BSR, seller count, or “bought in past month”
  on every listing; missing signals degrade gracefully (the score falls back to
  neutral values and notes which signals were missing).
- Monthly-sales and margin figures are **estimates** to rank candidates, not
  precise forecasts. Always validate a shortlist manually before sourcing.
- Prices are AED on Amazon UAE and USD for China suppliers; margin uses the
  pegged rate `AED_PER_USD = 3.6725`.
