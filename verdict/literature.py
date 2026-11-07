"""Academic database search & campus-network full-text retrieval (stdlib only).

Runs directly from the user's machine so requests inherit university Wi-Fi / campus VPN IP
entitlements, HTTP(S)_PROXY settings, optional publisher API keys, and library EZproxy prefixes.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

USER_AGENT = "verdict-cli/0.2 (scholarly-evidence-checker)"

PUBLISHER_DOI_PREFIXES: dict[str, tuple[str, ...]] = {
    "sciencedirect": ("10.1016",),
    "ieee": ("10.1109",),
    "acm": ("10.1145",),
    "springer": ("10.1007", "10.1038"),
    "wiley": ("10.1002", "10.1111"),
}

DB_ALIASES: dict[str, str] = {
    "elsevier": "sciencedirect",
    "sd": "sciencedirect",
    "s2": "semanticscholar",
    "scholar": "openalex",
}

SUPPORTED_DBS = (
    "sciencedirect",
    "ieee",
    "acm",
    "springer",
    "arxiv",
    "semanticscholar",
    "openalex",
    "crossref",
)


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    final_url: str


HttpFetcher = Callable[[str, dict[str, str], float], HttpResponse]


def default_http_fetch(url: str, headers: dict[str, str] | None = None, timeout: float = 15.0) -> HttpResponse:
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            info = {k.lower(): v for k, v in resp.headers.items()}
            return HttpResponse(
                status=int(resp.status),
                headers=info,
                body=resp.read(),
                final_url=resp.geturl(),
            )
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        info = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}
        return HttpResponse(status=int(exc.code), headers=info, body=body, final_url=url)


@dataclass
class LitConfig:
    elsevier_api_key: str = ""
    elsevier_inst_token: str = ""
    ieee_api_key: str = ""
    springer_api_key: str = ""
    s2_api_key: str = ""
    polite_email: str = ""
    ezproxy_prefix: str = ""
    private_terms: list[str] = field(default_factory=list)
    cache_dir: Path = field(default_factory=lambda: Path("cache/papers"))

    def ezproxy_url(self, target_url: str) -> str:
        if not self.ezproxy_prefix or not target_url:
            return ""
        if "{url}" in self.ezproxy_prefix:
            return self.ezproxy_prefix.replace("{url}", urllib.parse.quote(target_url, safe=""))
        return f"{self.ezproxy_prefix}{target_url}"


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip().lower()] = v.strip().strip("'\"")
    except OSError:
        return {}
    return out


def load_lit_config(task_lit: dict[str, Any] | None = None, base_dir: Path | None = None) -> LitConfig:
    creds: dict[str, str] = {}
    for env_candidate in (
        Path("~/.config/verdict/config.env").expanduser(),
        ((base_dir or Path.cwd()) / ".verdict.env").resolve(),
    ):
        creds.update(_parse_env_file(env_candidate))

    cred_path = Path(os.environ.get("VERDICT_CREDENTIALS_FILE", "~/.config/verdict/credentials.json")).expanduser()
    if cred_path.exists():
        try:
            raw_json = json.loads(cred_path.read_text(encoding="utf-8"))
            if isinstance(raw_json, dict):
                creds.update({str(k).lower(): str(v) for k, v in raw_json.items()})
        except (OSError, json.JSONDecodeError):
            pass

    tlit = task_lit or {}
    cdir = Path(tlit.get("cache_dir", "cache/papers"))
    if base_dir and not cdir.is_absolute():
        cdir = (base_dir / cdir).resolve()

    return LitConfig(
        elsevier_api_key=os.environ.get("ELSEVIER_API_KEY", creds.get("elsevier_api_key", "")).strip(),
        elsevier_inst_token=os.environ.get("ELSEVIER_INST_TOKEN", creds.get("elsevier_inst_token", "")).strip(),
        ieee_api_key=os.environ.get("IEEE_API_KEY", creds.get("ieee_api_key", "")).strip(),
        springer_api_key=os.environ.get("SPRINGER_API_KEY", creds.get("springer_api_key", "")).strip(),
        s2_api_key=os.environ.get("S2_API_KEY", creds.get("s2_api_key", "")).strip(),
        polite_email=os.environ.get(
            "UNPAYWALL_EMAIL",
            os.environ.get("VERDICT_EMAIL", creds.get("unpaywall_email", creds.get("polite_email", ""))),
        ).strip(),
        ezproxy_prefix=os.environ.get(
            "VERDICT_EZPROXY_PREFIX",
            os.environ.get("EZPROXY_PREFIX", tlit.get("ezproxy_prefix", creds.get("ezproxy_prefix", ""))),
        ).strip(),
        private_terms=[str(t).strip() for t in tlit.get("private_terms", []) if str(t).strip()],
        cache_dir=cdir,
    )


def validate_query_privacy(query: str, private_terms: list[str] | None = None) -> str | None:
    """Return an error reason if a search query appears to leak local paths, emails, secrets, or private terms."""
    if re.search(r"(^|\s)(/Users/|/home/|[A-Za-z]:\\|private/)", query):
        return "Query appears to contain a local or private filesystem path."
    if re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", query):
        return "Query appears to contain an email address."
    if re.search(r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b", query):
        return "Query appears to contain an API secret or token."
    for term in private_terms or []:
        if term and re.search(rf"\b{re.escape(term)}\b", query, flags=re.I):
            return f"Query contains configured private term '{term}'."
    return None


@dataclass
class WorkResult:
    title: str
    authors: list[str]
    year: int | None
    venue: str
    publisher: str
    doi: str
    url: str
    pdf_url: str = ""
    ezproxy_url: str = ""
    abstract: str = ""
    citations: int | None = None
    is_open_access: bool = False
    source_db: str = ""

    def to_dict(self, checked_date: str | None = None) -> dict[str, Any]:
        cdate = checked_date or str(dt.date.today())
        return {
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "venue": self.venue,
            "publisher": self.publisher,
            "doi": self.doi,
            "url": self.url,
            "pdf_url": self.pdf_url,
            "ezproxy_url": self.ezproxy_url,
            "is_open_access": self.is_open_access,
            "citations": self.citations,
            "source_db": self.source_db,
            "abstract": self.abstract,
            "verdict_source_snippet": {
                "label": f"{self.authors[0] + ' et al., ' if self.authors else ''}{self.title} ({self.venue or self.publisher}, {self.year or 'n.d.'})",
                "date": cdate,
                "url": f"https://doi.org/{self.doi}" if self.doi else self.url,
            },
        }


def _clean_doi(raw: str | None) -> str:
    if not raw:
        return ""
    s = raw.strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    return s.strip()


def _reconstruct_openalex_abstract(inv: dict[str, list[int]] | None) -> str:
    if not inv or not isinstance(inv, dict):
        return ""
    pos_map: dict[int, str] = {}
    for word, positions in inv.items():
        for p in positions:
            pos_map[int(p)] = word
    if not pos_map:
        return ""
    return " ".join(pos_map[idx] for idx in sorted(pos_map)).strip()


def _direct_publisher_pdf_guess(doi: str) -> str:
    low = doi.lower()
    if low.startswith(("10.1007/", "10.1038/")):
        return f"https://link.springer.com/content/pdf/{doi}.pdf"
    if low.startswith("10.1145/"):
        return f"https://dl.acm.org/doi/pdf/{doi}"
    if m := re.match(r"^10\.48550/arxiv\.(.+)$", low):
        return f"https://arxiv.org/pdf/{m.group(1)}.pdf"
    return ""


def _search_sciencedirect_api(
    query: str,
    cfg: LitConfig,
    since_year: int | None,
    limit: int,
    fetcher: HttpFetcher,
) -> tuple[list[WorkResult], str | None]:
    if not cfg.elsevier_api_key:
        return [], "ELSEVIER_API_KEY not set; using OpenAlex/Crossref Elsevier index (set ELSEVIER_API_KEY for full-text campus ScienceDirect search)."

    params: dict[str, str] = {"query": query, "count": str(min(limit, 25))}
    if since_year:
        params["date"] = f"{since_year}-{dt.date.today().year}"
    url = "https://api.elsevier.com/content/search/sciencedirect?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json", "X-ELS-APIKey": cfg.elsevier_api_key}
    if cfg.elsevier_inst_token:
        headers["X-ELS-Insttoken"] = cfg.elsevier_inst_token

    resp = fetcher(url, headers, 15.0)
    if resp.status != 200:
        return [], f"ScienceDirect API HTTP {resp.status}; falling back to Crossref/OpenAlex Elsevier prefix."

    data = json.loads(resp.body.decode("utf-8", errors="replace"))
    entries = data.get("search-results", {}).get("entry", []) or []
    results: list[WorkResult] = []
    for item in entries:
        if item.get("error"):
            continue
        doi = _clean_doi(item.get("prism:doi"))
        title = str(item.get("dc:title", "")).strip()
        if not title:
            continue
        cover = str(item.get("prism:coverDate", ""))
        year = int(cover[:4]) if len(cover) >= 4 and cover[:4].isdigit() else None
        authors_raw = item.get("authors", {})
        authors: list[str] = []
        if isinstance(authors_raw, dict) and isinstance(authors_raw.get("author"), list):
            authors = [a.get("$", "") or a.get("given-name", "") for a in authors_raw["author"] if isinstance(a, dict)]
        elif item.get("dc:creator"):
            authors = [str(item["dc:creator"])]
        sd_url = ""
        for lnk in item.get("link", []) or []:
            if isinstance(lnk, dict) and lnk.get("@ref") == "scidir":
                sd_url = lnk.get("@href", "")
        landing = sd_url or (f"https://doi.org/{doi}" if doi else "")
        oa = str(item.get("openaccess", "0")) in ("1", "true", "True")
        results.append(
            WorkResult(
                title=title,
                authors=[a for a in authors if a],
                year=year,
                venue=str(item.get("prism:publicationName", "")),
                publisher="Elsevier (ScienceDirect)",
                doi=doi,
                url=landing,
                ezproxy_url=cfg.ezproxy_url(landing),
                is_open_access=oa,
                source_db="sciencedirect-api",
            )
        )
    return results, None


def _search_ieee_api(
    query: str,
    cfg: LitConfig,
    since_year: int | None,
    limit: int,
    fetcher: HttpFetcher,
) -> tuple[list[WorkResult], str | None]:
    if not cfg.ieee_api_key:
        return [], None
    params: dict[str, str] = {
        "apikey": cfg.ieee_api_key,
        "querytext": query,
        "max_records": str(min(limit, 25)),
    }
    if since_year:
        params["start_year"] = str(since_year)
    url = "https://ieeexploreapi.ieee.org/api/v1/search/articles?" + urllib.parse.urlencode(params)
    resp = fetcher(url, {"Accept": "application/json"}, 15.0)
    if resp.status != 200:
        return [], f"IEEE API HTTP {resp.status}; using Crossref/OpenAlex IEEE index."
    data = json.loads(resp.body.decode("utf-8", errors="replace"))
    out: list[WorkResult] = []
    for art in data.get("articles", []) or []:
        doi = _clean_doi(art.get("doi"))
        landing = art.get("html_url") or (f"https://doi.org/{doi}" if doi else "")
        authors = [
            a.get("full_name", "")
            for a in (art.get("authors", {}).get("authors", []) or [])
            if a.get("full_name")
        ]
        out.append(
            WorkResult(
                title=str(art.get("title", "")).strip(),
                authors=authors,
                year=int(art["publication_year"]) if str(art.get("publication_year", "")).isdigit() else None,
                venue=str(art.get("publication_title", "")),
                publisher="IEEE",
                doi=doi,
                url=landing,
                pdf_url=str(art.get("pdf_url", "")),
                ezproxy_url=cfg.ezproxy_url(landing),
                abstract=str(art.get("abstract", "")).strip(),
                citations=art.get("citing_paper_count"),
                is_open_access=str(art.get("access_type", "")).upper() == "OPEN ACCESS",
                source_db="ieee-api",
            )
        )
    return out, None


def _search_springer_api(
    query: str,
    cfg: LitConfig,
    since_year: int | None,
    limit: int,
    fetcher: HttpFetcher,
) -> tuple[list[WorkResult], str | None]:
    if not cfg.springer_api_key:
        return [], None
    q = f"{query} year:{since_year}" if since_year else query
    params = {"q": q, "p": str(min(limit, 25)), "api_key": cfg.springer_api_key}
    url = "https://api.springernature.com/meta/v2/json?" + urllib.parse.urlencode(params)
    resp = fetcher(url, {"Accept": "application/json"}, 15.0)
    if resp.status != 200:
        return [], f"Springer API HTTP {resp.status}; using Crossref/OpenAlex Springer index."
    data = json.loads(resp.body.decode("utf-8", errors="replace"))
    out: list[WorkResult] = []
    for rec in data.get("records", []) or []:
        doi = _clean_doi(rec.get("doi"))
        pub_date = str(rec.get("publicationDate", ""))
        year = int(pub_date[:4]) if len(pub_date) >= 4 and pub_date[:4].isdigit() else None
        landing = f"https://doi.org/{doi}" if doi else ""
        out.append(
            WorkResult(
                title=str(rec.get("title", "")).strip(),
                authors=[c.get("creator", "") for c in (rec.get("creators", []) or []) if c.get("creator")],
                year=year,
                venue=str(rec.get("publicationName", "")),
                publisher="Springer Nature",
                doi=doi,
                url=landing,
                pdf_url=_direct_publisher_pdf_guess(doi),
                ezproxy_url=cfg.ezproxy_url(landing),
                abstract=str(rec.get("abstract", "")).strip(),
                is_open_access=str(rec.get("openaccess", "false")).lower() == "true",
                source_db="springer-api",
            )
        )
    return out, None


def _search_crossref(
    query: str,
    cfg: LitConfig,
    prefixes: tuple[str, ...] | None,
    since_year: int | None,
    limit: int,
    fetcher: HttpFetcher,
    db_tag: str = "crossref",
) -> list[WorkResult]:
    filters: list[str] = []
    if prefixes:
        filters.extend(f"prefix:{p}" for p in prefixes)
    if since_year:
        filters.append(f"from-pub-date:{since_year}-01-01")

    params: dict[str, str] = {"query": query, "rows": str(min(limit, 25))}
    if filters:
        params["filter"] = ",".join(filters)
    if cfg.polite_email:
        params["mailto"] = cfg.polite_email

    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    resp = fetcher(url, {"Accept": "application/json"}, 15.0)
    if resp.status != 200:
        return []
    data = json.loads(resp.body.decode("utf-8", errors="replace"))
    items = data.get("message", {}).get("items", []) or []
    out: list[WorkResult] = []
    for it in items:
        titles = it.get("title") or []
        title = titles[0].strip() if titles else ""
        if not title:
            continue
        doi = _clean_doi(it.get("DOI"))
        authors = []
        for a in it.get("author", []) or []:
            nm = f"{a.get('given', '')} {a.get('family', '')}".strip()
            if nm:
                authors.append(nm)
        year = None
        for dkey in ("published-print", "published-online", "issued"):
            parts = (it.get(dkey, {}).get("date-parts") or [[]])[0]
            if parts and isinstance(parts[0], int):
                year = parts[0]
                break
        containers = it.get("container-title") or []
        venue = containers[0] if containers else ""
        pdf_link = ""
        for lnk in it.get("link", []) or []:
            if "pdf" in str(lnk.get("content-type", "")).lower():
                pdf_link = lnk.get("URL", "")
                break
        if not pdf_link and doi:
            pdf_link = _direct_publisher_pdf_guess(doi)
        landing = it.get("URL") or (f"https://doi.org/{doi}" if doi else "")
        abstract_raw = re.sub(r"<[^>]+>", " ", str(it.get("abstract", ""))).strip()
        out.append(
            WorkResult(
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                publisher=str(it.get("publisher", "")),
                doi=doi,
                url=landing,
                pdf_url=pdf_link,
                ezproxy_url=cfg.ezproxy_url(landing),
                abstract=re.sub(r"\s+", " ", abstract_raw),
                citations=it.get("is-referenced-by-count"),
                source_db=db_tag,
            )
        )
    return out


def _search_openalex(
    query: str,
    cfg: LitConfig,
    publisher_filter_prefixes: tuple[str, ...] | None,
    since_year: int | None,
    limit: int,
    fetcher: HttpFetcher,
    db_tag: str = "openalex",
) -> list[WorkResult]:
    filters: list[str] = []
    if since_year:
        filters.append(f"from_publication_date:{since_year}-01-01")
    params: dict[str, str] = {
        "search": query,
        "per-page": str(min(max(limit * 2, 15), 50) if publisher_filter_prefixes else min(limit, 25)),
    }
    if filters:
        params["filter"] = ",".join(filters)
    if cfg.polite_email:
        params["mailto"] = cfg.polite_email

    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    resp = fetcher(url, {"Accept": "application/json"}, 15.0)
    if resp.status != 200:
        return []
    data = json.loads(resp.body.decode("utf-8", errors="replace"))
    out: list[WorkResult] = []
    for w in data.get("results", []) or []:
        title = str(w.get("title") or "").strip()
        if not title:
            continue
        doi = _clean_doi(w.get("doi"))
        if publisher_filter_prefixes:
            if not doi or not any(doi.lower().startswith(p.lower() + "/") for p in publisher_filter_prefixes):
                continue
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        venue = str(src.get("display_name") or "")
        publisher = str(src.get("host_organization_name") or "")
        oa = w.get("open_access") or {}
        best_oa = w.get("best_oa_location") or {}
        pdf_url = str(best_oa.get("pdf_url") or oa.get("oa_url") or "")
        if not pdf_url and doi:
            pdf_url = _direct_publisher_pdf_guess(doi)
        landing = str(loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else w.get("id", "")))
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in (w.get("authorships") or [])
            if a.get("author", {}).get("display_name")
        ]
        out.append(
            WorkResult(
                title=title,
                authors=authors,
                year=w.get("publication_year"),
                venue=venue,
                publisher=publisher,
                doi=doi,
                url=landing,
                pdf_url=pdf_url,
                ezproxy_url=cfg.ezproxy_url(landing),
                abstract=_reconstruct_openalex_abstract(w.get("abstract_inverted_index")),
                citations=w.get("cited_by_count"),
                is_open_access=bool(oa.get("is_oa")),
                source_db=db_tag,
            )
        )
        if len(out) >= limit:
            break
    return out


def _search_arxiv(
    query: str,
    since_year: int | None,
    limit: int,
    fetcher: HttpFetcher,
) -> list[WorkResult]:
    params = {
        "search_query": f"all:{query}",
        "start": "0",
        "max_results": str(min(limit, 25)),
    }
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    resp = fetcher(url, {"Accept": "application/atom+xml"}, 15.0)
    if resp.status != 200:
        return []
    try:
        root = ET.fromstring(resp.body.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    out: list[WorkResult] = []
    for entry in root.findall("atom:entry", ns):
        title = re.sub(r"\s+", " ", (entry.findtext("atom:title", default="", namespaces=ns) or "").strip())
        if not title:
            continue
        published = entry.findtext("atom:published", default="", namespaces=ns)
        year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None
        if since_year and year and year < since_year:
            continue
        abs_url = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
        arxiv_id = abs_url.rsplit("/abs/", 1)[-1] if "/abs/" in abs_url else ""
        doi = _clean_doi(entry.findtext("arxiv:doi", default="", namespaces=ns))
        if not doi and arxiv_id:
            doi = f"10.48550/arXiv.{arxiv_id.split('v')[0]}"
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else ""
        authors = [
            (a.findtext("atom:name", default="", namespaces=ns) or "").strip()
            for a in entry.findall("atom:author", ns)
        ]
        summary = re.sub(r"\s+", " ", (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip())
        out.append(
            WorkResult(
                title=title,
                authors=[a for a in authors if a],
                year=year,
                venue="arXiv",
                publisher="arXiv",
                doi=doi,
                url=abs_url,
                pdf_url=pdf_url,
                abstract=summary,
                is_open_access=True,
                source_db="arxiv",
            )
        )
    return out


def _search_semanticscholar(
    query: str,
    cfg: LitConfig,
    since_year: int | None,
    limit: int,
    fetcher: HttpFetcher,
) -> list[WorkResult]:
    params: dict[str, str] = {
        "query": query,
        "limit": str(min(limit, 25)),
        "fields": "title,authors,year,venue,externalIds,url,openAccessPdf,abstract,citationCount,isOpenAccess",
    }
    if since_year:
        params["year"] = f"{since_year}-"
    headers = {"Accept": "application/json"}
    if cfg.s2_api_key:
        headers["x-api-key"] = cfg.s2_api_key
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
    resp = fetcher(url, headers, 15.0)
    if resp.status != 200:
        return []
    data = json.loads(resp.body.decode("utf-8", errors="replace"))
    out: list[WorkResult] = []
    for p in data.get("data", []) or []:
        title = str(p.get("title") or "").strip()
        if not title:
            continue
        ext = p.get("externalIds") or {}
        doi = _clean_doi(ext.get("DOI"))
        oa_pdf = (p.get("openAccessPdf") or {}).get("url") or ""
        landing = f"https://doi.org/{doi}" if doi else str(p.get("url") or "")
        out.append(
            WorkResult(
                title=title,
                authors=[a.get("name", "") for a in (p.get("authors") or []) if a.get("name")],
                year=p.get("year"),
                venue=str(p.get("venue") or ""),
                publisher="",
                doi=doi,
                url=landing,
                pdf_url=oa_pdf or (_direct_publisher_pdf_guess(doi) if doi else ""),
                ezproxy_url=cfg.ezproxy_url(landing),
                abstract=str(p.get("abstract") or "").strip(),
                citations=p.get("citationCount"),
                is_open_access=bool(p.get("isOpenAccess") or oa_pdf),
                source_db="semanticscholar",
            )
        )
    return out


def _dedupe_works(items: list[WorkResult], limit: int) -> list[WorkResult]:
    by_key: dict[str, WorkResult] = {}
    for w in items:
        norm_title = re.sub(r"[^a-z0-9]+", "", w.title.lower())
        key = f"doi:{w.doi.lower()}" if w.doi else f"title:{norm_title}"
        existing = by_key.get(key)
        if not existing:
            by_key[key] = w
            continue
        # Merge richer fields into existing record
        if not existing.abstract and w.abstract:
            existing.abstract = w.abstract
        if not existing.pdf_url and w.pdf_url:
            existing.pdf_url = w.pdf_url
        if existing.citations is None and w.citations is not None:
            existing.citations = w.citations
        if not existing.venue and w.venue:
            existing.venue = w.venue
        if w.is_open_access:
            existing.is_open_access = True
        if w.source_db and w.source_db not in existing.source_db:
            existing.source_db = f"{existing.source_db}+{w.source_db}"
    return list(by_key.values())[:limit]


def parse_db_list(db_arg: str | None, default_dbs: list[str] | None = None) -> list[str]:
    if not db_arg or db_arg.strip().lower() == "all":
        return list(default_dbs) if default_dbs else ["openalex", "crossref", "arxiv", "semanticscholar"]
    chosen: list[str] = []
    for raw in db_arg.split(","):
        tok = raw.strip().lower()
        if not tok:
            continue
        tok = DB_ALIASES.get(tok, tok)
        if tok == "all":
            return ["sciencedirect", "ieee", "acm", "springer", "arxiv", "semanticscholar", "openalex"]
        if tok not in SUPPORTED_DBS:
            raise ValueError(f"Unknown database '{raw}'. Supported: {', '.join(SUPPORTED_DBS)}, all")
        if tok not in chosen:
            chosen.append(tok)
    return chosen


def search_literature(
    query: str,
    cfg: LitConfig,
    dbs: list[str],
    since_year: int | None = None,
    limit: int = 10,
    oa_only: bool = False,
    allow_raw_query: bool = False,
    fetcher: HttpFetcher = default_http_fetch,
) -> tuple[list[WorkResult], list[str]]:
    if not allow_raw_query:
        violation = validate_query_privacy(query, private_terms=cfg.private_terms)
        if violation:
            raise ValueError(f"{violation} Pass --allow-raw-query only if intentional.")

    collected: list[WorkResult] = []
    notes: list[str] = []

    for db in dbs:
        if db == "sciencedirect":
            sd_items, note = _search_sciencedirect_api(query, cfg, since_year, limit, fetcher)
            if note:
                notes.append(note)
            collected.extend(sd_items)
            prefixes = PUBLISHER_DOI_PREFIXES["sciencedirect"]
            collected.extend(_search_crossref(query, cfg, prefixes, since_year, limit, fetcher, "sciencedirect-crossref"))
            collected.extend(_search_openalex(query, cfg, prefixes, since_year, limit, fetcher, "sciencedirect-openalex"))
        elif db == "ieee":
            ieee_items, note = _search_ieee_api(query, cfg, since_year, limit, fetcher)
            if note:
                notes.append(note)
            collected.extend(ieee_items)
            prefixes = PUBLISHER_DOI_PREFIXES["ieee"]
            collected.extend(_search_crossref(query, cfg, prefixes, since_year, limit, fetcher, "ieee-crossref"))
            collected.extend(_search_openalex(query, cfg, prefixes, since_year, limit, fetcher, "ieee-openalex"))
        elif db == "acm":
            prefixes = PUBLISHER_DOI_PREFIXES["acm"]
            collected.extend(_search_crossref(query, cfg, prefixes, since_year, limit, fetcher, "acm-crossref"))
            collected.extend(_search_openalex(query, cfg, prefixes, since_year, limit, fetcher, "acm-openalex"))
        elif db == "springer":
            sp_items, note = _search_springer_api(query, cfg, since_year, limit, fetcher)
            if note:
                notes.append(note)
            collected.extend(sp_items)
            prefixes = PUBLISHER_DOI_PREFIXES["springer"]
            collected.extend(_search_crossref(query, cfg, prefixes, since_year, limit, fetcher, "springer-crossref"))
            collected.extend(_search_openalex(query, cfg, prefixes, since_year, limit, fetcher, "springer-openalex"))
        elif db == "arxiv":
            collected.extend(_search_arxiv(query, since_year, limit, fetcher))
        elif db == "semanticscholar":
            collected.extend(_search_semanticscholar(query, cfg, since_year, limit, fetcher))
        elif db == "openalex":
            collected.extend(_search_openalex(query, cfg, None, since_year, limit, fetcher, "openalex"))
        elif db == "crossref":
            collected.extend(_search_crossref(query, cfg, None, since_year, limit, fetcher, "crossref"))

    merged = _dedupe_works(collected, limit=limit * 2 if oa_only else limit)
    if oa_only:
        merged = [w for w in merged if w.is_open_access or bool(w.pdf_url)][:limit]
    return merged, notes


def _extract_pdf_text(pdf_path: Path, txt_path: Path) -> bool:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return False
    res = subprocess.run(
        [pdftotext, "-layout", str(pdf_path), str(txt_path)],
        capture_output=True,
        check=False,
    )
    return res.returncode == 0 and txt_path.exists() and txt_path.stat().st_size > 0


def _slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")[:100] or "paper"


def fetch_work(
    identifier: str,
    cfg: LitConfig,
    out_dir: Path | None = None,
    extract_text: bool = True,
    fetcher: HttpFetcher = default_http_fetch,
) -> dict[str, Any]:
    """Fetch full text or PDF for a DOI, arXiv ID, or URL using campus IP entitlements + OA fallbacks."""
    dest_dir = (out_dir or cfg.cache_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    raw = identifier.strip()
    arxiv_match = re.match(r"^(?:arxiv:)(\d{4}\.\d{4,5}(?:v\d+)?)$", raw, flags=re.I)
    if arxiv_match:
        aid = arxiv_match.group(1)
        doi = f"10.48550/arXiv.{aid.split('v')[0]}"
        direct_url = f"https://arxiv.org/pdf/{aid}.pdf"
    elif raw.startswith(("http://", "https://")) and "doi.org/" not in raw.lower():
        doi = ""
        direct_url = raw
    else:
        doi = _clean_doi(raw)
        direct_url = ""

    slug = _slugify(doi or raw)
    attempts: list[str] = []

    # 1. ScienceDirect / Elsevier Article Retrieval API (full text when on subscribed university IP!)
    if doi.lower().startswith("10.1016/") and cfg.elsevier_api_key:
        sd_api = f"https://api.elsevier.com/content/article/doi/{urllib.parse.quote(doi, safe='')}"
        hdrs = {"Accept": "application/json", "X-ELS-APIKey": cfg.elsevier_api_key}
        if cfg.elsevier_inst_token:
            hdrs["X-ELS-Insttoken"] = cfg.elsevier_inst_token
        resp = fetcher(sd_api, hdrs, 20.0)
        if resp.status == 200:
            payload = json.loads(resp.body.decode("utf-8", errors="replace"))
            ftr = payload.get("full-text-retrieval-response") or {}
            orig_text = str(ftr.get("originalText") or "").strip()
            core = ftr.get("coredata") or {}
            abstract = str(core.get("dc:description") or "").strip()
            if orig_text and len(orig_text) > len(abstract) + 200:
                txt_file = dest_dir / f"{slug}.txt"
                txt_file.write_text(orig_text, encoding="utf-8")
                return {
                    "status": "full_text",
                    "mode": "sciencedirect_campus_api",
                    "doi": doi,
                    "saved_text": str(txt_file),
                    "saved_pdf": None,
                    "message": "Retrieved full text via ScienceDirect API using institutional/campus IP entitlement.",
                }
            attempts.append(
                "ScienceDirect API returned abstract-only (current IP is not entitled to full text for this title; checking OA/preprint repositories)."
            )
            if abstract:
                (dest_dir / f"{slug}.abstract.txt").write_text(abstract, encoding="utf-8")
        else:
            attempts.append(f"ScienceDirect API returned HTTP {resp.status}.")

    # 2. Gather candidate PDF URLs (direct URL, campus publisher PDF, OpenAlex OA, Unpaywall OA)
    pdf_candidates: list[tuple[str, str]] = []
    if direct_url:
        pdf_candidates.append(("direct_url", direct_url))
    if doi:
        guess = _direct_publisher_pdf_guess(doi)
        if guess:
            pdf_candidates.append(("publisher_campus_pdf", guess))

        # Check OpenAlex for OA/repository PDF locations (e.g., author-accepted manuscript on arXiv/HAL/Zenodo)
        oa_api = f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi, safe='/')}"
        oa_resp = fetcher(oa_api, {"Accept": "application/json"}, 12.0)
        if oa_resp.status == 200:
            w = json.loads(oa_resp.body.decode("utf-8", errors="replace"))
            best = (w.get("best_oa_location") or {}).get("pdf_url")
            if best:
                pdf_candidates.append(("openalex_oa", str(best)))
            for loc in w.get("locations") or []:
                pu = loc.get("pdf_url")
                if pu and all(pu != u for _, u in pdf_candidates):
                    pdf_candidates.append(("openalex_repo", str(pu)))

        # Optional Unpaywall lookup
        if cfg.polite_email:
            upw_api = (
                f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='/')}?"
                + urllib.parse.urlencode({"email": cfg.polite_email})
            )
            upw_resp = fetcher(upw_api, {"Accept": "application/json"}, 12.0)
            if upw_resp.status == 200:
                uobj = json.loads(upw_resp.body.decode("utf-8", errors="replace"))
                best_upw = (uobj.get("best_oa_location") or {}).get("url_for_pdf")
                if best_upw and all(best_upw != u for _, u in pdf_candidates):
                    pdf_candidates.append(("unpaywall_oa", str(best_upw)))

    # 3. Attempt PDF download with strict `%PDF-` magic-byte verification (rejects HTML paywalls!)
    for source_label, purl in pdf_candidates:
        resp = fetcher(purl, {"Accept": "application/pdf,*/*;q=0.8"}, 25.0)
        if resp.status == 200 and resp.body.startswith(b"%PDF-"):
            pdf_file = dest_dir / f"{slug}.pdf"
            pdf_file.write_bytes(resp.body)
            txt_file = dest_dir / f"{slug}.txt"
            extracted = _extract_pdf_text(pdf_file, txt_file) if extract_text else False
            return {
                "status": "full_text",
                "mode": source_label,
                "doi": doi,
                "source_url": purl,
                "saved_pdf": str(pdf_file),
                "saved_text": str(txt_file) if extracted else None,
                "message": f"Downloaded verified PDF via {source_label}"
                + (" and extracted plain text." if extracted else "."),
            }
        ctype = resp.headers.get("content-type", "unknown")
        attempts.append(f"{source_label} ({purl}) -> HTTP {resp.status} ({ctype}; not a raw PDF, likely paywall/login).")

    doi_url = f"https://doi.org/{doi}" if doi else direct_url
    return {
        "status": "paywalled_or_unavailable",
        "mode": "none",
        "doi": doi,
        "url": doi_url,
        "ezproxy_url": cfg.ezproxy_url(doi_url),
        "saved_pdf": None,
        "saved_text": str(dest_dir / f"{slug}.abstract.txt") if (dest_dir / f"{slug}.abstract.txt").exists() else None,
        "attempts": attempts,
        "message": (
            "Full text could not be retrieved programmatically. Connect to your university VPN (full-tunnel so "
            "publisher domains route via campus IP), set ELSEVIER_API_KEY for ScienceDirect full-text API, or open "
            "the DOI / EZproxy URL in a browser."
        ),
    }


def run_netcheck(
    cfg: LitConfig,
    sample_sd_doi: str = "10.1016/j.future.2023.01.001",
    fetcher: HttpFetcher = default_http_fetch,
) -> dict[str, Any]:
    """Check scholarly API connectivity, configured keys, proxy settings, and campus entitlement."""
    endpoints = [
        ("OpenAlex", "https://api.openalex.org/works?per-page=1"),
        ("Crossref", "https://api.crossref.org/works?rows=1"),
        ("arXiv", "https://export.arxiv.org/api/query?search_query=all:test&max_results=1"),
    ]
    probes: list[dict[str, Any]] = []
    for name, url in endpoints:
        try:
            r = fetcher(url, {"Accept": "application/json"}, 8.0)
            probes.append({"service": name, "reachable": r.status == 200, "http_status": r.status})
        except Exception as exc:
            probes.append({"service": name, "reachable": False, "error": str(exc)})

    sd_entitlement: dict[str, Any] = {
        "api_key_configured": bool(cfg.elsevier_api_key),
        "inst_token_configured": bool(cfg.elsevier_inst_token),
        "campus_fulltext_entitled": None,
        "note": "",
    }
    if cfg.elsevier_api_key:
        ent_url = f"https://api.elsevier.com/content/article/entitlement/doi/{sample_sd_doi}"
        hdrs = {"Accept": "application/json", "X-ELS-APIKey": cfg.elsevier_api_key}
        if cfg.elsevier_inst_token:
            hdrs["X-ELS-Insttoken"] = cfg.elsevier_inst_token
        try:
            r = fetcher(ent_url, hdrs, 10.0)
            if r.status == 200:
                body_txt = r.body.decode("utf-8", errors="replace")
                entitled = '"entitled":true' in body_txt.lower().replace(" ", "") or ">true<" in body_txt.lower()
                sd_entitlement["campus_fulltext_entitled"] = entitled
                sd_entitlement["note"] = (
                    "Elsevier recognizes your current IP/token as entitled for ScienceDirect full text."
                    if entitled
                    else "API key works, but current IP is not entitled for sample closed DOI (check university VPN split-tunnel)."
                )
            else:
                sd_entitlement["note"] = f"Entitlement probe returned HTTP {r.status}."
        except Exception as exc:
            sd_entitlement["note"] = f"Entitlement probe failed: {exc}"
    else:
        sd_entitlement["note"] = (
            "ScienceDirect discovery works keyless via Crossref/OpenAlex (10.1016 prefix); "
            "set ELSEVIER_API_KEY (free at dev.elsevier.com) to unlock campus-IP full-text retrieval."
        )

    return {
        "proxies_detected": {
            "https_proxy": bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")),
            "ezproxy_prefix": cfg.ezproxy_prefix or None,
        },
        "credentials_configured": {
            "ELSEVIER_API_KEY": bool(cfg.elsevier_api_key),
            "ELSEVIER_INST_TOKEN": bool(cfg.elsevier_inst_token),
            "IEEE_API_KEY": bool(cfg.ieee_api_key),
            "SPRINGER_API_KEY": bool(cfg.springer_api_key),
            "S2_API_KEY": bool(cfg.s2_api_key),
            "UNPAYWALL_EMAIL": bool(cfg.polite_email),
        },
        "probes": probes,
        "sciencedirect_campus_status": sd_entitlement,
    }
