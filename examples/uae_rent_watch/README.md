# 🏠 UAE Rent Watch

A simple **"let me know when the rent changes"** tool for the UAE, built on
**ScrapeGraphAI**.

You keep a **watchlist** of the areas you care about — *Dubai Marina*, *JVC*,
*Al Reem Island in Abu Dhabi*, *Al Nahda in Sharjah*… — and this tool checks the
typical asking rent for each one, notices when it goes **up or down**, and
**tells you**. Nice and simple.

It's designed to cost **nothing to run** and to need **no coding**:

- 🆓 the AI can be a **free local model** (Ollama) or a **free-tier API** (Google
  Gemini, Groq) — no bill;
- 📧 alerts arrive by **email** (a free Gmail "App Password" works);
- ⏰ it can run **automatically every day for free** on **GitHub Actions** — no
  server, no computer left on.

> **Plain-English version:** add the areas you like, press a button, and get an
> email whenever the rent there moves.

---

## What you get

```
your watchlist ──► property portal "to-rent" page per area
                           │
                           ▼
            ScrapeGraphAI reads the typical asking rent
              (average, median, cheapest, dearest)
                           │
                 compare with last time
                           │
                           ▼
        rent went up/down?  ──►  notify you (screen + file + email)
```

There are **three ways** to use it — pick whichever suits you:

| Way | Best for | Needs |
| --- | --- | --- |
| 📱 **Web app** | tapping buttons on your phone | run one command on a computer |
| ⌨️ **Command line** | a quick one-off check | one command |
| ⏰ **Daily auto-check** | "just email me, hands-off" | a free GitHub account |

---

## 1. Install (once)

```bash
pip install scrapegraphai
playwright install            # lets it open the property pages
```

### Pick a free AI model

