#!/usr/bin/env python3
"""site-crawler: local bounded site crawler for static HTML.

Recursively crawl within the start host, dedup normalized URLs, extract main
content with trafilatura, and write markdown mirroring the site structure.
Each normalized URL maps to a UNIQUE output file; pre-existing files are
never overwritten. --max-pages caps successfully extracted pages only;
--max-requests caps total HTTP requests (errors, no-text pages and every
redirect hop count). Redirects are followed hop-by-hop, and the crawl scope
is checked before EACH hop is requested.

Usage:
  python crawl_site.py --url https://docs.example.com/ --out ./out --max-pages 50 --max-requests 150
"""
import argparse
import hashlib
import math
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests
import trafilatura

UA = "Mozilla/5.0 (compatible; SiteCrawler/1.0; +local knowledge ingestion)"
TIMEOUT = 20
MAX_REDIRECTS = 5  # manual hops per entry URL; each hop counts against the request budget
BINARY_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
              ".pdf", ".zip", ".gz", ".tar", ".7z", ".mp3", ".mp4", ".avi",
              ".mov", ".wav", ".woff", ".woff2", ".ttf", ".eot", ".exe", ".dmg")
TRACKING_PARAMS = re.compile(r"^(utm_|ref|spm|from|source|fbclid|gclid|mtm_|mc_|pk_)", re.I)


