"""Tests for scraper.py — unit tests (no network) + one live integration test."""

import csv
import io
import unittest
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

from urllib.parse import urlparse

from scraper import (
    CHINA_TERMS,
    KEYWORD_CATEGORIES,
    SOURCE_URLS,
    _count_keyword,
    china_mention_count,
    classify_text,
    collect_candidates,
    extract_full_text,
    extract_metadata,
    first_excerpt,
    govuk_api_articles,
    parse_date,
    scrape_article,
)


# ── Keyword matching ───────────────────────────────────────────────────────────

class TestCountKeyword(unittest.TestCase):
    def test_single_word_exact(self):
        self.assertEqual(_count_keyword("china is a country", "china"), 1)

    def test_single_word_case_insensitive(self):
        # _count_keyword expects pre-lowercased text (called via count_keywords)
        self.assertEqual(_count_keyword("china china china", "china"), 3)

    def test_word_boundary_no_false_positive(self):
        # "china" should not match inside "machinery"
        self.assertEqual(_count_keyword("machinery", "china"), 0)

    def test_multi_word_phrase(self):
        self.assertEqual(_count_keyword("economic security is key", "economic security"), 1)

    def test_multi_word_no_match(self):
        self.assertEqual(_count_keyword("economic resilience", "economic security"), 0)

    def test_apostrophe_boundary(self):
        # "China's" — \b sits before the apostrophe, so "china" matches
        self.assertEqual(_count_keyword("china's policy", "china"), 1)


class TestChinaMentionCount(unittest.TestCase):
    def test_direct_china(self):
        self.assertEqual(china_mention_count("UK and China relations"), 1)

    def test_prc(self):
        self.assertEqual(china_mention_count("the PRC government"), 1)

    def test_peoples_republic(self):
        # Matches both the full phrase AND "china" standalone — count is 2
        self.assertEqual(china_mention_count("People's Republic of China"), 2)

    def test_ccp(self):
        self.assertEqual(china_mention_count("the CCP leadership"), 1)

    def test_multiple_terms(self):
        # Both "China" and "Chinese" count
        count = china_mention_count("China's Chinese diplomats visited.")
        self.assertEqual(count, 2)

    def test_no_match(self):
        self.assertEqual(china_mention_count("UK-US trade relations"), 0)

    def test_case_insensitive(self):
        self.assertEqual(china_mention_count("CHINESE officials"), 1)


class TestClassifyText(unittest.TestCase):
    def test_securitisation_detected(self):
        text = "China poses a national security threat as an adversary."
        cats = classify_text(text)
        self.assertGreater(cats["securitisation"], 0)

    def test_economic_security_detected(self):
        text = "Supply chains and critical infrastructure are at risk."
        cats = classify_text(text)
        self.assertGreater(cats["economic_security"], 0)

    def test_cyber_espionage_detected(self):
        text = "State-sponsored espionage and cyber attacks are increasing."
        cats = classify_text(text)
        self.assertGreater(cats["cyber_espionage"], 0)

    def test_military_defence_detected(self):
        text = "The rules-based order depends on deterrence in the Indo-Pacific."
        cats = classify_text(text)
        self.assertGreater(cats["military_defence"], 0)

    def test_partnership_detected(self):
        text = "Bilateral relations and cooperation remain important."
        cats = classify_text(text)
        self.assertGreater(cats["partnership"], 0)

    def test_pragmatic_engagement_detected(self):
        text = "A balanced approach and responsible engagement are needed."
        cats = classify_text(text)
        self.assertGreater(cats["pragmatic_engagement"], 0)

    def test_zero_when_absent(self):
        text = "The weather in London was pleasant."
        cats = classify_text(text)
        for v in cats.values():
            self.assertEqual(v, 0)

    def test_all_categories_present(self):
        cats = classify_text("test")
        self.assertEqual(set(cats.keys()), set(KEYWORD_CATEGORIES.keys()))

    def test_multiple_categories_simultaneously(self):
        text = (
            "China poses a national security threat. Supply chains are vulnerable. "
            "Cyber attacks have increased. Defence cooperation is key."
        )
        cats = classify_text(text)
        positive = [k for k, v in cats.items() if v > 0]
        self.assertGreaterEqual(len(positive), 3)


