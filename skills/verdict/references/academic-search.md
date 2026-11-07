# University Network & Academic Database Search (`verdict search` & `verdict fetch`)

## Why local execution matters on a university network

Cloud-hosted AI web-search tools execute from remote datacenter IPs, so academic publishers do **not**
recognize your university's institutional subscription. `verdict search`, `verdict fetch`, and
`verdict netcheck` run **directly on your machine**, so requests exit through your campus Wi-Fi,
university VPN, or institutional proxy.

*(If your AI coding assistant runs terminal commands inside a network-disabled sandbox by default,
allow `verdict search`, `verdict fetch`, and `verdict netcheck` to run with local network access.)*

## How each database works

| Database (`--db`) | Discovery without API key | Campus full-text / PDF behavior | Optional key / env var |
|---|---|---|---|
| `sciencedirect` | **Yes** — queries Crossref (`prefix:10.1016`) + OpenAlex Elsevier index with reconstructed abstracts & OA links | With free `ELSEVIER_API_KEY` on a **subscribing university IP**, `api.elsevier.com` returns **complete article full text** (`FULL` view). Also resolves OA/arXiv twins automatically. | `ELSEVIER_API_KEY` (free at `dev.elsevier.com`), optional `ELSEVIER_INST_TOKEN` |
| `ieee` | **Yes** — Crossref (`prefix:10.1109`) + OpenAlex + OA/arXiv twin resolution | Direct `IEEE_API_KEY` search + OA/repository PDF fetch | `IEEE_API_KEY` |
| `acm` | **Yes** — Crossref (`prefix:10.1145`) + OpenAlex | Fetches `dl.acm.org/doi/pdf/<doi>` directly over subscribed campus IP or ACM OPEN | None needed |
| `springer` | **Yes** — Crossref (`prefix:10.1007`, `10.1038`) + OpenAlex | Fetches `link.springer.com/content/pdf/<doi>.pdf` directly over subscribed campus IP | Optional `SPRINGER_API_KEY` |
| `arxiv` | **Yes** — arXiv Atom API | Direct PDF download + `pdftotext` extraction | None needed |
| `semanticscholar` | **Yes** — Semantic Scholar Graph API | Returns `openAccessPdf` links + citation counts | Optional `S2_API_KEY` |
| `openalex` / `crossref` | **Yes** — 250M+ works across all publishers | Best-OA repository PDF + TDM links | Optional `UNPAYWALL_EMAIL` (polite pool + Unpaywall) |

## Setting credentials cleanly (never in your repository)

Create `~/.config/verdict/credentials.json` (or export environment variables):

```json
{
  "elsevier_api_key": "YOUR_FREE_ELSEVIER_DEV_KEY",
  "polite_email": "you@university.edu",
  "ezproxy_prefix": "https://ezproxy.your-university.edu/login?url="
}
```

## University VPN split-tunnel check

Many university VPN profiles (`eduVPN`, Cisco AnyConnect) use **split tunneling** by default, routing
internal `*.uni-domain` hosts over VPN while sending `api.elsevier.com`, `dl.acm.org`, and
`link.springer.com` over your home ISP.

Run:

```bash
verdict netcheck
```

If `sciencedirect_campus_status` reports that your API key works but your IP is not entitled, switch
your VPN client to a **full-tunnel / library profile** (or set `HTTPS_PROXY` / `ezproxy_prefix`).

