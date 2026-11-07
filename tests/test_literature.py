"""Offline mocked unit tests for ScienceDirect & multi-DB academic search, campus full-text fetch, and netcheck."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verdict.literature import (
    HttpResponse,
    LitConfig,
    fetch_work,
    load_lit_config,
    parse_db_list,
    run_netcheck,
    search_literature,
    validate_query_privacy,
)


class LiteratureSearchAndCampusTests(unittest.TestCase):
    def test_privacy_guard_blocks_paths_emails_secrets_and_private_terms(self) -> None:
        self.assertIsNotNone(validate_query_privacy("check /Users/alice/secret/notes.md"))
        self.assertIsNotNone(validate_query_privacy("private/prospects.md power"))
        self.assertIsNotNone(validate_query_privacy("contact founder@company.io benchmark"))
        self.assertIsNotNone(validate_query_privacy("key sk-1234567890abcdefghijklmnop"))
        self.assertIsNotNone(validate_query_privacy("benchmark AcmePartner cluster", private_terms=["AcmePartner"]))
        self.assertIsNone(validate_query_privacy("server power management switchback experiment", private_terms=["AcmePartner"]))

    def test_load_lit_config_reads_dot_verdict_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".verdict.env"
            env_file.write_text(
                "ELSEVIER_API_KEY=env-els-key\nEZPROXY_PREFIX='https://ezproxy.uni.edu/login?url='\n",
                encoding="utf-8",
            )
            cfg = load_lit_config({"private_terms": ["SecretCo"]}, base_dir=Path(tmp))
            self.assertEqual(cfg.elsevier_api_key, "env-els-key")
            self.assertEqual(cfg.ezproxy_prefix, "https://ezproxy.uni.edu/login?url=")
            self.assertEqual(cfg.private_terms, ["SecretCo"])

    def test_sciencedirect_keyless_fallback_merges_crossref_and_openalex(self) -> None:
        def fake_fetch(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
            if "api.crossref.org/works" in url and "prefix%3A10.1016" in url:
                payload = {
                    "message": {
                        "items": [
                            {
                                "DOI": "10.1016/j.future.2025.02.010",
                                "title": ["Energy-aware cluster scheduling on bare metal"],
                                "author": [{"given": "Ada", "family": "Lovelace"}],
                                "published-print": {"date-parts": [[2025, 4]]},
                                "container-title": ["Future Generation Computer Systems"],
                                "publisher": "Elsevier BV",
                                "is-referenced-by-count": 14,
                            }
                        ]
                    }
                }
                return HttpResponse(200, {"content-type": "application/json"}, json.dumps(payload).encode(), url)
            if "api.openalex.org/works" in url:
                payload = {
                    "results": [
                        {
                            "title": "Energy-aware cluster scheduling on bare metal",
                            "doi": "https://doi.org/10.1016/j.future.2025.02.010",
                            "publication_year": 2025,
                            "cited_by_count": 16,
                            "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/pdf/2501.00001.pdf"},
                            "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/2501.00001.pdf"},
                            "primary_location": {
                                "landing_page_url": "https://www.sciencedirect.com/science/article/pii/S0167739X2500010X",
                                "source": {
                                    "display_name": "Future Generation Computer Systems",
                                    "host_organization_name": "Elsevier",
                                },
                            },
                            "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
                            "abstract_inverted_index": {"Measured": [0], "wall": [1], "power": [2], "on": [3], "clusters.": [4]},
                        }
                    ]
                }
                return HttpResponse(200, {"content-type": "application/json"}, json.dumps(payload).encode(), url)
            return HttpResponse(404, {}, b"", url)

        cfg = LitConfig(ezproxy_prefix="https://ezproxy.uni.edu/login?url=")
        dbs = parse_db_list("sciencedirect")
        works, notes = search_literature("cluster power", cfg, dbs=dbs, since_year=2024, fetcher=fake_fetch)
        self.assertEqual(len(works), 1)
        self.assertTrue(any("ELSEVIER_API_KEY not set" in n for n in notes))
        w = works[0]
        self.assertEqual(w.doi, "10.1016/j.future.2025.02.010")
        self.assertEqual(w.abstract, "Measured wall power on clusters.")
        self.assertEqual(w.pdf_url, "https://arxiv.org/pdf/2501.00001.pdf")
        self.assertTrue(w.ezproxy_url.startswith("https://ezproxy.uni.edu/login?url="))

    def test_sciencedirect_direct_api_and_campus_fulltext_fetch(self) -> None:
        seen_headers: dict[str, str] = {}

        def fake_fetch(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
            seen_headers.update(headers)
            if "api.elsevier.com/content/article/doi/" in url:
                payload = {
                    "full-text-retrieval-response": {
                        "coredata": {"dc:description": "Short abstract."},
                        "originalText": "FULL ARTICLE TEXT " * 40,
                    }
                }
                return HttpResponse(200, {"content-type": "application/json"}, json.dumps(payload).encode(), url)
            return HttpResponse(404, {}, b"", url)

        with tempfile.TemporaryDirectory() as tmp:
            cfg = LitConfig(
                elsevier_api_key="test-els-key",
                elsevier_inst_token="test-inst-tok",
                cache_dir=Path(tmp),
            )
            res = fetch_work("10.1016/j.apenergy.2025.120001", cfg, fetcher=fake_fetch)
            self.assertEqual(res["status"], "full_text")
            self.assertEqual(res["mode"], "sciencedirect_campus_api")
            self.assertEqual(seen_headers.get("X-ELS-APIKey"), "test-els-key")
            self.assertEqual(seen_headers.get("X-ELS-Insttoken"), "test-inst-tok")
            saved = Path(res["saved_text"]).read_text(encoding="utf-8")
            self.assertIn("FULL ARTICLE TEXT", saved)

    def test_fetch_rejects_html_paywall_and_falls_back_to_verified_oa_pdf(self) -> None:
        def fake_fetch(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
            if "link.springer.com/content/pdf/" in url:
                # Simulate off-campus HTML login redirect pretending to be 200 OK
                return HttpResponse(200, {"content-type": "text/html"}, b"<html>Sign in via institution</html>", url)
            if "api.openalex.org/works/" in url:
                payload = {"best_oa_location": {"pdf_url": "https://arxiv.org/pdf/2502.12345.pdf"}, "locations": []}
                return HttpResponse(200, {"content-type": "application/json"}, json.dumps(payload).encode(), url)
            if "arxiv.org/pdf/2502.12345.pdf" in url:
                return HttpResponse(200, {"content-type": "application/pdf"}, b"%PDF-1.5 fake valid pdf bytes", url)
            return HttpResponse(404, {}, b"", url)

        with tempfile.TemporaryDirectory() as tmp:
            cfg = LitConfig(cache_dir=Path(tmp))
            res = fetch_work("10.1007/s00450-025-0001-x", cfg, extract_text=False, fetcher=fake_fetch)
            self.assertEqual(res["status"], "full_text")
            self.assertEqual(res["mode"], "openalex_oa")
            self.assertTrue(Path(res["saved_pdf"]).read_bytes().startswith(b"%PDF-"))

    def test_netcheck_detects_campus_entitlement(self) -> None:
        def fake_fetch(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
            if "entitlement/doi" in url:
                return HttpResponse(
                    200,
                    {"content-type": "application/json"},
                    b'{"entitlement-response":{"document-entitlement":{"entitled":true}}}',
                    url,
                )
            return HttpResponse(200, {"content-type": "application/json"}, b"{}", url)

        cfg = LitConfig(elsevier_api_key="test-key")
        rep = run_netcheck(cfg, fetcher=fake_fetch)
        self.assertTrue(rep["sciencedirect_campus_status"]["campus_fulltext_entitled"])
        self.assertTrue(all(p["reachable"] for p in rep["probes"]))


if __name__ == "__main__":
    unittest.main()

