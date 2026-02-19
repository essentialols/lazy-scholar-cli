#!/usr/bin/env python3
"""
Lazy Scholar CLI

Aggregate citation data, metadata, open access links, editorial notices,
community feedback, and AI summaries for academic papers from multiple
public APIs. No authentication required.

Data sources: CrossRef, PubMed, OpenAlex, Semantic Scholar, PubPeer,
Europe PMC, Hypothesis, and ClinicalTrials.gov.

Usage:
    python scholar.py 10.1038/s41586-020-2649-2
    python scholar.py https://doi.org/10.1038/s41586-020-2649-2
    python scholar.py paper.pdf
    python scholar.py --title "Attention is All You Need" --author Vaswani
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DOI_REGEX = re.compile(r"(10\.\d{4,9}/[^\s\"'<>\]\)}{,]+)", re.IGNORECASE)

# Browser User-Agent — blend in with normal traffic
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Rate limiting — conservative, one invocation = many API calls across sources
RATE_LIMIT_FILE = Path(__file__).parent / ".rate_limit_log"
MIN_INTERVAL_SECONDS = 5
MAX_REQUESTS_PER_HOUR = 10
API_CALL_DELAY_MIN = 0.5
API_CALL_DELAY_MAX = 2.0


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def check_rate_limit():
    """Enforce rate limits."""
    now = time.time()
    window = 3600

    timestamps = []
    if RATE_LIMIT_FILE.exists():
        try:
            timestamps = json.loads(RATE_LIMIT_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            timestamps = []

    timestamps = [ts for ts in timestamps if now - ts < window]

    if timestamps:
        elapsed = now - max(timestamps)
        if elapsed < MIN_INTERVAL_SECONDS:
            wait = MIN_INTERVAL_SECONDS - elapsed
            print(f"Rate limit: waiting {wait:.1f}s between requests...")
            time.sleep(wait)

    if len(timestamps) >= MAX_REQUESTS_PER_HOUR:
        oldest = min(timestamps)
        retry_after = window - (now - oldest)
        print(
            f"Rate limit reached ({MAX_REQUESTS_PER_HOUR} lookups/hour). "
            f"Try again in {retry_after / 60:.0f} minute(s).",
            file=sys.stderr,
        )
        sys.exit(1)


def record_request():
    """Record one invocation timestamp."""
    now = time.time()
    window = 3600

    timestamps = []
    if RATE_LIMIT_FILE.exists():
        try:
            timestamps = json.loads(RATE_LIMIT_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            timestamps = []

    timestamps = [ts for ts in timestamps if now - ts < window]
    timestamps.append(now)
    RATE_LIMIT_FILE.write_text(json.dumps(timestamps))


def _polite_delay():
    """Random delay between API calls."""
    time.sleep(random.uniform(API_CALL_DELAY_MIN, API_CALL_DELAY_MAX))


# ---------------------------------------------------------------------------
# Proxy rotation
# ---------------------------------------------------------------------------

_PROXY_FORCE: bool | None = None  # None = use config, True = force on, False = force off


def _load_proxy_config() -> dict:
    """Load proxy configuration from ~/.scholar-proxies.json."""
    config_path = Path.home() / ".scholar-proxies.json"
    try:
        return json.loads(config_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"enabled": False, "proxies": []}


def _get_proxy() -> dict | None:
    """Get a random proxy for the requests library, or None if disabled."""
    config = _load_proxy_config()
    enabled = _PROXY_FORCE if _PROXY_FORCE is not None else config.get("enabled", False)
    if not enabled:
        return None
    proxies = config.get("proxies", [])
    if not proxies:
        return None
    url = random.choice(proxies)
    return {"http": url, "https": url}


def _get(url, params=None, headers=None, timeout=30, retries=2):
    """GET with default headers and 429 retry."""
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers=h, timeout=timeout, proxies=_get_proxy())
            if resp.ok:
                return resp.json()
            if resp.status_code == 429 and attempt < retries:
                time.sleep(random.uniform(3, 8))
                continue
            return None
        except Exception:
            if attempt < retries:
                time.sleep(2)
                continue
    return None


def _post(url, json_data=None, params=None, headers=None, timeout=30, retries=2):
    """POST with default headers and 429 retry."""
    h = {"User-Agent": USER_AGENT, "Accept": "application/json",
         "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, json=json_data, params=params, headers=h, timeout=timeout, proxies=_get_proxy())
            if resp.ok:
                return resp.json()
            if resp.status_code == 429 and attempt < retries:
                time.sleep(random.uniform(3, 8))
                continue
            return None
        except Exception:
            if attempt < retries:
                time.sleep(2)
                continue
    return None


# ---------------------------------------------------------------------------
# DOI extraction
# ---------------------------------------------------------------------------

def extract_doi(text: str) -> str | None:
    """Extract a DOI from arbitrary text."""
    match = DOI_REGEX.search(text)
    if match:
        doi = match.group(1).rstrip(".,;:")
        for ext in [".pdf", ".html", ".full", ".xml"]:
            if doi.lower().endswith(ext):
                doi = doi[: -len(ext)]
        return doi
    return None


def extract_doi_from_url(url: str) -> str | None:
    url = unquote(url)
    if "doi.org/" in url:
        return extract_doi(url.split("doi.org/", 1)[1])
    return extract_doi(url)


def extract_doi_from_pdf(pdf_path: str) -> str | None:
    from pdfminer.high_level import extract_text
    from pdfminer.pdfparser import PDFParser
    from pdfminer.pdfdocument import PDFDocument

    pdf_file = Path(pdf_path).resolve()
    if not pdf_file.exists():
        print(f"File not found: {pdf_file}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(pdf_file, "rb") as f:
            parser = PDFParser(f)
            doc = PDFDocument(parser)
            for meta in (doc.info or []):
                for key in ("doi", "Subject", "Title", "WPS-ARTICLEDOI"):
                    val = meta.get(key, b"")
                    if isinstance(val, bytes):
                        val = val.decode("utf-8", errors="ignore")
                    doi = extract_doi(str(val))
                    if doi:
                        return doi
    except Exception:
        pass

    try:
        text = extract_text(str(pdf_file), maxpages=3)
        return extract_doi(text)
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# API: CrossRef
# ---------------------------------------------------------------------------

def fetch_crossref(doi: str) -> dict | None:
    """Fetch paper metadata from CrossRef."""
    data = _get(f"https://api.crossref.org/works/{quote(doi, safe='')}")
    if data and "message" in data:
        return data["message"]
    return None


def search_crossref(title: str, rows: int = 3) -> list:
    """Search CrossRef by title."""
    data = _get(
        "https://api.crossref.org/works",
        params={
            "query.title": title,
            "rows": rows,
            "select": "DOI,title,author,issued,container-title,is-referenced-by-count,publisher,type",
        },
    )
    if data and "message" in data:
        return data["message"].get("items", [])
    return []


# ---------------------------------------------------------------------------
# API: OpenAlex
# ---------------------------------------------------------------------------

def fetch_openalex(doi: str) -> dict | None:
    """Fetch paper data from OpenAlex."""
    return _get(f"https://api.openalex.org/works/https://doi.org/{quote(doi, safe='')}")


# ---------------------------------------------------------------------------
# API: Semantic Scholar
# ---------------------------------------------------------------------------

def fetch_semantic_scholar(doi: str) -> dict | None:
    """Fetch paper data from Semantic Scholar."""
    fields = "paperId,title,citationCount,influentialCitationCount,isOpenAccess,openAccessPdf,tldr,publicationTypes,publicationDate,journal,authors"
    data = _get(
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi, safe='')}",
        params={"fields": fields},
    )
    # If DOI lookup failed and this is an arXiv DOI, try ArXiv:ID format
    if not data:
        m = re.match(r"10\.48550/arXiv\.(.+)", doi, re.IGNORECASE)
        if m:
            data = _get(
                f"https://api.semanticscholar.org/graph/v1/paper/ArXiv:{m.group(1)}",
                params={"fields": fields},
            )
    return data


def fetch_recommendations(paper_id: str, limit: int = 5) -> list:
    """Fetch recommended papers from Semantic Scholar."""
    data = _get(
        f"https://api.semanticscholar.org/recommendations/v1/papers/forpaper/{paper_id}",
        params={"fields": "title,authors,year,citationCount", "limit": limit},
    )
    if data:
        return data.get("recommendedPapers", [])
    return []


# ---------------------------------------------------------------------------
# API: PubMed / NCBI
# ---------------------------------------------------------------------------

def fetch_pubmed_id(doi: str) -> str | None:
    """Look up PubMed ID for a DOI."""
    data = _get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params={"db": "pubmed", "term": f"{doi}[doi]", "retmode": "json"},
    )
    if data:
        ids = data.get("esearchresult", {}).get("idlist", [])
        if ids:
            return ids[0]
    return None


def fetch_pubmed_pmc(pmid: str) -> str | None:
    """Check if a paper is available in PubMed Central."""
    data = _get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi",
        params={
            "dbfrom": "pubmed", "db": "pmc", "id": pmid, "retmode": "json",
        },
    )
    if data:
        for linkset in data.get("linksets", []):
            for db in linkset.get("linksetdbs", []):
                if db.get("dbto") == "pmc":
                    links = db.get("links", [])
                    if links:
                        return links[0]
    return None


# ---------------------------------------------------------------------------
# API: PubPeer
# ---------------------------------------------------------------------------

def fetch_pubpeer(doi: str) -> dict | None:
    """Fetch PubPeer comments for a DOI."""
    data = _post(
        "https://pubpeer.com/v3/publications",
        json_data={"version": "1.6.2", "browser": "Chrome", "dois": [doi]},
        params={"devkey": "PubMedChrome"},
    )
    if data and data.get("feedbacks"):
        for fb in data["feedbacks"]:
            if fb.get("id", "").lower() == doi.lower():
                return fb
    return None


# ---------------------------------------------------------------------------
# API: Europe PMC
# ---------------------------------------------------------------------------

def fetch_europepmc(doi: str) -> dict | None:
    """Fetch paper data from Europe PMC."""
    data = _get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"},
    )
    if data:
        results = data.get("resultList", {}).get("result", [])
        if results:
            return results[0]
    return None


# ---------------------------------------------------------------------------
# API: Hypothesis
# ---------------------------------------------------------------------------

def fetch_hypothesis_count(doi: str) -> int:
    """Fetch annotation count from Hypothesis."""
    data = _get(
        "https://hypothes.is/api/search",
        params={"uri": f"https://doi.org/{doi}", "limit": 0},
    )
    if data:
        return data.get("total", 0)
    return 0


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def aggregate_paper_data(doi: str) -> dict:
    """Fetch data from all sources and merge into a single report."""
    report = {"doi": doi, "sources": {}}

    print("  CrossRef...", end="", flush=True)
    cr = fetch_crossref(doi)
    if cr:
        report["sources"]["crossref"] = cr
    print(" done")

    _polite_delay()
    print("  OpenAlex...", end="", flush=True)
    oa = fetch_openalex(doi)
    if oa:
        report["sources"]["openalex"] = oa
    print(" done")

    _polite_delay()
    print("  Semantic Scholar...", end="", flush=True)
    ss = fetch_semantic_scholar(doi)
    if ss:
        report["sources"]["semantic_scholar"] = ss
    print(" done")

    _polite_delay()
    print("  PubMed...", end="", flush=True)
    pmid = fetch_pubmed_id(doi)
    if pmid:
        report["pmid"] = pmid
        pmc = fetch_pubmed_pmc(pmid)
        if pmc:
            report["pmc_id"] = pmc
    print(" done")

    _polite_delay()
    print("  Europe PMC...", end="", flush=True)
    epmc = fetch_europepmc(doi)
    if epmc:
        report["sources"]["europepmc"] = epmc
    print(" done")

    _polite_delay()
    print("  PubPeer...", end="", flush=True)
    pp = fetch_pubpeer(doi)
    if pp:
        report["sources"]["pubpeer"] = pp
    print(" done")

    _polite_delay()
    print("  Hypothesis...", end="", flush=True)
    hyp_count = fetch_hypothesis_count(doi)
    report["hypothesis_annotations"] = hyp_count
    print(" done")

    # Recommendations from Semantic Scholar (if we got a paper ID)
    if ss and ss.get("paperId"):
        _polite_delay()
        print("  Recommendations...", end="", flush=True)
        recs = fetch_recommendations(ss["paperId"])
        if recs:
            report["recommendations"] = recs
        print(" done")

    return report


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------

def to_markdown(report: dict) -> str:
    """Convert aggregated report to Markdown."""
    lines = []
    doi = report["doi"]
    cr = report["sources"].get("crossref", {})
    oa = report["sources"].get("openalex", {})
    ss = report["sources"].get("semantic_scholar", {})
    epmc = report["sources"].get("europepmc", {})
    pp = report["sources"].get("pubpeer")

    # Title
    title = (cr.get("title", [None]) or [None])
    if isinstance(title, list):
        title = title[0] if title else None
    title = title or ss.get("title") or oa.get("title") or epmc.get("title") or doi
    lines.append(f"# {title}")
    lines.append("")

    # Metadata
    lines.append(f"**DOI:** [{doi}](https://doi.org/{doi})")

    # Authors
    authors = _extract_authors(cr, oa, ss, epmc)
    if authors:
        display = ", ".join(authors[:10])
        if len(authors) > 10:
            display += " *et al.*"
        lines.append(f"**Authors:** {display}")

    # Journal
    journal = _extract_journal(cr, ss, epmc)
    if journal:
        lines.append(f"**Journal:** {journal}")

    # Date
    date = _extract_date(cr, ss, oa, epmc)
    if date:
        lines.append(f"**Published:** {date}")

    # Type
    pub_type = cr.get("type") or (ss.get("publicationTypes") or [None])[0]
    if pub_type:
        lines.append(f"**Type:** {pub_type}")

    # PubMed
    pmid = report.get("pmid")
    if pmid:
        lines.append(f"**PubMed:** [{pmid}](https://pubmed.ncbi.nlm.nih.gov/{pmid}/)")
    pmc = report.get("pmc_id")
    if pmc:
        lines.append(f"**PMC:** [PMC{pmc}](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc}/)")

    lines.append("")
    lines.append("---")
    lines.append("")

    # TLDR (Semantic Scholar)
    tldr = ss.get("tldr", {})
    if isinstance(tldr, dict) and tldr.get("text"):
        lines.append("## TL;DR")
        lines.append("")
        lines.append(f"*{tldr['text']}*")
        lines.append("")

    # Abstract
    abstract = cr.get("abstract") or epmc.get("abstractText")
    if abstract:
        # Strip HTML tags and collapse whitespace
        abstract = re.sub(r"<[^>]+>", "", abstract)
        abstract = re.sub(r"\s+", " ", abstract).strip()
        lines.append("## Abstract")
        lines.append("")
        lines.append(abstract)
        lines.append("")

    # Citation counts from multiple sources
    lines.append("## Citation Counts")
    lines.append("")
    lines.append("| Source | Citations | Notes |")
    lines.append("|--------|-----------|-------|")

    cr_count = cr.get("is-referenced-by-count")
    if cr_count is not None:
        lines.append(f"| CrossRef | {cr_count:,} | - |")

    oa_count = oa.get("cited_by_count")
    if oa_count is not None:
        percentile = oa.get("cited_by_percentile_year", {}).get("min")
        note = f"{percentile}th percentile for year" if percentile is not None else "-"
        lines.append(f"| OpenAlex | {oa_count:,} | {note} |")

    ss_count = ss.get("citationCount")
    if ss_count is not None:
        influential = ss.get("influentialCitationCount", 0)
        note = f"{influential} influential" if influential else "-"
        lines.append(f"| Semantic Scholar | {ss_count:,} | {note} |")

    epmc_count = epmc.get("citedByCount")
    if epmc_count is not None:
        lines.append(f"| Europe PMC | {epmc_count:,} | - |")

    lines.append("")

    # Open Access
    oa_urls = _extract_oa_links(cr, oa, ss, epmc, report)
    if oa_urls:
        lines.append("## Open Access")
        lines.append("")
        for label, url in oa_urls:
            lines.append(f"- [{label}]({url})")
        lines.append("")

    # Editorial notices / retractions
    notices = _extract_notices(cr)
    if notices:
        lines.append("## Editorial Notices")
        lines.append("")
        for notice in notices:
            lines.append(f"- **{notice}**")
        lines.append("")

    # PubPeer
    if pp:
        comments = pp.get("total_comments", 0)
        if comments > 0:
            lines.append("## PubPeer")
            lines.append("")
            users = pp.get("users", "")
            if isinstance(users, (int, float)):
                user_str = f"{int(users)} user{'s' if users != 1 else ''}"
            elif isinstance(users, str) and users:
                user_str = users + " users"
            else:
                user_str = ""
            url = pp.get("url", f"https://pubpeer.com/search?q={doi}")
            comment_text = f"**{comments} comment{'s' if comments != 1 else ''}**"
            if user_str:
                comment_text += f" from {user_str}"
            lines.append(f"{comment_text} — [View on PubPeer]({url})")
            lines.append("")

    # Hypothesis
    hyp_count = report.get("hypothesis_annotations", 0)
    if hyp_count > 0:
        lines.append("## Hypothesis Annotations")
        lines.append("")
        lines.append(f"**{hyp_count} annotation{'s' if hyp_count != 1 else ''}** — [View](https://hypothes.is/search?q=doi%3A{doi})")
        lines.append("")

    # Topics/concepts from OpenAlex
    concepts = oa.get("concepts", [])
    if concepts:
        top = [c for c in concepts if c.get("score", 0) > 0.3][:8]
        if top:
            lines.append("## Topics")
            lines.append("")
            for c in top:
                score = c.get("score", 0)
                lines.append(f"- {c.get('display_name', '?')} ({score:.0%})")
            lines.append("")

    # Funders
    funders = cr.get("funder", [])
    if funders:
        lines.append("## Funding")
        lines.append("")
        for f in funders:
            name = f.get("name", "Unknown")
            awards = f.get("award", [])
            if awards:
                lines.append(f"- {name} (grants: {', '.join(awards)})")
            else:
                lines.append(f"- {name}")
        lines.append("")

    # References count
    refs = cr.get("reference", [])
    if refs:
        ref_with_doi = sum(1 for r in refs if r.get("DOI"))
        lines.append("## References")
        lines.append("")
        lines.append(f"**{len(refs)} references** ({ref_with_doi} with DOI)")
        lines.append("")

    # Recommendations
    recs = report.get("recommendations", [])
    if recs:
        lines.append("## Recommended Papers")
        lines.append("")
        for r in recs[:5]:
            r_title = r.get("title", "Untitled")
            r_authors = r.get("authors", [])
            r_year = r.get("year", "")
            r_cite = r.get("citationCount", 0)
            author_str = ""
            if r_authors:
                author_str = r_authors[0].get("name", "")
                if len(r_authors) > 1:
                    author_str += " et al."
            parts = [r_title]
            if author_str:
                parts.append(author_str)
            if r_year:
                parts.append(str(r_year))
            if r_cite:
                parts.append(f"{r_cite:,} citations")
            lines.append(f"- {' — '.join(parts)}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by Lazy Scholar CLI on {datetime.now().strftime('%Y-%m-%d')}*")
    return "\n".join(lines)


def _extract_authors(cr, oa, ss, epmc) -> list[str]:
    """Extract author names from the best available source."""
    # CrossRef
    authors = cr.get("author", [])
    if authors:
        names = []
        for a in authors:
            given = a.get("given", "")
            family = a.get("family", "")
            name = f"{given} {family}".strip()
            if name:
                names.append(name)
        if names:
            return names

    # OpenAlex
    authorships = oa.get("authorships", [])
    if authorships:
        return [a.get("author", {}).get("display_name", "") for a in authorships if a.get("author", {}).get("display_name")]

    # Semantic Scholar
    ss_authors = ss.get("authors", [])
    if ss_authors:
        return [a.get("name", "") for a in ss_authors if a.get("name")]

    # Europe PMC
    author_str = epmc.get("authorString", "")
    if author_str:
        return [author_str]

    return []


def _extract_journal(cr, ss, epmc) -> str | None:
    """Extract journal name."""
    ct = cr.get("container-title", [])
    if ct:
        journal = ct[0] if isinstance(ct, list) else ct
        vol = cr.get("volume", "")
        issue = cr.get("issue", "")
        page = cr.get("page", "")
        parts = [journal]
        if vol:
            parts.append(f"vol. {vol}")
        if issue:
            parts.append(f"no. {issue}")
        if page:
            parts.append(f"pp. {page}")
        return ", ".join(parts)

    ss_journal = ss.get("journal", {})
    if isinstance(ss_journal, dict) and ss_journal.get("name"):
        return ss_journal["name"]

    return epmc.get("journalTitle")


def _extract_date(cr, ss, oa, epmc) -> str | None:
    """Extract publication date."""
    for key in ("published-print", "published-online", "issued", "created"):
        dp = cr.get(key, {}).get("date-parts", [[]])
        if dp and dp[0]:
            parts = dp[0]
            if len(parts) >= 3:
                return f"{parts[0]}-{parts[1]:02d}-{parts[2]:02d}"
            elif len(parts) >= 1:
                return str(parts[0])

    if ss.get("publicationDate"):
        return ss["publicationDate"]

    if oa.get("publication_year"):
        return str(oa["publication_year"])

    return epmc.get("pubYear")


def _extract_oa_links(cr, oa, ss, epmc, report) -> list[tuple[str, str]]:
    """Extract open access PDF/HTML links."""
    links = []

    # PMC
    pmc = report.get("pmc_id")
    if pmc:
        links.append(("PubMed Central", f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc}/"))

    # Semantic Scholar OA PDF
    ss_pdf = ss.get("openAccessPdf", {})
    if isinstance(ss_pdf, dict) and ss_pdf.get("url"):
        links.append(("Semantic Scholar PDF", ss_pdf["url"]))

    # OpenAlex OA
    oa_access = oa.get("open_access", {})
    if isinstance(oa_access, dict) and oa_access.get("oa_url"):
        links.append((f"OpenAlex ({oa_access.get('oa_status', 'open')})", oa_access["oa_url"]))

    # Europe PMC
    if epmc.get("isOpenAccess") == "Y":
        epmc_urls = epmc.get("fullTextUrlList", {}).get("fullTextUrl", [])
        for u in epmc_urls:
            if u.get("documentStyle") == "pdf":
                links.append(("Europe PMC PDF", u["url"]))
                break

    # CrossRef license
    licenses = cr.get("license", [])
    for lic in licenses:
        url = lic.get("URL", "")
        if "creativecommons.org" in url:
            links.append(("License", url))
            break

    return links


def _extract_notices(cr) -> list[str]:
    """Extract editorial notices from CrossRef."""
    notices = []

    # Check relation field
    relation = cr.get("relation", {})
    if relation.get("is-retracted-by"):
        notices.append("RETRACTED")
    if relation.get("has-expression-of-concern"):
        notices.append("Expression of concern")

    # Check update-to / updated-by
    for update in cr.get("update-to", []) + cr.get("updated-by", []):
        label = update.get("label", "").lower()
        if "retract" in label:
            notices.append("RETRACTED")
        elif "correction" in label or "erratum" in label:
            notices.append(f"Has correction/erratum")
        elif "concern" in label:
            notices.append("Expression of concern")

    return list(set(notices))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:80] if slug else "report"


def resolve_arxiv_doi(doi: str) -> str:
    """If doi is an arXiv DOI (10.48550/arXiv.*), query Semantic Scholar to
    find the canonical published DOI.  Falls back to CrossRef title search
    if S2 doesn't have the published DOI linked.  Returns the published DOI
    if found, otherwise the original."""
    m = re.match(r"10\.48550/arXiv\.(.+)", doi, re.IGNORECASE)
    if not m:
        return doi
    arxiv_id = m.group(1)
    title = None
    try:
        resp = requests.get(
            f"https://api.semanticscholar.org/graph/v1/paper/ArXiv:{arxiv_id}",
            params={"fields": "externalIds,title"},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
            proxies=_get_proxy(),
        )
        if resp.ok:
            data = resp.json()
            ext = data.get("externalIds") or {}
            published_doi = ext.get("DOI", "")
            if published_doi and published_doi.lower() != doi.lower():
                print(f"  Resolved arXiv DOI → {published_doi}")
                return published_doi
            title = data.get("title")
    except Exception:
        pass
    # Fallback: search CrossRef by title if S2 didn't have a published DOI
    if title:
        try:
            cr_resp = requests.get(
                "https://api.crossref.org/works",
                params={"query.title": title, "rows": 3},
                headers={"User-Agent": USER_AGENT},
                timeout=10,
                proxies=_get_proxy(),
            )
            if cr_resp.ok:
                items = cr_resp.json().get("message", {}).get("items", [])
                for item in items:
                    cr_doi = item.get("DOI", "")
                    cr_title = (item.get("title") or [""])[0].lower()
                    if cr_doi and cr_doi.lower() != doi.lower() and title.lower() in cr_title:
                        print(f"  Resolved arXiv DOI → {cr_doi} (via CrossRef title match)")
                        return cr_doi
        except Exception:
            pass
    return doi


def resolve_input(input_str: str) -> str | None:
    """Resolve input to a DOI."""
    if input_str.startswith("10."):
        return resolve_arxiv_doi(input_str)
    if input_str.startswith(("http://", "https://")):
        doi = extract_doi_from_url(input_str)
        return resolve_arxiv_doi(doi) if doi else None
    path = Path(input_str).expanduser()
    if path.exists() and path.suffix.lower() == ".pdf":
        print(f"Extracting DOI from PDF: {path.name}")
        doi = extract_doi_from_pdf(str(path))
        if doi:
            print(f"  Found DOI: {doi}")
            return resolve_arxiv_doi(doi)
        else:
            print(f"  No DOI found in PDF.", file=sys.stderr)
        return doi
    doi = extract_doi(input_str)
    return resolve_arxiv_doi(doi) if doi else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Aggregate citation data from multiple academic APIs"
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="DOIs, URLs, or PDF file paths",
    )
    parser.add_argument(
        "--title", "-t",
        help="Paper title (for CrossRef search when DOI is unknown)",
    )
    parser.add_argument(
        "--author", "-a",
        help="First author last name (used with --title to narrow search)",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output file path (default: auto-generated)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of Markdown",
    )
    proxy_group = parser.add_mutually_exclusive_group()
    proxy_group.add_argument("--proxy", dest="use_proxy", action="store_true", default=None,
                             help="Force proxy usage (overrides ~/.scholar-proxies.json)")
    proxy_group.add_argument("--no-proxy", dest="use_proxy", action="store_false",
                             help="Disable proxy (overrides ~/.scholar-proxies.json)")
    args = parser.parse_args()

    global _PROXY_FORCE
    _PROXY_FORCE = args.use_proxy

    # Resolve DOIs
    dois = []

    if args.title:
        print(f"Searching CrossRef for: \"{args.title}\"...")
        results = search_crossref(args.title, rows=5)
        if not results:
            print("  No results found.", file=sys.stderr)
            sys.exit(1)

        # If author provided, filter
        if args.author:
            author_lower = args.author.lower()
            filtered = [r for r in results if any(
                author_lower in (a.get("family", "") or "").lower()
                for a in r.get("author", [])
            )]
            if filtered:
                results = filtered

        best = results[0]
        doi = best.get("DOI")
        best_title = best.get("title", [""])[0] if isinstance(best.get("title"), list) else best.get("title", "")
        print(f"  Found: {best_title}")
        print(f"  DOI: {doi}")
        if doi:
            dois.append(doi)

    for inp in (args.inputs or []):
        doi = resolve_input(inp)
        if doi:
            dois.append(doi)
        else:
            print(f"Could not extract DOI from: {inp}", file=sys.stderr)

    if not dois:
        parser.error("No DOIs found. Provide DOIs, URLs, PDFs, or use --title.")

    # Deduplicate
    seen = set()
    unique = []
    for d in dois:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    dois = unique

    check_rate_limit()
    record_request()

    # Process each DOI
    reports = []
    for i, doi in enumerate(dois):
        if i > 0:
            print()
            time.sleep(random.uniform(3, 6))
        print(f"[{i + 1}/{len(dois)}] Fetching data for {doi}...")
        report = aggregate_paper_data(doi)
        reports.append(report)

    if args.json:
        output = reports[0] if len(reports) == 1 else reports
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
        return

    # Generate Markdown
    all_md = []
    for report in reports:
        all_md.append(to_markdown(report))

    md = "\n\n".join(all_md)

    if args.output:
        out_path = Path(args.output)
    else:
        output_dir = Path(__file__).parent / "output"
        output_dir.mkdir(exist_ok=True)
        if len(reports) == 1:
            cr = reports[0]["sources"].get("crossref", {})
            title_field = cr.get("title", ["report"])
            title = title_field[0] if isinstance(title_field, list) else title_field
            name = title or "report"
        else:
            name = f"scholar-report-{len(reports)}-papers"
        out_path = output_dir / f"{slugify(name)}.md"

    out_path.write_text(md, encoding="utf-8")
    print(f"\nReport saved to: {out_path}")


if __name__ == "__main__":
    main()
