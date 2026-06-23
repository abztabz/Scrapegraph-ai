"""
Standalone ScrapeGraphAI hosted-API smoke test.

Run it directly to see EXACTLY what the v2 API returns for a Bayut page:

    python test_sgai.py

It needs SGAI_API_KEY in the environment (or edit API_KEY below).
This deliberately does NOT import uae_rent_watch.py, so there is no doubt
about which code is running.
"""

import os
import sys

API_KEY = os.getenv("SGAI_API_KEY", "")
URL = "https://www.bayut.com/to-rent/apartments/dubai/?query=Dubai+Marina+Dubai&beds=1"
PROMPT = (
    "This is a UAE property portal page listing apartments for rent. "
    "Read the rental prices and report the average yearly asking rent in AED "
    "as a plain number, plus how many listings you counted."
)

print("Python:", sys.version)
print("API key present:", bool(API_KEY), "(length:", len(API_KEY), ")")

try:
    import scrapegraph_py
    print("scrapegraph_py version:", getattr(scrapegraph_py, "__version__", "unknown"))
    print("scrapegraph_py exports:", [n for n in dir(scrapegraph_py) if not n.startswith("_")])
except Exception as exc:
    print("Could not import scrapegraph_py:", repr(exc))
    sys.exit(1)

from scrapegraph_py import ScrapeGraphAI  # noqa: E402

client = ScrapeGraphAI(api_key=API_KEY)
print("\nClient methods:", [n for n in dir(client) if not n.startswith("_")])

print("\nCalling client.extract() ...")
try:
    result = client.extract(prompt=PROMPT, url=URL)
    print("\n=== RAW RESULT ===")
    print("type:", type(result))
    print("repr:", repr(result))
    print("status attr:", getattr(result, "status", "<none>"))
    print("data attr:", getattr(result, "data", "<none>"))
    print("error attr:", getattr(result, "error", "<none>"))
except Exception as exc:
    print("\n=== extract() raised ===")
    print(repr(exc))