class TestFirstExcerpt(unittest.TestCase):
    def test_returns_surrounding_text(self):
        text = "A" * 200 + " China policy " + "B" * 200
        excerpt = first_excerpt(text, window=100)
        self.assertIn("China", excerpt)

    def test_ellipsis_added_when_truncated(self):
        text = "X" * 200 + " China " + "Y" * 200
        excerpt = first_excerpt(text, window=50)
        self.assertTrue(excerpt.startswith("..."))
        self.assertTrue(excerpt.endswith("..."))

    def test_no_ellipsis_at_start(self):
        text = "China is central to UK policy on many issues."
        excerpt = first_excerpt(text, window=300)
        self.assertFalse(excerpt.startswith("..."))

    def test_empty_when_no_china_term(self):
        self.assertEqual(first_excerpt("No relevant content here."), "")

    def test_prc_triggers_excerpt(self):
        text = "The PRC has expanded its military reach."
        excerpt = first_excerpt(text)
        self.assertIn("PRC", excerpt)


# ── Date parsing ───────────────────────────────────────────────────────────────

class TestParseDate(unittest.TestCase):
    def test_iso_format(self):
        self.assertEqual(parse_date("2024-03-15"), "2024-03-15")

    def test_human_readable(self):
        self.assertEqual(parse_date("15 March 2024"), "2024-03-15")

    def test_datetime_string(self):
        self.assertEqual(parse_date("2024-03-15T10:30:00Z"), "2024-03-15")

    def test_none_input(self):
        self.assertIsNone(parse_date(None))

    def test_invalid_string(self):
        self.assertIsNone(parse_date("not a date"))

    def test_fuzzy_parse(self):
        result = parse_date("Published: 10 January 2023")
        self.assertEqual(result, "2023-01-10")


# ── HTML extraction ────────────────────────────────────────────────────────────

