#!/usr/bin/env python3
"""
Policy web scraper — searches UK government and parliamentary sources for
China-related content and classifies each result by framing category.

Default sources (can be overridden via CLI args):
  gov.uk orgs: FCDO, Cabinet Office, PM's Office, MoD
  Parliament: Hansard, ISC, Foreign Affairs / Defence / JCNSS committees
  Libraries: Commons Library, Lords Library
"""

import argparse
import csv
import logging
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

USER_AGENT = "policy-web-scraper/1.0 (+https://github.com)"
REQUEST_DELAY = 1.0  # seconds between requests, to be a polite scraper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ── Keyword definitions ────────────────────────────────────────────────────────

CHINA_TERMS: List[str] = [
    "china", "chinese", "prc", "people's republic of china",
    "ccp", "chinese communist party",
]

KEYWORD_CATEGORIES: Dict[str, List[str]] = {
    "securitisation": [
        "threat", "security threat", "national security", "danger",
        "hostile", "hostile state", "hostile actor", "aggression",
        "coercion", "coercive", "adversary", "enemy",
        "risk", "security risk", "security concern",
        "vulnerability", "critical vulnerability",
        "systemic challenge", "epoch-defining challenge",
        "strategic challenge", "systemic competitor",
        "strategic competitor", "overdependence", "strategic autonomy",
    ],
    "economic_security": [
        "economic security", "supply chains", "trade dependency",
        "critical infrastructure", "investment screening",
        "national security and investment act", "technology security",
        "sensitive sectors", "strategic sectors", "critical minerals",
        "economic coercion", "industrial strategy",
    ],
    "cyber_espionage": [
        "espionage", "spying", "cyber", "cyber security", "cyber threat",
        "cyber attack", "foreign interference", "state interference",
        "influence operations", "intellectual property theft", "ip theft",
        "surveillance", "data security", "information security",
        "misinformation", "disinformation",
    ],
    "military_defence": [
        "military", "defence", "armed forces", "indo-pacific",
        "deterrence", "deterrent", "strategic competition",
        "strategic environment", "security architecture",
        "regional stability", "maritime security", "freedom of navigation",
        "rules-based order", "armed conflict",
    ],
    "partnership": [
        "partnership", "cooperation", "collaboration",
        "constructive", "mutual benefit", "prosperity",
        "bilateral relations", "shared interests",
    ],
    "pragmatic_engagement": [
        "pragmatic", "robustly pragmatic", "constructive engagement",
        "responsible engagement", "balanced approach",
        "complex relationship", "multi-dimensional relationship",
        "cooperate where possible", "compete where necessary",
        "protect where needed",
    ],
}

SOURCE_URLS: List[str] = [
    "https://www.gov.uk/search/all",
    "https://www.gov.uk/government/organisations/foreign-commonwealth-development-office",
    "https://www.gov.uk/government/organisations/cabinet-office",
    "https://www.gov.uk/government/organisations/prime-ministers-office-10-downing-street",
    "https://www.gov.uk/government/organisations/ministry-of-defence",
    "https://hansard.parliament.uk",
    "https://isc.independent.gov.uk",
    "https://committees.parliament.uk/committee/78/foreign-affairs-committee/",
    "https://committees.parliament.uk/committee/24/defence-committee/",
    "https://committees.parliament.uk/committee/111/joint-committee-on-the-national-security-strategy/",
    "https://commonslibrary.parliament.uk",
    "https://lordslibrary.parliament.uk",
]

# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _get(url: str, params=None, timeout: int = 15) -> Optional[requests.Response]:
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, params=params, timeout=timeout
        )
        resp.raise_for_status()
        time.sleep(REQUEST_DELAY)
        return resp
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        if code == 403:
            logging.warning("Skipping %s — 403 Forbidden (access denied)", url)
        elif code == 404:
            logging.warning("Skipping %s — 404 Not Found", url)
        else:
            logging.warning("Skipping %s — HTTP %s", url, code)
        return None
    except requests.RequestException as exc:
        logging.warning("Skipping %s — %s", url, exc)
        return None


def fetch_page(url: str) -> Optional[BeautifulSoup]:
    resp = _get(url)
    return BeautifulSoup(resp.text, "html.parser") if resp else None


def fetch_json(url: str, params=None) -> Optional[dict]:
    resp = _get(url, params=params)
    if not resp:
        return None
    try:
        return resp.json()
    except ValueError as exc:
        logging.warning("JSON parse error from %s: %s", url, exc)
        return None

# ── Keyword matching ───────────────────────────────────────────────────────────

def _count_keyword(text_lower: str, keyword: str) -> int:
    kw = keyword.lower()
    if " " in kw:
        return text_lower.count(kw)
    return len(re.findall(r"\b" + re.escape(kw) + r"\b", text_lower))


def count_keywords(text: str, keywords: List[str]) -> int:
    t = text.lower()
    return sum(_count_keyword(t, kw) for kw in keywords)


def china_mention_count(text: str) -> int:
    return count_keywords(text, CHINA_TERMS)