- **Totally free, on your own computer:** install [Ollama](https://ollama.com),
  then `ollama pull llama3.2`. Use `--model ollama/llama3.2` (the default).
- **Most reliable (best for the daily auto-check): the ScrapeGraphAI hosted API.**
  It renders JavaScript and handles anti-bot pages *server-side*, so no local
  browser or LLM is needed — ideal for big portals like Bayut / Property Finder.
  Get a free key at [scrapegraphai.com](https://scrapegraphai.com), then:
  ```bash
  export SGAI_API_KEY="sgai-..."
  python uae_rent_watch.py --watchlist my_watchlist.json --sgai
  ```
- **Bring-your-own LLM (DIY scraping):** a free local model via
  [Ollama](https://ollama.com) (`--model ollama/llama3.2`), or a cloud key such as
  [Groq](https://console.groq.com) (`--model groq/llama-3.3-70b-versatile`,
  worldwide free tier) or Google Gemini (`--model google_genai/gemini-2.0-flash`,
  free tier not available in every country). Keys are read from env
  (`GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, …).

---

## 2. Your watchlist

Copy the example and edit it — it's just a list of areas, no code:

```bash
cp watchlist.example.json my_watchlist.json
```

```json
{
  "watchlist": [
    { "area": "Dubai Marina", "city": "Dubai", "property_type": "apartment", "bedrooms": "1", "portal": "bayut" },
    { "area": "Jumeirah Village Circle", "city": "Dubai", "bedrooms": "2", "portal": "propertyfinder" },
    { "area": "Al Reem Island", "city": "Abu Dhabi", "property_type": "apartment" }
  ]
}
```

Only `area` is required. `city` defaults to *Dubai*. `property_type` can be
`apartment`, `studio`, `villa` or `townhouse`. `bedrooms` is optional.

`portal` chooses where the rent is read from — **`bayut`** (default),
**`propertyfinder`** or **`dubizzle`**, the biggest UAE listing sites. Watch the
*same* area on more than one to compare them side by side (each is tracked
separately). You can also paste a portal `url` if you want to control the exact
page that's read.

---

## 3a. Run it from your phone (web app)

```bash
cd web
python app.py
```

It prints two links:

```
On this computer:  http://localhost:8000
On your phone:     http://192.168.x.x:8000   <-- open this on your phone
```

Open the second link in your phone's browser (phone must be on the **same Wi-Fi**
as the computer). Then **add areas**, press **"Check my areas now"**, and watch
the results come in. Tip: in Safari/Chrome use *Add to Home Screen* for an
app-like icon.

The web app saves your watchlist and remembers the latest rents for you.

## 3b. Run it from the command line

```bash
# Try it instantly with built-in sample data — no network, no API key.
# Run it twice to see real rent changes get flagged the second time.
python uae_rent_watch.py --demo

# free local model (real data)
python uae_rent_watch.py --watchlist my_watchlist.json --model ollama/llama3.2

# free-tier Gemini, and email me the result
export SMTP_HOST=smtp.gmail.com SMTP_PORT=587
export SMTP_USER="you@gmail.com" SMTP_PASSWORD="your-gmail-app-password"
export NOTIFY_EMAIL="you@gmail.com"
python uae_rent_watch.py \
    --watchlist my_watchlist.json \
    --model google_genai/gemini-2.0-flash \
    --email
```

It prints a report, writes `rent_report.txt`, and remembers what it saw in
`rent_state.json` so that **next time** it can tell you what changed.

## 3c. Get a free daily email automatically (no computer needed)

Use the ready-made GitHub Actions workflow in
[`github-action.example.yml`](github-action.example.yml). It runs on GitHub's
free servers once a day, emails you any changes, and remembers the rents between
runs. The file's top comment walks you through it step by step (copy it to
`.github/workflows/`, add a few secrets, done).

---

## Getting the email working (free)

Emails go out over standard SMTP. The easiest free option is **Gmail**:

1. Turn on 2-Step Verification on your Google account.
2. Create an **App Password** (Google Account ▸ Security ▸ App passwords).
3. Set these (in your shell, or as GitHub secrets for the daily run):

| Variable | Value |
| --- | --- |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | the **App Password** (not your normal password) |
| `NOTIFY_EMAIL` | where to send alerts (can be the same Gmail) |

Leave `--email` off if you just want it printed to the screen and saved to a file.

---

## Command-line options

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--watchlist` | `watchlist.example.json` | Your list of areas (JSON). |
| `--portal` | `bayut` | Default source portal (`bayut`, `propertyfinder` or `dubizzle`) for areas that don't set their own. |
| `--model` | `ollama/llama3.2` | LLM to use (free local, or `google_genai/gemini-2.0-flash`, etc.). |
| `--api-key` | env | API key for paid/cloud models (else read from env). |
| `--demo` | off | Use built-in sample data (no network/API key) so you can try it. |
| `--threshold` | `3.0` | Only flag rent moves at least this big (%). |
| `--email` | off | Also email the report (needs the `SMTP_*` vars above). |
| `--state-file` | `rent_state.json` | Where it remembers previous rents. |
| `--report` | `rent_report.txt` | Where it writes the text report. |
| `--quiet` | off | Less logging. |

---

## How the numbers are worked out

- For each area it opens that area's **to-rent** listings page and asks the AI to
  read back the **average**, **median**, cheapest and dearest **yearly** asking
  rent, plus how many listings it saw.
- It tracks the **median** (most typical) rent where available — it's less
  thrown off by one unusually cheap/expensive listing than the average.
- A change is "notable" if it moves at least `--threshold` percent (default 3%).
  The very first reading of a new area is always reported so you know the
  baseline.
- If a page can't be read on a given day, the last known value is kept rather
  than wiped, so a one-off hiccup won't look like a giant price swing.

---

## Notes & caveats

- **Asking rents, not contract rents.** This reads *listing* (asking) prices,
  which is a good early indicator of where a market is heading, but it isn't the
  official registered/contract rent. For official figures and the legal rent-cap
  picture, see the **Dubai Land Department / RERA Rental Index** and the relevant
  emirate's authority.
- **Be polite & legal.** Respect each portal's Terms of Service and `robots.txt`,
  keep the volume low (a once-a-day check is plenty), and use the data for your
  own personal research. For anything heavier, use ScrapeGraphAI's hosted API or
  an official data feed.
- **Estimates, not guarantees.** The figures are there to *flag movement* in
  your areas, not to value a specific flat. Always check the actual listings
  (the report includes the link) before making a decision.
- The web app is a lightweight single-user tool meant to run on your own
  machine/LAN; it has no login, so don't expose it to the public internet.