class TestExtractFullText(unittest.TestCase):
    def _soup(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")

    def test_prefers_govspeak(self):
        html = """
        <main><p>nav text</p></main>
        <div class="gem-c-govspeak"><p>article body</p></div>
        """
        text = extract_full_text(self._soup(html))
        self.assertIn("article body", text)

    def test_falls_back_to_article(self):
        html = "<article><p>article content</p></article>"
        text = extract_full_text(self._soup(html))
        self.assertIn("article content", text)

    def test_falls_back_to_main(self):
        html = "<main><p>main content</p></main>"
        text = extract_full_text(self._soup(html))
        self.assertIn("main content", text)

    def test_full_page_fallback(self):
        html = "<html><body><p>only content</p></body></html>"
        text = extract_full_text(self._soup(html))
        self.assertIn("only content", text)


class TestExtractMetadata(unittest.TestCase):
    def _soup(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")

    def test_extracts_h1_title(self):
        html = "<h1>UK-China Relations</h1>"
        meta = extract_metadata(self._soup(html), "https://www.gov.uk/test")
        self.assertEqual(meta["title"], "UK-China Relations")

    def test_extracts_time_datetime(self):
        html = '<time datetime="2024-05-20">20 May 2024</time>'
        meta = extract_metadata(self._soup(html), "https://www.gov.uk/test")
        self.assertEqual(meta["publication_date"], "2024-05-20")

    def test_extracts_department_from_org_link(self):
        html = '<a href="/government/organisations/cabinet-office">Cabinet Office</a>'
        meta = extract_metadata(self._soup(html), "https://www.gov.uk/test")
        self.assertEqual(meta["department"], "Cabinet Office")

    def test_source_is_domain(self):
        meta = extract_metadata(self._soup("<html/>"), "https://hansard.parliament.uk/debates/123")
        self.assertEqual(meta["source"], "hansard.parliament.uk")

    def test_url_preserved(self):
        url = "https://www.gov.uk/government/publications/test"
        meta = extract_metadata(self._soup("<html/>"), url)
        self.assertEqual(meta["url"], url)

    def test_meta_tag_date_fallback(self):
        html = '<meta name="article:published_time" content="2023-11-01T00:00:00Z"/>'
        meta = extract_metadata(self._soup(html), "https://www.gov.uk/test")
        self.assertEqual(meta["publication_date"], "2023-11-01")


# ── scrape_article (mocked network) ───────────────────────────────────────────

SAMPLE_GOV_HTML = """
<html>
<head><title>UK-China Trade Policy</title></head>
<body>
  <h1>UK-China Trade Policy</h1>
  <time datetime="2024-06-01">1 June 2024</time>
  <a href="/government/organisations/cabinet-office">Cabinet Office</a>
  <div class="gem-c-govspeak">
    <p>The UK's relationship with China and the PRC is complex. China represents
    both an economic opportunity and a national security concern. Supply chains
    involving Chinese components require careful investment screening. The UK
    maintains a pragmatic and balanced approach to engagement with China, while
    protecting against cyber threats and espionage.</p>
  </div>
</body>
</html>
"""

SAMPLE_NO_CHINA_HTML = """
<html>
<body>
  <h1>UK-US Trade Policy</h1>
  <time datetime="2024-06-01">1 June 2024</time>
  <div class="gem-c-govspeak">
    <p>The transatlantic relationship remains a cornerstone of UK foreign policy.</p>
  </div>
</body>
</html>
"""


class TestScrapeArticle(unittest.TestCase):
    @patch("scraper.fetch_page")
    def test_returns_row_when_china_mentioned(self, mock_fetch):
        mock_fetch.return_value = BeautifulSoup(SAMPLE_GOV_HTML, "html.parser")
        row = scrape_article("https://www.gov.uk/test")
        self.assertIsNotNone(row)
        self.assertGreater(row["china_mentions"], 0)

    @patch("scraper.fetch_page")
    def test_returns_none_when_no_china(self, mock_fetch):
        mock_fetch.return_value = BeautifulSoup(SAMPLE_NO_CHINA_HTML, "html.parser")
        result = scrape_article("https://www.gov.uk/test")
        self.assertIsNone(result)

    @patch("scraper.fetch_page")
    def test_extracts_title(self, mock_fetch):
        mock_fetch.return_value = BeautifulSoup(SAMPLE_GOV_HTML, "html.parser")
        row = scrape_article("https://www.gov.uk/test")
        self.assertEqual(row["title"], "UK-China Trade Policy")

    @patch("scraper.fetch_page")
    def test_extracts_date(self, mock_fetch):
        mock_fetch.return_value = BeautifulSoup(SAMPLE_GOV_HTML, "html.parser")
        row = scrape_article("https://www.gov.uk/test")
        self.assertEqual(row["publication_date"], "2024-06-01")

    @patch("scraper.fetch_page")
    def test_extracts_department(self, mock_fetch):
        mock_fetch.return_value = BeautifulSoup(SAMPLE_GOV_HTML, "html.parser")
        row = scrape_article("https://www.gov.uk/test")
        self.assertEqual(row["department"], "Cabinet Office")

    @patch("scraper.fetch_page")
    def test_category_flags_populated(self, mock_fetch):
        mock_fetch.return_value = BeautifulSoup(SAMPLE_GOV_HTML, "html.parser")
        row = scrape_article("https://www.gov.uk/test")
        # Article mentions national security, supply chains, cyber — these cats should fire
        self.assertGreater(row["cat_securitisation"], 0)
        self.assertGreater(row["cat_economic_security"], 0)
        self.assertGreater(row["cat_cyber_espionage"], 0)

    @patch("scraper.fetch_page")
    def test_excerpt_contains_china_term(self, mock_fetch):
        mock_fetch.return_value = BeautifulSoup(SAMPLE_GOV_HTML, "html.parser")
        row = scrape_article("https://www.gov.uk/test")
        excerpt = row["excerpt"].lower()
        self.assertTrue(any(term in excerpt for term in CHINA_TERMS))

    @patch("scraper.fetch_page")
    def test_stub_metadata_takes_priority(self, mock_fetch):
        mock_fetch.return_value = BeautifulSoup(SAMPLE_GOV_HTML, "html.parser")
        stub = {
            "title": "Stub Title Override",
            "publication_date": "2024-01-01",
            "department": "FCDO",
            "source": "www.gov.uk",
        }
        row = scrape_article("https://www.gov.uk/test", stub=stub)
        self.assertEqual(row["title"], "Stub Title Override")
        self.assertEqual(row["department"], "FCDO")

    @patch("scraper.fetch_page")
    def test_returns_none_when_fetch_fails(self, mock_fetch):
        mock_fetch.return_value = None
        result = scrape_article("https://www.gov.uk/test")
        self.assertIsNone(result)


# ── govuk_api_articles (mocked) ───────────────────────────────────────────────

class TestGovukApiArticles(unittest.TestCase):
    @patch("scraper.fetch_json")
    def test_parses_results(self, mock_json):
        mock_json.return_value = {
            "results": [
                {
                    "title": "China Relations",
                    "link": "/government/publications/china-relations",
                    "public_timestamp": "2024-03-01T00:00:00Z",
                    "organisations": ["Foreign, Commonwealth & Development Office"],
                    "description": "Overview of UK-China relations.",
                }
            ],
            "total": 1,
        }
        stubs = govuk_api_articles(org_slug="foreign-commonwealth-development-office")
        self.assertEqual(len(stubs), 1)
        self.assertEqual(stubs[0]["title"], "China Relations")
        self.assertTrue(stubs[0]["url"].startswith("https://www.gov.uk"))
        self.assertEqual(stubs[0]["publication_date"], "2024-03-01")

    @patch("scraper.fetch_json")
    def test_returns_empty_on_api_failure(self, mock_json):
        mock_json.return_value = None
        stubs = govuk_api_articles(org_slug="cabinet-office")
        self.assertEqual(stubs, [])

    @patch("scraper.fetch_json")
    def test_skips_results_with_no_link(self, mock_json):
        mock_json.return_value = {
            "results": [{"title": "No link result", "link": ""}]
        }
        stubs = govuk_api_articles()
        self.assertEqual(stubs, [])


# ── Source URL list sanity check ───────────────────────────────────────────────

class TestSourceUrls(unittest.TestCase):
    def test_all_expected_domains_present(self):
        domains = {urlparse(u).netloc for u in SOURCE_URLS}
        expected = {
            "www.gov.uk",
            "hansard.parliament.uk",
            "isc.independent.gov.uk",
            "committees.parliament.uk",
            "commonslibrary.parliament.uk",
            "lordslibrary.parliament.uk",
        }
        self.assertTrue(expected.issubset(domains))

    def test_all_urls_are_https(self):
        for url in SOURCE_URLS:
            self.assertTrue(url.startswith("https://"), f"Not HTTPS: {url}")


# ── Live integration test (requires network, skipped in CI) ───────────────────

import os

@unittest.skipIf(os.getenv("CI") == "true", "Skipping live network test in CI")
class TestLiveGovukApi(unittest.TestCase):
    """Hits the real gov.uk Search API — skipped when CI=true."""

    def test_api_returns_china_results(self):
        from scraper import govuk_api_articles
        stubs = govuk_api_articles(
            org_slug="foreign-commonwealth-development-office", count=5
        )
        # API is live — we should get at least one result
        self.assertIsInstance(stubs, list)
        if stubs:
            self.assertIn("url", stubs[0])
            self.assertTrue(stubs[0]["url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
