# Lazy Scholar CLI

CLI tool to aggregate citation data, metadata, open access links, editorial notices, community feedback, and paper recommendations for academic papers. Queries 8 public APIs in parallel to build a comprehensive report. No authentication or account required.

Data sources: [CrossRef](https://www.crossref.org/), [PubMed/NCBI](https://pubmed.ncbi.nlm.nih.gov/), [OpenAlex](https://openalex.org/), [Semantic Scholar](https://www.semanticscholar.org/), [PubPeer](https://pubpeer.com/), [Europe PMC](https://europepmc.org/), [Hypothesis](https://hypothes.is/), and Semantic Scholar Recommendations.

## Setup

```bash
pip install -r requirements.txt
```

No Playwright needed — all API calls are simple HTTP requests. `pdfminer.six` is only needed if you want to extract DOIs from local PDFs.

## Usage

### Look up a DOI

```bash
python scholar.py 10.1038/s41586-021-03819-2
```

### Look up from a URL

Extracts the DOI automatically from doi.org links, publisher URLs, etc.

```bash
python scholar.py https://doi.org/10.1038/s41586-021-03819-2
python scholar.py https://www.nature.com/articles/s41586-021-03819-2
```

### Look up from a local PDF

Extracts the DOI from PDF metadata or the first few pages of text.

```bash
python scholar.py paper.pdf
```

### Look up by title and author

When you don't have a DOI, search CrossRef by paper title and first author:

```bash
python scholar.py --title "Attention is All You Need" --author Vaswani
```

### Batch lookup

Pass multiple DOIs, URLs, or PDFs at once:

```bash
python scholar.py 10.1038/s41586-021-03819-2 10.1038/s41586-020-2649-2
```

### JSON output

Dump the raw aggregated data instead of Markdown:

```bash
python scholar.py 10.1038/s41586-021-03819-2 --json
```

### Custom output path

```bash
python scholar.py 10.1038/s41586-021-03819-2 --output my-report.md
```

## Output

Reports are saved as Markdown to the `output/` directory by default. Each report includes:

- Paper metadata (title, authors, journal, volume/issue/page, publication date, type)
- TL;DR (from Semantic Scholar's TLDR model)
- Abstract (from CrossRef or Europe PMC)
- Citation counts from 4 independent sources (CrossRef, OpenAlex, Semantic Scholar, Europe PMC)
- PubMed ID and PMC links
- Open access links (PMC, Semantic Scholar PDF, OpenAlex OA, Europe PMC PDF, Creative Commons license)
- Editorial notices (retractions, corrections, expressions of concern)
- PubPeer comments (count + link)
- Hypothesis annotations (count + link)
- Topics/concepts (from OpenAlex, scored by relevance)
- Funding information (from CrossRef)
- Reference count
- Recommended papers (from Semantic Scholar Recommendations API)

## Rate Limits

The tool is deliberately conservative to avoid detection and stay under the radar:

- **5 seconds** minimum between invocations
- **10 invocations** per hour maximum
- **0.5–2 second random delay** (jitter) between each API call within a single invocation
- **3–6 second random delay** between papers in batch mode

A single invocation makes 8-9 API calls across 7 different services, so the effective API call rate is spread across multiple domains. The jitter prevents bursty request patterns that would stand out in server logs.

Rate limits are tracked per-invocation (not per API call) in `.rate_limit_log`.

All requests use a standard Chrome User-Agent. No custom headers, no identifying information.

## How It Works

### The APIs

All 8 data sources are **public and unauthenticated**. These are the same endpoints the [Lazy Scholar](https://chrome.google.com/webstore/detail/lazy-scholar/fpbejnkbipighommbphkhpphcggohlld) browser extension uses. The extension makes the exact same calls with zero auth headers.

### How the extension was reverse-engineered

The Lazy Scholar browser extension was found installed in Chrome and Brave Browser. The extension's source consists of a background script and content scripts that make direct API calls when the user clicks the toolbar icon on a paper page.

Key findings:

- **No authentication** on any API call — all endpoints are public
- **DOI-based lookups** — every API call uses DOI as the primary identifier
- **PubPeer uses a public devkey** (`PubMedChrome`) as a query parameter — this is hardcoded in the extension and not a secret
- **CrossRef is the primary source** — used for metadata, citations, references, funding, editorial notices
- **Multiple fallback sources** — OpenAlex, Semantic Scholar, and Europe PMC provide overlapping citation data for cross-verification

### Endpoints

#### CrossRef — `https://api.crossref.org`

The primary metadata source. Returns the richest data per DOI.

**GET /works/{doi}**

```
GET https://api.crossref.org/works/10.1038/s41586-021-03819-2
```

Response (abbreviated):

```json
{
  "status": "ok",
  "message": {
    "DOI": "10.1038/s41586-021-03819-2",
    "title": ["Highly accurate protein structure prediction with AlphaFold"],
    "author": [
      {"given": "John", "family": "Jumper", "sequence": "first"},
      {"given": "Richard", "family": "Evans", "sequence": "additional"}
    ],
    "container-title": ["Nature"],
    "volume": "596",
    "issue": "7873",
    "page": "583-589",
    "published-print": {"date-parts": [[2021, 8, 26]]},
    "type": "journal-article",
    "is-referenced-by-count": 38431,
    "reference": [{"DOI": "10.1006/jmbi.1999.3091", "key": "..."}, ...],
    "funder": [{"name": "EPSRC", "award": ["EP/P020259/1"]}],
    "license": [{"URL": "https://creativecommons.org/licenses/by/4.0"}],
    "relation": {"is-retracted-by": [], "has-expression-of-concern": []}
  }
}
```

Used for: title, authors, journal info, publication date, type, citation count, references, funders, license, editorial notices (retractions, corrections, expressions of concern).

**GET /works?query.title={title}** — title search (used with `--title` flag)

```
GET https://api.crossref.org/works?query.title=Attention+is+All+You+Need&rows=5&select=DOI,title,author,issued,container-title,is-referenced-by-count,publisher,type
```

#### OpenAlex — `https://api.openalex.org`

**GET /works/https://doi.org/{doi}**

```
GET https://api.openalex.org/works/https://doi.org/10.1038/s41586-021-03819-2
```

Response (abbreviated):

```json
{
  "id": "https://openalex.org/W3177828909",
  "title": "Highly accurate protein structure prediction with AlphaFold",
  "cited_by_count": 38523,
  "cited_by_percentile_year": {"min": 99},
  "open_access": {
    "is_oa": true,
    "oa_status": "gold",
    "oa_url": "https://www.nature.com/articles/s41586-021-03819-2.pdf"
  },
  "concepts": [
    {"display_name": "Protein structure prediction", "score": 0.97},
    {"display_name": "Protein structure", "score": 0.92}
  ],
  "authorships": [
    {"author": {"display_name": "John Jumper"}}
  ]
}
```

Used for: citation count with percentile ranking, open access status/URL, topic concepts with relevance scores, author data (fallback).

Note: OpenAlex has moved to a paid pricing model and enforces daily rate limits. The tool retries on 429 responses with random 3-8s backoff.

#### Semantic Scholar — `https://api.semanticscholar.org`

**GET /graph/v1/paper/DOI:{doi}**

```
GET https://api.semanticscholar.org/graph/v1/paper/DOI:10.1038/s41586-021-03819-2?fields=paperId,title,citationCount,influentialCitationCount,isOpenAccess,openAccessPdf,tldr,publicationTypes,publicationDate,journal,authors
```

Response (abbreviated):

```json
{
  "paperId": "d0e6e465b58f2cdd10bb7e0fe6519b18a6c1f3f0",
  "title": "Highly accurate protein structure prediction with AlphaFold",
  "citationCount": 32145,
  "influentialCitationCount": 2847,
  "isOpenAccess": true,
  "openAccessPdf": {"url": "https://www.nature.com/articles/s41586-021-03819-2.pdf"},
  "tldr": {"text": "This paper presents AlphaFold, a neural network..."},
  "publicationTypes": ["JournalArticle"],
  "authors": [{"name": "John Jumper"}, ...]
}
```

Used for: TL;DR summary, citation count with influential citation count, OA PDF link, publication type, author data (fallback).

**GET /recommendations/v1/papers/forpaper/{paperId}** — related papers

```
GET https://api.semanticscholar.org/recommendations/v1/papers/forpaper/d0e6e465b58f2cdd10bb7e0fe6519b18a6c1f3f0?fields=title,authors,year,citationCount&limit=5
```

Response:

```json
{
  "recommendedPapers": [
    {
      "title": "Highly accurate protein structure prediction for the human proteome",
      "authors": [{"name": "Kathryn Tunyasuvunakool"}],
      "year": 2021,
      "citationCount": 3847
    }
  ]
}
```

Used for: related paper recommendations (only fetched if Semantic Scholar returns a paperId).

#### PubMed / NCBI E-Utilities — `https://eutils.ncbi.nlm.nih.gov`

Two sequential calls: first look up the PubMed ID, then check for PMC availability.

**GET /entrez/eutils/esearch.fcgi** — DOI → PubMed ID

```
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=10.1038/s41586-021-03819-2[doi]&retmode=json
```

Response:

```json
{
  "esearchresult": {
    "count": "1",
    "idlist": ["34265844"]
  }
}
```

**GET /entrez/eutils/elink.fcgi** — PubMed ID → PMC ID

```
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi?dbfrom=pubmed&db=pmc&id=34265844&retmode=json
```

Response (abbreviated):

```json
{
  "linksets": [{
    "linksetdbs": [{
      "dbto": "pmc",
      "links": ["8371605"]
    }]
  }]
}
```

Used for: PubMed ID link, PMC full-text link.

#### Europe PMC — `https://www.ebi.ac.uk/europepmc`

**GET /webservices/rest/search**

```
GET https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:"10.1038/s41586-021-03819-2"&format=json&resultType=core
```

Response (abbreviated):

```json
{
  "resultList": {
    "result": [{
      "title": "Highly accurate protein structure prediction with AlphaFold",
      "citedByCount": 29132,
      "isOpenAccess": "Y",
      "abstractText": "Proteins are essential to life...",
      "fullTextUrlList": {
        "fullTextUrl": [
          {"documentStyle": "pdf", "url": "https://www.nature.com/articles/s41586-021-03819-2.pdf"}
        ]
      }
    }]
  }
}
```

Used for: citation count, abstract (fallback), OA PDF links, journal info (fallback).

#### PubPeer — `https://pubpeer.com`

**POST /v3/publications?devkey=PubMedChrome**

```
POST https://pubpeer.com/v3/publications?devkey=PubMedChrome
Content-Type: application/json

{"version": "1.6.2", "browser": "Chrome", "dois": ["10.1038/s41586-021-03819-2"]}
```

Response:

```json
{
  "feedbacks": [{
    "id": "10.1038/s41586-021-03819-2",
    "total_comments": 4,
    "users": "John A. Cooper, Cacyparis Melanolitha, Arundelia Dissimilis, Scalopus Aquaticus",
    "url": "https://pubpeer.com/publications/C2EE9B9C04557C7ED7CFCF17309503"
  }]
}
```

The `devkey=PubMedChrome` parameter is hardcoded in the Lazy Scholar extension. It's a public identifier, not a secret — the PubPeer API uses it to track which extensions are making calls, not for authentication. The request body includes browser and version metadata matching what the extension sends.

Used for: post-publication comment count, direct link to PubPeer discussion.

#### Hypothesis — `https://hypothes.is`

**GET /api/search**

```
GET https://hypothes.is/api/search?uri=https://doi.org/10.1038/s41586-021-03819-2&limit=0
```

Response:

```json
{
  "total": 12,
  "rows": []
}
```

With `limit=0`, only the total count is returned (no annotation bodies). Used for: annotation count and link to Hypothesis search.

### DOI extraction from URLs and PDFs

When given a URL or PDF instead of a bare DOI, the tool extracts DOIs using this regex:

```
10.\d{4,9}/[^\s"'<>\])}{,]+
```

For URLs: parses `doi.org/` links first, then falls back to searching the full URL.

For PDFs: checks metadata fields (`doi`, `Subject`, `Title`, `WPS-ARTICLEDOI`), then extracts text from the first 3 pages and searches for the DOI pattern. Common trailing characters (`.pdf`, `.html`, `.full`, `.xml`) and punctuation (`.,;:`) are stripped.

### Traffic profile

Each invocation makes 8-9 HTTP calls across 7 different domains:

1. `api.crossref.org` — 1 call (metadata)
2. `api.openalex.org` — 1 call (citations + concepts)
3. `api.semanticscholar.org` — 1-2 calls (paper + recommendations)
4. `eutils.ncbi.nlm.nih.gov` — 2 calls (PubMed ID + PMC link)
5. `ebi.ac.uk` (Europe PMC) — 1 call
6. `pubpeer.com` — 1 call
7. `hypothes.is` — 1 call

Between each call: 0.5–2 second random delay. Between invocations: minimum 5 seconds. Max 10 invocations per hour. Between papers in batch mode: 3–6 second random delay.

All requests use a standard Chrome User-Agent. No custom headers, no identifying information.

### 429 Retry Logic

Several APIs (OpenAlex, Semantic Scholar) enforce rate limits and return HTTP 429. The tool handles this automatically:

- Up to 2 retries per request
- Random 3–8 second backoff on 429 responses
- 2 second backoff on other transient failures
- Gracefully degrades — if a source is unavailable, the report is still generated from the remaining sources

### Files

| File | Purpose |
|---|---|
| `.rate_limit_log` | Invocation timestamps for rate limiting (gitignored) |
| `output/` | Generated Markdown reports (gitignored) |

## Dependencies

- `requests` — HTTP calls to all APIs
- `pdfminer.six` — PDF DOI extraction (optional, only needed for PDF input)