def normalize_url(url: str) -> str:
    """Strip fragment + tracking params, collapse slashes, keep query otherwise."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    path = re.sub(r"/{2,}", "/", parts.path)
    # unify trailing slash: /docs and /docs/ are the same page
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if not TRACKING_PARAMS.search(k)]
    query_str = urllib.parse.urlencode(query)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query_str, ""))


def looks_binary(url: str) -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    return path.endswith(BINARY_EXT)


def is_internal(url: str, base_netloc: str, allow_subdomains: bool = False) -> bool:
    """Strict same-host by default (exact netloc incl. port);
    subdomains of the base host only when explicitly allowed."""
    try:
        netloc = urllib.parse.urlsplit(url).netloc
    except ValueError:
        return False
    if urllib.parse.urlsplit(url).scheme.lower() not in {"http", "https"}:
        return False
    netloc, base = netloc.lower(), base_netloc.lower()
    if allow_subdomains:
        return netloc == base or netloc.endswith("." + base)
    return netloc == base


def extract_links(html: str, base_url: str) -> list[str]:
    """Pull same-scheme http(s) links from raw HTML."""
    links = []
    # minimal anchor href extraction (no bs4 dependency)
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.I):
        href = m.group(1).strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        resolved = urllib.parse.urljoin(base_url, href)
        if resolved.startswith(("http://", "https://")):
            links.append(resolved)
    return links


def page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return t[:120]
    return ""


def fetch(url: str, session: requests.Session, delay: float) -> tuple[str | None, object | None, str | None]:
    """Return (html, response, error). None html on failure.

    Redirects are NOT followed here: the caller inspects each 3xx hop and
    checks the crawl scope before issuing the next request."""
    time.sleep(delay)
    try:
        resp = session.get(url, timeout=TIMEOUT, allow_redirects=False)
        resp.raise_for_status()
        return resp.text, resp, None
    except requests.RequestException as e:
        return None, None, f"{type(e).__name__}: {e}"


def sanitize_filename(path_part: str) -> str:
    path_part = path_part.replace("/", "_").replace("\\", "_")
    path_part = re.sub(r"[<>:\"/\\|?*]", "_", path_part)
    path_part = path_part.strip(" ._")
    path_part = (path_part or "index")[:100]
    if path_part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        path_part = "_" + path_part
    return path_part


def main() -> int:
    ap = argparse.ArgumentParser(description="Local bounded site crawler for static HTML")
    ap.add_argument("--url", required=True, help="Start URL")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--max-pages", type=int, default=100,
                    help="Cap on successfully extracted pages (NOT a total-request cap)")
    ap.add_argument("--max-requests", type=int, default=0,
                    help="Total HTTP request budget: errors, no-text pages and every "
                         "redirect hop count; 0 = 3x max-pages")
    ap.add_argument("--max-consecutive-errors", type=int, default=5,
                    help="Abort (exit 2) after this many consecutive fetch errors")
    ap.add_argument("--delay", type=float, default=0.5, help="Delay before each request (s)")
    ap.add_argument("--same-host-only", action="store_true", default=True,
                    help="Compat flag: scope is ALWAYS limited to the start host; strict by default")
    ap.add_argument("--allow-subdomains", action="store_true",
                    help="Explicitly include subdomains of the start host (default: excluded)")
    ap.add_argument("--skip-prefix", action="append", default=[], help="Skip URLs containing prefix")
    args = ap.parse_args()

    if args.max_pages <= 0 or args.max_requests < 0 or args.max_consecutive_errors <= 0:
        ap.error("max-pages/errors must be positive; max-requests is 0 (auto) or positive")
    if not math.isfinite(args.delay) or args.delay < 0:
        ap.error("delay must be finite and nonnegative")
    start = normalize_url(args.url)
    try:
        parts = urllib.parse.urlsplit(start)
        valid = (parts.scheme.lower() in {"http", "https"} and parts.hostname
                 and parts.username is None and parts.password is None)
        parts.port  # validate the port before creating any output or requesting
    except ValueError:
        valid = False
    if not valid:
        ap.error("url must be an HTTP(S) URL with a valid host/port and without credentials")
    base_netloc = parts.netloc
    domain_dir = base_netloc.replace(":", "_")
    out_dir = Path(args.out) / domain_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    request_budget = args.max_requests if args.max_requests > 0 else args.max_pages * 3

    def in_scope(u: str) -> bool:
        if looks_binary(u):
            return False
        if not is_internal(u, base_netloc, args.allow_subdomains):
            return False
        if any(p in u for p in args.skip_prefix):
            return False
        return True

    seen: set[str] = set()
    queue: list[str] = [start]
    taken: dict[Path, str] = {}
    stats = {"ok": 0, "no_text": 0, "skip": 0, "error": 0, "requests": 0, "redirects": 0}
    errors: list[str] = []
    consecutive_errors = 0
    aborted = False

    scope_mode = "host+subdomains" if args.allow_subdomains else "strict-same-host"
    print(f"[site-crawler] start={start} out={out_dir} max-pages={args.max_pages} "
          f"max-requests={request_budget} delay={args.delay} scope={scope_mode}")

    while queue and stats["ok"] < args.max_pages:
        if stats["requests"] >= request_budget:
            print(f"  [BUDGET] request budget {request_budget} reached; stopping before further requests")
            aborted = True
            break
        url = queue.pop(0)
        norm = normalize_url(url)
        if norm in seen:
            continue
        seen.add(norm)

        if not in_scope(norm):
            stats["skip"] += 1
            continue

        # Follow the redirect chain manually; the scope is re-checked before
        # EACH hop is requested. No request is ever auto-issued to a target
        # outside scope (cross-host redirect stops here).
        cur, hops = norm, 0
        html = None
        fetch_failed = False
        while True:
            html, resp, err = fetch(cur, session, args.delay)
            stats["requests"] += 1
            if err is not None:
                stats["error"] += 1
                consecutive_errors += 1
                errors.append(f"{cur} -> {err}")
                print(f"  [ERR] {cur} ({err})")
                fetch_failed = True
                break
            consecutive_errors = 0
            if resp is not None and 300 <= resp.status_code < 400:
                stats["redirects"] += 1
                loc = (resp.headers.get("Location") or "").strip()
                nxt = normalize_url(urllib.parse.urljoin(cur, loc)) if loc else ""
                if not loc or nxt == cur or nxt in seen or hops >= MAX_REDIRECTS:
                    stats["skip"] += 1
                    print(f"  [SKIP-REDIRECT] {cur} -> {loc or '(no Location)'}: unresolvable/seen/loop")
                    html = None
                    break
                if not in_scope(nxt):
                    stats["skip"] += 1
                    print(f"  [SKIP-REDIRECT] {cur} -> {nxt}: out of scope, request NOT issued")
                    html = None
                    break
                seen.add(nxt)
                cur, hops = nxt, hops + 1
                if stats["requests"] >= request_budget:
                    stats["skip"] += 1
                    print(f"  [SKIP-REDIRECT] {cur}: request budget exhausted, request NOT issued")
                    html = None
                    aborted = True
                    break
                continue
            break
        if fetch_failed:
            if consecutive_errors >= args.max_consecutive_errors:
                aborted = True
                print(f"  [ABORT] {consecutive_errors} consecutive errors reached; stopping")
                break
            continue
        if html is None:
            continue

        extracted = trafilatura.extract(html, output_format="markdown",
                                        include_links=False, with_metadata=True)
        if not extracted:
            stats["no_text"] += 1
            print(f"  [NO-TEXT] {cur}")
        else:
            title = page_title(html)
            # trafilatura's metadata output may start with "**Title:** ..."; keep raw markdown
            out_path = _url_to_file(cur, out_dir, taken)
            if out_path is None:
                stats["skip"] += 1
                print(f"  [CONFLICT] {cur}: no unique non-overwriting path; nothing written")
                continue
            header = f"<!-- source: {cur} -->\n"
            if title and not extracted.startswith("# "):
                header += f"# {title}\n\n"
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with out_path.open("x", encoding="utf-8") as output:
                    output.write(header + extracted)
            except OSError as exc:
                stats["error"] += 1
                errors.append(f"{cur} -> output {type(exc).__name__}")
                print(f"  [OUTPUT-ERR] {cur}: {type(exc).__name__}; existing file not overwritten")
                continue
            stats["ok"] += 1
            print(f"  [OK] {cur} -> {out_path}")

        # enqueue links from current page (resolved against the final URL)
        for link in extract_links(html, cur):
            if normalize_url(link) not in seen:
                queue.append(link)

    print("\n[site-crawler] done")
    print(f"  ok={stats['ok']} no_text={stats['no_text']} skip={stats['skip']} "
          f"error={stats['error']} requests={stats['requests']}/{request_budget} "
          f"redirects={stats['redirects']}")
    if errors:
        print("  errors:")
        for e in errors[:20]:
            print(f"    {e}")
    if aborted:
        return 2
    return 0 if stats["error"] == 0 else 1


def _url_to_file(url: str, out_dir: Path, taken: dict[Path, str]) -> Path | None:
    """Map a normalized URL to a UNIQUE output path mirroring site structure.

    - path segments mirror the URL; a non-tracking query is kept in the file
      name so /a?id=1 and /a?id=2 do not collapse into one file
    - if another URL already claimed the path (e.g. /foo vs /foo.html), or a
      pre-existing file from an earlier run sits there, a deterministic sha1
      suffix is used instead of overwriting
    - returns None when even the suffixed path is occupied (caller must skip)
    """
    parts = urllib.parse.urlsplit(url)
    rel = parts.path.lstrip("/")
    if not rel:
        rel = "index"
    if rel.endswith("/"):
        rel = rel.rstrip("/") + "/index"
    # strip file extension if any (html/htm/php/asp), else keep as-is
    no_ext = re.sub(r"\.(html?|php|aspx?|jsp)$", "", rel, flags=re.I)
    base, _, tail = no_ext.rpartition("/")
    name = sanitize_filename(tail) if tail else "index"
    if parts.query:
        q = re.sub(r"[^0-9A-Za-z._-]+", "_", parts.query).strip("._-")
        if q:
            name = f"{name}__{q}"
    base = Path(*(sanitize_filename(segment) for segment in base.split("/") if segment))
    name = name[:140]
    candidate = out_dir / base / f"{name}.md"
    if not candidate.resolve().is_relative_to(out_dir.resolve()):
        return None
    if candidate in taken or candidate.exists():
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
        candidate = out_dir / base / f"{name}-{digest}.md"
        if candidate in taken or candidate.exists():
            return None
    taken[candidate] = url
    return candidate


if __name__ == "__main__":
    sys.exit(main())
