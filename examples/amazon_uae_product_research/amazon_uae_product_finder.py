"""
Amazon UAE Product Research Tool
================================

A product-research / product-sourcing tool built on top of ScrapeGraphAI.

Given one or more seed keywords (e.g. "yoga mat", "car phone holder"), it:

1. Scrapes the Amazon UAE (amazon.ae) search results for each keyword.
2. Enriches every candidate product by scraping its detail page for the
   signals that actually decide whether a product is worth selling:
       * monthly sales      ("xxx+ bought in past month")
       * number of reviews  (proxy for how entrenched competitors are)
       * star rating        (customer satisfaction / quality gap)
       * BSR                (Best Sellers Rank -> demand within a category)
       * number of sellers  (how many competitors share the buy box)
3. (Optional) Finds a China supplier (Alibaba / 1688 / DHgate) so you can
   estimate landed cost and gross margin -- "buying it in China is the
   easiest thing to do".
4. Scores every product with an **opportunity score** that rewards high
   demand + low competition + healthy margin, then prints and exports a
   ranked shortlist (CSV + JSON).

The heavy lifting (HTML -> structured data) is done by ScrapeGraphAI's
``SmartScraperGraph`` and ``SearchGraph``; this module only adds the
Amazon-specific schemas, the scoring model and the CLI glue.

Usage
-----
    export OPENAI_APIKEY="sk-..."

    python amazon_uae_product_finder.py \
        --keywords "yoga mat" "resistance bands" \
        --max-products 5 \
        --source-china \
        --output shortlist

See README.md for full documentation.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional
from urllib.parse import quote_plus

from pydantic import BaseModel, Field

try:
    from scrapegraphai.graphs import SearchGraph, SmartScraperGraph
except ImportError as exc:  # pragma: no cover - helpful message when run standalone
    raise SystemExit(
        "scrapegraphai is not installed. Install it with `pip install scrapegraphai` "
        "(and run `playwright install`) before using this tool."
    ) from exc


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

AMAZON_UAE_DOMAIN = "https://www.amazon.ae"
DEFAULT_CURRENCY = "AED"


# ----------------------------------------------------------------------------
# Extraction schemas (what ScrapeGraphAI should pull out of each page)
# ----------------------------------------------------------------------------


class SearchResultProduct(BaseModel):
    """A single product as it appears on an Amazon UAE search-results page."""

    asin: Optional[str] = Field(
        default=None, description="The Amazon ASIN / product id if visible."
    )
    title: str = Field(description="The product title.")
    url: Optional[str] = Field(
        default=None, description="Absolute or relative link to the product page."
    )
    price: Optional[float] = Field(
        default=None, description="Current price as a number, no currency symbol."
    )
    currency: Optional[str] = Field(
        default=DEFAULT_CURRENCY, description="Currency code, usually AED."
    )
    rating: Optional[float] = Field(
        default=None, description="Average star rating out of 5."
    )
    reviews_count: Optional[int] = Field(
        default=None, description="Total number of ratings/reviews."
    )
    bought_last_month: Optional[int] = Field(
        default=None,
        description="Units bought in the past month, e.g. '500+ bought in past "
        "month' -> 500. Null if not shown.",
    )
    is_sponsored: Optional[bool] = Field(
        default=None, description="True if the listing is a sponsored ad."
    )


class SearchResults(BaseModel):
    """The full list of products scraped from a search-results page."""

    products: List[SearchResultProduct] = Field(default_factory=list)


class ProductDetail(BaseModel):
    """The deeper signals scraped from a single product detail page."""

    title: Optional[str] = Field(default=None, description="Product title.")
    brand: Optional[str] = Field(default=None, description="Brand / manufacturer.")
    price: Optional[float] = Field(default=None, description="Current price as a number.")
    rating: Optional[float] = Field(default=None, description="Average star rating /5.")
    reviews_count: Optional[int] = Field(
        default=None, description="Total number of ratings/reviews."
    )
    bought_last_month: Optional[int] = Field(
        default=None,
        description="Units bought in the past month, e.g. '1K+ bought in past "
        "month' -> 1000. Null if not shown.",
    )
    bsr_rank: Optional[int] = Field(
        default=None,
        description="Best Sellers Rank number from 'Best Sellers Rank' section, "
        "e.g. '#1,234 in Sports' -> 1234. Use the top-level (smallest) rank.",
    )
    bsr_category: Optional[str] = Field(
        default=None, description="The category the BSR rank refers to."
    )
    seller_count: Optional[int] = Field(
        default=None,
        description="Number of sellers / offers for this product (competitors "
        "sharing the listing). Look for 'New & Used' or 'other sellers'.",
    )


class ChinaSupplier(BaseModel):
    """A China-sourcing option found via web search."""

    platform: Optional[str] = Field(
        default=None, description="Sourcing platform, e.g. Alibaba, 1688, DHgate."
    )
    supplier_name: Optional[str] = Field(default=None, description="Supplier/store name.")
    product_url: Optional[str] = Field(default=None, description="Link to the listing.")
    unit_price_usd: Optional[float] = Field(
        default=None, description="Lowest unit price in USD."
    )
    min_order_qty: Optional[int] = Field(
        default=None, description="Minimum order quantity (MOQ)."
    )


class ChinaSourcing(BaseModel):
    """A small list of China-sourcing options for one product."""

    suppliers: List[ChinaSupplier] = Field(default_factory=list)


# ----------------------------------------------------------------------------
# Result record + scoring
# ----------------------------------------------------------------------------


@dataclass
class ProductOpportunity:
    """A fully-enriched and scored product candidate."""

    keyword: str
    title: str
    url: Optional[str] = None
    asin: Optional[str] = None
    price: Optional[float] = None
    currency: str = DEFAULT_CURRENCY
    rating: Optional[float] = None
    reviews_count: Optional[int] = None
    bought_last_month: Optional[int] = None
    bsr_rank: Optional[int] = None
    bsr_category: Optional[str] = None
    seller_count: Optional[int] = None
    brand: Optional[str] = None

    # sourcing
    china_unit_price_usd: Optional[float] = None
    china_platform: Optional[str] = None
    china_supplier: Optional[str] = None
    china_url: Optional[str] = None
    estimated_margin_pct: Optional[float] = None

    # scoring (filled in by score())
    demand_score: float = 0.0
    competition_score: float = 0.0
    margin_score: float = 0.0
    opportunity_score: float = 0.0
    notes: List[str] = field(default_factory=list)


# Rough AED -> USD conversion for margin estimation (peg is ~3.6725).
AED_PER_USD = 3.6725

# Opportunity score weights. Demand + low competition are the core idea;
# margin only contributes when China sourcing data is available.
WEIGHT_DEMAND = 0.45
WEIGHT_COMPETITION = 0.35
WEIGHT_MARGIN = 0.20


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _demand_score(opp: ProductOpportunity) -> float:
    """Higher monthly sales and a stronger (smaller) BSR => more demand."""
    score = 0.0
    has_signal = False

    if opp.bought_last_month is not None:
        has_signal = True
        # 0 -> 0pts, 50 -> ~39pts, 500 -> ~73pts, 2000+ -> ~100pts (gentle ramp).
        sales = max(opp.bought_last_month, 0)
        score = max(score, _clamp(13.5 * (sales ** 0.27)))

    if opp.bsr_rank is not None and opp.bsr_rank > 0:
        has_signal = True
        # #1 -> ~100, #1,000 -> ~60, #100,000 -> ~20. Smaller rank is better.
        import math

        bsr_score = _clamp(100.0 - 13.0 * math.log10(opp.bsr_rank))
        score = max(score, bsr_score)

    if not has_signal:
        # No demand evidence at all -> neutral-low so it doesn't float to the top.
        opp.notes.append("no demand signal (sales/BSR) found")
        return 25.0

    return _clamp(score)


def _competition_score(opp: ProductOpportunity) -> float:
    """
    Fewer reviews and fewer sellers => easier to compete. We *invert* the
    competition signals so a high score means a low-competition opportunity.
    """
    score = 100.0
    has_signal = False

    if opp.reviews_count is not None:
        has_signal = True
        reviews = max(opp.reviews_count, 0)
        # 0 reviews -> 100, 100 -> ~74, 1,000 -> ~48, 10,000 -> ~22.
        import math

        review_pressure = 26.0 * math.log10(reviews + 1)
        score = min(score, _clamp(100.0 - review_pressure))

    if opp.seller_count is not None and opp.seller_count > 0:
        has_signal = True
        # 1 seller -> 100, 5 -> ~70, 10 -> ~50, 20+ -> ~20.
        seller_penalty = 5.0 * (opp.seller_count - 1)
        score = min(score, _clamp(100.0 - seller_penalty))

    if not has_signal:
        opp.notes.append("no competition signal (reviews/sellers) found")
        return 50.0

    return _clamp(score)


def _margin_score(opp: ProductOpportunity) -> float:
    """Gross margin between Amazon UAE price and China landed cost."""
    if opp.price is None or opp.china_unit_price_usd is None:
        return 0.0  # contributes nothing if we can't estimate it

    price_usd = opp.price / AED_PER_USD
    if price_usd <= 0:
        return 0.0

    margin = (price_usd - opp.china_unit_price_usd) / price_usd
    opp.estimated_margin_pct = round(margin * 100.0, 1)

    # 0% -> 0pts, 50% -> 50pts, 70%+ -> ~100pts.
    return _clamp(margin * 140.0)


def score(opp: ProductOpportunity, has_china_data: bool) -> ProductOpportunity:
    """Compute demand/competition/margin sub-scores and the blended score."""
    opp.demand_score = round(_demand_score(opp), 1)
    opp.competition_score = round(_competition_score(opp), 1)
    opp.margin_score = round(_margin_score(opp), 1)

    if has_china_data:
        blended = (
            WEIGHT_DEMAND * opp.demand_score
            + WEIGHT_COMPETITION * opp.competition_score
            + WEIGHT_MARGIN * opp.margin_score
        )
    else:
        # Re-normalise demand + competition to fill the margin weight.
        total = WEIGHT_DEMAND + WEIGHT_COMPETITION
        blended = (
            (WEIGHT_DEMAND / total) * opp.demand_score
            + (WEIGHT_COMPETITION / total) * opp.competition_score
        )

    opp.opportunity_score = round(_clamp(blended), 1)
    return opp


# ----------------------------------------------------------------------------
# The finder
# ----------------------------------------------------------------------------


class AmazonUAEProductFinder:
    """Orchestrates search -> enrich -> source -> score for Amazon UAE."""

    def __init__(
        self,
        graph_config: dict,
        source_china: bool = False,
        verbose: bool = True,
        progress_callback: Optional[Callable[[str], None]] = None,
    ):
        self.graph_config = graph_config
        self.source_china = source_china
        self.verbose = verbose
        # Optional hook so a UI (e.g. the web app) can surface live progress.
        self.progress_callback = progress_callback

    # -- logging -----------------------------------------------------------
    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)
        if self.progress_callback is not None:
            self.progress_callback(message.strip())

    # -- step 1: search ----------------------------------------------------
    def search_keyword(self, keyword: str, max_products: int) -> List[SearchResultProduct]:
        search_url = f"{AMAZON_UAE_DOMAIN}/s?k={quote_plus(keyword)}"
        self._log(f"\n🔎 Searching Amazon UAE for '{keyword}' -> {search_url}")

        graph = SmartScraperGraph(
            prompt=(
                f"This is an Amazon UAE search results page. Extract up to "
                f"{max_products} organic (non-sponsored if possible) products. "
                "For each product return the title, the product page url (make it "
                "absolute, starting with https://www.amazon.ae), the ASIN if "
                "present, the price as a number, the star rating, the number of "
                "reviews, and the 'bought in past month' count if shown."
            ),
            source=search_url,
            config=self.graph_config,
            schema=SearchResults,
        )
        result = graph.run()
        products = self._coerce_products(result)
        self._log(f"   found {len(products)} candidate products")
        return products[:max_products]

    @staticmethod
    def _coerce_products(result) -> List[SearchResultProduct]:
        """SmartScraperGraph may return a dict or a pydantic model; normalise it."""
        if result is None:
            return []
        if isinstance(result, SearchResults):
            return result.products
        if isinstance(result, BaseModel):
            result = result.model_dump()
        if isinstance(result, dict):
            raw = result.get("products", result.get("content", []))
        elif isinstance(result, list):
            raw = result
        else:
            return []

        products: List[SearchResultProduct] = []
        for item in raw or []:
            if isinstance(item, SearchResultProduct):
                products.append(item)
            elif isinstance(item, dict):
                try:
                    products.append(SearchResultProduct(**item))
                except Exception:
                    # title is the only required field; skip malformed rows.
                    if item.get("title"):
                        products.append(SearchResultProduct(title=item["title"]))
        return products

    # -- step 2: enrich ----------------------------------------------------
    def enrich_product(self, product: SearchResultProduct) -> ProductDetail:
        url = self._absolute_url(product.url)
        if not url:
            return ProductDetail(
                title=product.title,
                price=product.price,
                rating=product.rating,
                reviews_count=product.reviews_count,
                bought_last_month=product.bought_last_month,
            )

        self._log(f"   📄 Enriching: {product.title[:60]!r}")
        graph = SmartScraperGraph(
            prompt=(
                "This is an Amazon UAE product detail page. Extract: the title, "
                "brand, price as a number, average star rating, total number of "
                "reviews, the 'bought in past month' units if shown, the Best "
                "Sellers Rank (BSR) number and its category from the product "
                "information / details section, and the number of other sellers "
                "or offers for this product."
            ),
            source=url,
            config=self.graph_config,
            schema=ProductDetail,
        )
        try:
            detail = self._coerce_detail(graph.run())
        except Exception as exc:  # network / parsing hiccup -> degrade gracefully
            self._log(f"      ⚠️  enrich failed ({exc}); using search-page data only")
            detail = ProductDetail()

        # Fall back to the cheaper search-page signals where the detail page is blank.
        detail.title = detail.title or product.title
        detail.price = detail.price if detail.price is not None else product.price
        detail.rating = detail.rating if detail.rating is not None else product.rating
        if detail.reviews_count is None:
            detail.reviews_count = product.reviews_count
        if detail.bought_last_month is None:
            detail.bought_last_month = product.bought_last_month
        return detail

    @staticmethod
    def _coerce_detail(result) -> ProductDetail:
        if isinstance(result, ProductDetail):
            return result
        if isinstance(result, BaseModel):
            result = result.model_dump()
        if isinstance(result, dict):
            # Some models nest under 'content'.
            payload = result.get("content", result)
            if isinstance(payload, dict):
                try:
                    return ProductDetail(**payload)
                except Exception:
                    return ProductDetail()
        return ProductDetail()

    # -- step 3: source from China ----------------------------------------
    def source_from_china(self, title: str) -> Optional[ChinaSupplier]:
        self._log(f"   🏭 Sourcing from China: {title[:60]!r}")
        try:
            graph = SearchGraph(
                prompt=(
                    f"Find wholesale suppliers on Alibaba, 1688 or DHgate for this "
                    f"product: '{title}'. Return the platform, supplier name, listing "
                    "url, the lowest unit price in USD, and the minimum order quantity."
                ),
                config={**self.graph_config, "max_results": 3},
                schema=ChinaSourcing,
            )
            result = graph.run()
            supplier = self._best_supplier(result)
            if supplier:
                self._log(
                    f"      → {supplier.platform or 'supplier'} "
                    f"~${supplier.unit_price_usd} (MOQ {supplier.min_order_qty})"
                )
            return supplier
        except Exception as exc:
            self._log(f"      ⚠️  sourcing failed ({exc})")
            return None

    @staticmethod
    def _best_supplier(result) -> Optional[ChinaSupplier]:
        suppliers: List[ChinaSupplier] = []
        if isinstance(result, ChinaSourcing):
            suppliers = result.suppliers
        elif isinstance(result, BaseModel):
            result = result.model_dump()
        if isinstance(result, dict):
            raw = result.get("suppliers", [])
            for item in raw or []:
                if isinstance(item, dict):
                    try:
                        suppliers.append(ChinaSupplier(**item))
                    except Exception:
                        continue
        priced = [s for s in suppliers if s.unit_price_usd]
        if priced:
            return min(priced, key=lambda s: s.unit_price_usd)
        return suppliers[0] if suppliers else None

    # -- orchestration -----------------------------------------------------
    def find_products(
        self, keywords: List[str], max_products: int = 5
    ) -> List[ProductOpportunity]:
        opportunities: List[ProductOpportunity] = []

        for keyword in keywords:
            for product in self.search_keyword(keyword, max_products):
                detail = self.enrich_product(product)
                opp = ProductOpportunity(
                    keyword=keyword,
                    title=detail.title or product.title,
                    url=self._absolute_url(product.url),
                    asin=product.asin,
                    price=detail.price,
                    currency=product.currency or DEFAULT_CURRENCY,
                    rating=detail.rating,
                    reviews_count=detail.reviews_count,
                    bought_last_month=detail.bought_last_month,
                    bsr_rank=detail.bsr_rank,
                    bsr_category=detail.bsr_category,
                    seller_count=detail.seller_count,
                    brand=detail.brand,
                )

                if self.source_china:
                    supplier = self.source_from_china(opp.title)
                    if supplier:
                        opp.china_platform = supplier.platform
                        opp.china_supplier = supplier.supplier_name
                        opp.china_url = supplier.product_url
                        opp.china_unit_price_usd = supplier.unit_price_usd

                score(opp, has_china_data=self.source_china)
                opportunities.append(opp)

        opportunities.sort(key=lambda o: o.opportunity_score, reverse=True)
        return opportunities

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _absolute_url(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        if url.startswith("http"):
            return url
        if url.startswith("/"):
            return AMAZON_UAE_DOMAIN + url
        return f"{AMAZON_UAE_DOMAIN}/{url}"


# ----------------------------------------------------------------------------
# Output helpers
# ----------------------------------------------------------------------------


def print_shortlist(opportunities: List[ProductOpportunity], top: int) -> None:
    print("\n" + "=" * 78)
    print(" AMAZON UAE — PRODUCT OPPORTUNITY SHORTLIST")
    print("=" * 78)
    if not opportunities:
        print("No products found. Try different keywords or check your API key.")
        return

    for rank, opp in enumerate(opportunities[:top], start=1):
        print(f"\n#{rank}  [score {opp.opportunity_score}/100]  {opp.title[:70]}")
        print(f"     keyword: {opp.keyword}")
        price = f"{opp.price} {opp.currency}" if opp.price is not None else "n/a"
        print(
            f"     price: {price}   rating: {opp.rating or 'n/a'}   "
            f"reviews: {opp.reviews_count if opp.reviews_count is not None else 'n/a'}"
        )
        print(
            f"     monthly sales: {opp.bought_last_month or 'n/a'}   "
            f"BSR: {('#' + format(opp.bsr_rank, ',')) if opp.bsr_rank else 'n/a'}"
            f"{(' in ' + opp.bsr_category) if opp.bsr_category else ''}   "
            f"sellers/competitors: {opp.seller_count if opp.seller_count is not None else 'n/a'}"
        )
        print(
            f"     sub-scores -> demand {opp.demand_score} | "
            f"competition {opp.competition_score} | margin {opp.margin_score}"
        )
        if opp.china_unit_price_usd is not None:
            print(
                f"     china: ${opp.china_unit_price_usd} via {opp.china_platform or '?'}"
                f"   est. margin: {opp.estimated_margin_pct}%"
            )
        if opp.url:
            print(f"     link: {opp.url}")
        if opp.notes:
            print(f"     notes: {'; '.join(opp.notes)}")


def export_json(opportunities: List[ProductOpportunity], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump([asdict(o) for o in opportunities], handle, indent=2, ensure_ascii=False)
    print(f"\n💾 Wrote {len(opportunities)} rows to {path}")


def export_csv(opportunities: List[ProductOpportunity], path: str) -> None:
    if not opportunities:
        return
    fieldnames = [k for k in asdict(opportunities[0]).keys() if k != "notes"]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames + ["notes"])
        writer.writeheader()
        for opp in opportunities:
            row = asdict(opp)
            row["notes"] = "; ".join(row.get("notes") or [])
            writer.writerow(row)
    print(f"💾 Wrote {len(opportunities)} rows to {path}")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def build_graph_config(args: argparse.Namespace) -> dict:
    api_key = (
        args.api_key
        or os.getenv("OPENAI_APIKEY")
        or os.getenv("OPENAI_API_KEY")
    )
    if args.model.startswith("openai/") and not api_key:
        raise SystemExit(
            "No OpenAI API key found. Set OPENAI_APIKEY / OPENAI_API_KEY or pass "
            "--api-key. (Or use an Ollama model with --model ollama/llama3.2.)"
        )

    config: dict = {
        "llm": {"model": args.model},
        "verbose": args.verbose,
        "headless": True,
    }
    if api_key:
        config["llm"]["api_key"] = api_key
    if args.serper_api_key or os.getenv("SERPER_API_KEY"):
        config["serper_api_key"] = args.serper_api_key or os.getenv("SERPER_API_KEY")
        config["search_engine"] = "serper"
    return config


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape Amazon UAE and rank products worth selling.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--keywords", nargs="+", help="One or more seed keywords/niches to research."
    )
    group.add_argument(
        "--keywords-file",
        help="Path to a text file with one keyword per line.",
    )
    parser.add_argument(
        "--max-products",
        type=int,
        default=5,
        help="Max products to evaluate per keyword.",
    )
    parser.add_argument(
        "--source-china",
        action="store_true",
        help="Also search Alibaba/1688/DHgate to estimate sourcing cost & margin.",
    )
    parser.add_argument(
        "--model",
        default="openai/gpt-4o-mini",
        help="LLM to use, e.g. openai/gpt-4o-mini or ollama/llama3.2.",
    )
    parser.add_argument("--api-key", help="LLM API key (else read from env).")
    parser.add_argument("--serper-api-key", help="Serper.dev key for China search.")
    parser.add_argument(
        "--output",
        default="amazon_uae_shortlist",
        help="Output basename; writes <name>.csv and <name>.json.",
    )
    parser.add_argument(
        "--top", type=int, default=10, help="How many rows to print to the console."
    )
    parser.add_argument(
        "--quiet", dest="verbose", action="store_false", help="Reduce logging."
    )
    parser.set_defaults(verbose=True)
    return parser.parse_args(argv)


def load_keywords(args: argparse.Namespace) -> List[str]:
    if args.keywords:
        return args.keywords
    with open(args.keywords_file, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip() and not line.startswith("#")]


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    keywords = load_keywords(args)
    graph_config = build_graph_config(args)

    finder = AmazonUAEProductFinder(
        graph_config=graph_config,
        source_china=args.source_china,
        verbose=args.verbose,
    )
    opportunities = finder.find_products(keywords, max_products=args.max_products)

    print_shortlist(opportunities, top=args.top)
    export_csv(opportunities, f"{args.output}.csv")
    export_json(opportunities, f"{args.output}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