def classify_text(text: str) -> Dict[str, int]:
    return {cat: count_keywords(text, kws) for cat, kws in KEYWORD_CATEGORIES.items()}


def first_excerpt(text: str, window: int = 300) -> str:
    """Return a snippet centred on the first China mention."""
    tl = text.lower()
    for term in CHINA_TERMS:
        idx = tl.find(term.lower())
        if idx != -1:
            s = max(0, idx - window // 2)
            e = min(len(text), idx + len(term) + window // 2)
            prefix = "..." if s > 0 else ""
            suffix = "..." if e < len(text) else ""
            return prefix + text[s:e].strip() + suffix
    return ""

# ── Text and metadata extraction ───────────────────────────────────────────────

def extract_full_text(soup: BeautifulSoup) -> str:
    for sel in [
        ".gem-c-govspeak",
        ".govuk-govspeak",
        ".publication-content",
        "#content article",
        ".article-content",
        ".entry-content",
        "article",
        "main",
    ]:
        el = soup.select_one(sel)
        if el:
            return el.get_text(separator=" ", strip=True)
    return soup.get_text(separator=" ", strip=True)


def parse_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return date_parser.parse(value, fuzzy=True).date().isoformat()
    except (ValueError, OverflowError):
        return None


def extract_metadata(soup: BeautifulSoup, url: str) -> Dict[str, Optional[str]]:
    title = None
    for sel in ["h1", ".govuk-heading-xl", ".gem-c-document-title__text", "title"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            title = el.get_text(strip=True)
            break

    date_str = None
    tt = soup.find("time", attrs={"datetime": True})
    if tt:
        date_str = tt.get("datetime") or tt.get_text()
    if not date_str:
        for name in ["article:published_time", "og:updated_time", "date", "publication_date"]:
            tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
            if tag and tag.get("content"):
                date_str = tag["content"].strip()
                break

    department = None
    org_link = soup.select_one("a[href*='/government/organisations/']")
    if org_link:
        department = org_link.get_text(strip=True)

    return {
        "url": url,
        "title": title,
        "publication_date": parse_date(date_str),
        "department": department,
        "source": urlparse(url).netloc,
    }

# ── Source-specific collectors ─────────────────────────────────────────────────

Stub = Dict  # partial metadata dict, may be None


def govuk_api_articles(org_slug: Optional[str] = None, count: int = 100) -> List[Stub]:
    """Use the gov.uk Search API to find China-related articles from an org (or all)."""
    params: List[Tuple] = [
        ("q", "china"),
        ("count", count),
        ("fields[]", "title"),
        ("fields[]", "link"),
        ("fields[]", "description"),
        ("fields[]", "public_timestamp"),
        ("fields[]", "organisations"),
    ]
    if org_slug:
        params.append(("filter_organisations[]", org_slug))

    data = fetch_json("https://www.gov.uk/api/search.json", params=params)
    if not data:
        return []

    stubs = []
    for item in data.get("results", []):
        link = item.get("link", "")
        if not link:
            continue
        if not link.startswith("http"):
            link = "https://www.gov.uk" + link
        orgs = item.get("organisations", [])
        if orgs and isinstance(orgs[0], dict):
            dept = ", ".join(o.get("title", o.get("slug", "")) for o in orgs)
        else:
            dept = ", ".join(str(o) for o in orgs)
        stubs.append({
            "url": link,
            "title": item.get("title"),
            "publication_date": parse_date(item.get("public_timestamp", "")),
            "department": dept,
            "source": "www.gov.uk",
        })
    return stubs


def hansard_article_links(query: str = "china", max_results: int = 50) -> List[str]:
    soup = fetch_page(f"https://hansard.parliament.uk/search/Debates?searchTerm={query}")
    if not soup:
        return []
    seen: set = set()
    links: List[str] = []
    for a in soup.select("a[href]"):
        href = a["href"]
        if not href.startswith("http"):
            href = "https://hansard.parliament.uk" + href
        if "hansard.parliament.uk" in href and href not in seen:
            seen.add(href)
            links.append(href)
            if len(links) >= max_results:
                break
    return links


def committee_article_links(committee_url: str, max_results: int = 50) -> List[str]:
    pub_url = committee_url.rstrip("/") + "/publications/"
    soup = fetch_page(pub_url)
    if not soup:
        soup = fetch_page(committee_url)
    if not soup:
        return []
    seen: set = set()
    links: List[str] = []
    for a in soup.select("a[href]"):
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin("https://committees.parliament.uk", href)
        if (
            "committees.parliament.uk" in href
            and href not in seen
            and href.rstrip("/") != committee_url.rstrip("/")
        ):
            seen.add(href)
            links.append(href)
            if len(links) >= max_results:
                break
    return links


def isc_report_links(max_results: int = 30) -> List[str]:
    soup = None
    for path in ["/our-work/isc-reports/", "/reports/", "/"]:
        soup = fetch_page("https://isc.independent.gov.uk" + path)
        if soup:
            break
    if not soup:
        return []
    seen: set = set()
    links: List[str] = []
    for a in soup.select("a[href]"):
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin("https://isc.independent.gov.uk", href)
        if "isc.independent.gov.uk" in href and href not in seen:
            seen.add(href)
            links.append(href)
            if len(links) >= max_results:
                break
    return links


def library_article_links(base_url: str, query: str = "china", max_results: int = 50) -> List[str]:
    domain = urlparse(base_url).netloc
    soup = fetch_page(f"{base_url.rstrip('/')}/?s={query}")
    if not soup:
        return []
    seen: set = set()
    links: List[str] = []
    for a in soup.select("a[href]"):
        href = a["href"]
        if (
            domain in href
            and href not in seen
            and href.rstrip("/") != base_url.rstrip("/")
        ):
            seen.add(href)
            links.append(href)
            if len(links) >= max_results:
                break
    return links

# ── Routing: source URL → list of article candidates ──────────────────────────

def collect_candidates(source_url: str) -> List[Dict]:
    """
    Given a source/listing URL, return a list of dicts:
        {"url": <article_url>, "stub": <partial_metadata_or_None>}
    """
    parsed = urlparse(source_url)
    domain = parsed.netloc
    path = parsed.path

    logging.info("Collecting candidates from: %s", source_url)

    if domain == "www.gov.uk":
        if "/government/organisations/" in path:
            org_slug = path.rstrip("/").rsplit("/", 1)[-1]
            stubs = govuk_api_articles(org_slug=org_slug)
        else:
            stubs = govuk_api_articles()
        return [{"url": s["url"], "stub": s} for s in stubs]

    if domain == "hansard.parliament.uk":
        return [{"url": u, "stub": None} for u in hansard_article_links()]

    if domain == "isc.independent.gov.uk":
        return [{"url": u, "stub": None} for u in isc_report_links()]

    if domain == "committees.parliament.uk":
        return [{"url": u, "stub": None} for u in committee_article_links(source_url)]

    if domain in ("commonslibrary.parliament.uk", "lordslibrary.parliament.uk"):
        base = f"{parsed.scheme}://{domain}"
        return [{"url": u, "stub": None} for u in library_article_links(base)]

    # Generic fallback: scrape all internal links from the page
    soup = fetch_page(source_url)
    if not soup:
        return []
    seen: set = set()
    links: List[str] = []
    for a in soup.select("a[href]"):
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin(source_url, href)
        if domain in href and href not in seen:
            seen.add(href)
            links.append(href)
    return [{"url": u, "stub": None} for u in links]

# ── Article scraping ───────────────────────────────────────────────────────────

def scrape_article(url: str, stub: Optional[Stub] = None) -> Optional[Dict]:
    """
    Fetch an article URL. Returns a result row dict if the page mentions China,
    otherwise returns None. Uses stub metadata where available to avoid redundant
    HTML parsing for title/date/department.
    """
    soup = fetch_page(url)
    if not soup:
        return None

    full_text = extract_full_text(soup)
    n_china = china_mention_count(full_text)
    if n_china == 0:
        return None

    # Merge stub (from API) with freshly scraped metadata; stub takes priority
    page_meta = extract_metadata(soup, url)
    meta: Dict = {**page_meta}
    if stub:
        for key, val in stub.items():
            if val is not None and key != "url":
                meta[key] = val
    meta["url"] = url

    cats = classify_text(full_text)
    return {
        **meta,
        "china_mentions": n_china,
        **{f"cat_{k}": v for k, v in cats.items()},
    }

# ── Entry point ────────────────────────────────────────────────────────────────

FIELDNAMES: List[str] = [
    "url", "title", "publication_date", "department",
    "china_mentions",
    "cat_securitisation", "cat_economic_security", "cat_cyber_espionage",
    "cat_military_defence", "cat_partnership", "cat_pragmatic_engagement",
]


def run(source_urls: List[str], output_csv: str) -> None:
    rows: List[Dict] = []
    seen_urls: set = set()

    for source_url in source_urls:
        candidates = collect_candidates(source_url)
        logging.info("  %d candidates from %s", len(candidates), source_url)

        for cand in candidates:
            art_url = cand["url"]
            if art_url in seen_urls:
                continue
            seen_urls.add(art_url)

            row = scrape_article(art_url, stub=cand.get("stub"))
            if row:
                rows.append(row)
                logging.info("  [+] %s", row.get("title") or art_url)

    if not rows:
        logging.warning("No China-mentioning content found across all sources.")
        return

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    logging.info("Saved %d records to %s", len(rows), output_csv)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Scrape UK policy sources for China mentions and classify by framing category. "
            "Outputs a CSV with one row per matching article."
        )
    )
    ap.add_argument(
        "urls",
        nargs="*",
        default=SOURCE_URLS,
        help="Source/listing URLs to collect from (default: built-in UK policy sources).",
    )
    ap.add_argument(
        "--output",
        default="china_policy_scrape.csv",
        help="Output CSV file path (default: china_policy_scrape.csv).",
    )
    args = ap.parse_args()
    run(args.urls, args.output)


if __name__ == "__main__":
    main()
