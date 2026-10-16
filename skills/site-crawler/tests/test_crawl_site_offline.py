#!/usr/bin/env python3
"""Offline behavioral tests for site-crawler crawl_site.py (no network, no deps).

requests/trafilatura are stubbed via sys.modules BEFORE the crawler module is
loaded; routes are mocked responses. This release draft retains the 19 current
behavior cases; three historical preimage reproductions stay in private evidence.

Run:  python test_crawl_site_offline.py -v
"""
import contextlib
import hashlib
import importlib.util
import io
import re
import shutil
import sys
import tempfile
import types
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
CAND_ROOT = HERE.parent
CAND_SCRIPT = CAND_ROOT / "scripts" / "crawl_site.py"

BASE = "https://docs.example.com"
CTX = {"router": {}, "log": []}


# ---------------------------------------------------------------- stub modules
def install_stubs():
    requests_mod = types.ModuleType("requests")

    class RequestException(Exception):
        pass

    class HTTPError(RequestException):
        pass

    class Response:
        def __init__(self, url, status, body, headers):
            self.url = url
            self.status_code = status
            self.text = body
            self.headers = headers

        def raise_for_status(self):
            if self.status_code >= 400:
                raise HTTPError(f"{self.status_code} Client Error for url: {self.url}")

    class Session:
        def __init__(self):
            self.headers = {}

        def _route(self, url):
            CTX["log"].append(url)
            raw = CTX["router"].get(url)
            if raw is None:
                raise RequestException(f"unrouted mock url: {url}")
            spec = raw if isinstance(raw, dict) else {"status": 200, "body": raw}
            return Response(url, spec.get("status", 200), spec.get("body", ""),
                            dict(spec.get("headers", {})))

        def get(self, url, timeout=None, allow_redirects=True, **kw):
            resp = self._route(url)
            if allow_redirects:  # emulate real requests' default auto-follow
                hops = 0
                while 300 <= resp.status_code < 400 and hops < 10:
                    hops += 1
                    loc = resp.headers.get("Location")
                    if not loc:
                        break
                    resp = self._route(urllib.parse.urljoin(resp.url, loc))
            return resp

    requests_mod.RequestException = RequestException
    requests_mod.HTTPError = HTTPError
    requests_mod.Session = Session
    sys.modules["requests"] = requests_mod

    trafo = types.ModuleType("trafilatura")

    def extract(html, output_format=None, include_links=None, with_metadata=None):
        if "<NO-TEXT>" in html:
            return None
        m = re.search(r'<div id="main">(.*?)</div>', html, re.S)
        return f"Body: {m.group(1)}" if m else "Body: ?"

    trafo.extract = extract
    sys.modules["trafilatura"] = trafo


def load_crawler(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def page(marker, links=()):
    anchors = "".join(f'<a href="{u}">{u}</a>' for u in links)
    return (f"<html><head><title>T-{marker}</title></head><body>"
            f'<div id="main">{marker}</div>{anchors}</body></html>')


def run_crawler(mod, args, router):
    CTX["router"], CTX["log"] = router, []
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["crawl_site.py"] + args), \
         contextlib.redirect_stdout(out):
        code = mod.main()
    return code, out.getvalue(), list(CTX["log"])


class CrawlerCase(unittest.TestCase):
    candidate = None

    @classmethod
    def setUpClass(cls):
        install_stubs()
        cls.candidate = load_crawler(CAND_SCRIPT, "crawl_candidate")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="site-crawler-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = self.tmp / "out"

    def files(self):
        return {p.name: p.read_text(encoding="utf-8")
                for p in sorted((self.out).rglob("*.md"))}

    def assert_no_host(self, log, host):
        for u in log:
            self.assertNotIn(host, urllib.parse.urlsplit(u).netloc,
                             f"request issued outside scope: {u}")


# ------------------------------------------------------ candidate: mapping
class TestCandidateUniqueMapping(CrawlerCase):

    def test_query_variants_map_to_unique_files(self):
        router = {
            f"{BASE}/": page("home", ["/a?id=1", "/a?id=2"]),
            f"{BASE}/a?id=1": page("one"),
            f"{BASE}/a?id=2": page("two"),
        }
        code, log_txt, reqs = run_crawler(
            self.candidate, ["--url", f"{BASE}/", "--out", str(self.out),
                             "--max-pages", "10", "--delay", "0"], router)
        self.assertEqual(code, 0)
        files = self.files()
        self.assertIn("a__id_1.md", files)
        self.assertIn("a__id_2.md", files)
        self.assertIn("Body: one", files["a__id_1.md"])
        self.assertIn("Body: two", files["a__id_2.md"])
        self.assertNotIn("<!-- source: https://docs.example.com/a?id=2 -->",
                         files["a__id_1.md"])  # each file keeps its own source

    def test_foo_vs_foo_html_unique(self):
        router = {
            f"{BASE}/": page("home", ["/foo", "/foo.html"]),
            f"{BASE}/foo": page("plain-foo"),
            f"{BASE}/foo.html": page("html-foo"),
        }
        code, log_txt, reqs = run_crawler(
            self.candidate, ["--url", f"{BASE}/", "--out", str(self.out),
                             "--max-pages", "10", "--delay", "0"], router)
        self.assertEqual(code, 0)
        files = self.files()
        self.assertEqual(len(files), 3)  # index + two non-colliding foo variants
        digest = hashlib.sha1(f"{BASE}/foo.html".encode()).hexdigest()[:8]
        self.assertIn(f"foo-{digest}.md", files)  # /foo.html: collision -> deterministic suffix
        self.assertIn("foo.md", files)
        all_bodies = " ".join(files.values())
        self.assertIn("Body: plain-foo", all_bodies)
        self.assertIn("Body: html-foo", all_bodies)

    def test_existing_files_never_overwritten(self):
        host_dir = self.out / "docs.example.com"
        host_dir.mkdir(parents=True)
        (host_dir / "index.md").write_text("OLD RESULT FROM EARLIER RUN", encoding="utf-8")
        router = {f"{BASE}/": page("fresh-home")}
        code, log_txt, reqs = run_crawler(
            self.candidate, ["--url", f"{BASE}/", "--out", str(self.out),
                             "--max-pages", "10", "--delay", "0"], router)
        self.assertEqual(code, 0)
        self.assertEqual((host_dir / "index.md").read_text(encoding="utf-8"),
                         "OLD RESULT FROM EARLIER RUN")
        digest = hashlib.sha1(f"{BASE}/".encode()).hexdigest()[:8]
        self.assertIn(f"index-{digest}.md", self.files())
        self.assertIn("Body: fresh-home", self.files()[f"index-{digest}.md"])


# ------------------------------------------------------ candidate: scope + redirects
class TestCandidateScopeAndRedirects(CrawlerCase):

    def test_cross_host_redirect_target_not_requested(self):
        router = {
            f"{BASE}/": page("home", ["/ext", "https://evil.example.org/direct"]),
            f"{BASE}/ext": {"status": 302, "body": "",
                            "headers": {"Location": "https://evil.example.org/payload"}},
            "https://evil.example.org/payload": page("STOLEN"),
            "https://evil.example.org/direct": page("DIRECT"),
        }
        code, log_txt, reqs = run_crawler(
            self.candidate, ["--url", f"{BASE}/", "--out", str(self.out),
                             "--max-pages", "10", "--delay", "0"], router)
        self.assert_no_host(reqs, "evil.example.org")
        self.assertIn("out of scope, request NOT issued", log_txt)
        bodies = " ".join(self.files().values())
        self.assertNotIn("STOLEN", bodies)
        self.assertNotIn("DIRECT", bodies)
        self.assertEqual(code, 0)  # out-of-scope skips are not fetch errors

    def test_subdomain_denied_by_default(self):
        router = {
            f"{BASE}/": page("home", ["https://cdn.docs.example.com/x"]),
            "https://cdn.docs.example.com/x": page("cdn-x"),
        }
        code, log_txt, reqs = run_crawler(
            self.candidate, ["--url", f"{BASE}/", "--out", str(self.out),
                             "--max-pages", "10", "--delay", "0"], router)
        self.assert_no_host(reqs, "cdn.docs.example.com")
        self.assertIn("strict-same-host", log_txt)

    def test_allow_subdomains_explicit(self):
        router = {
            f"{BASE}/": page("home", ["https://cdn.docs.example.com/x"]),
            "https://cdn.docs.example.com/x": page("cdn-x"),
        }
        code, log_txt, reqs = run_crawler(
            self.candidate, ["--url", f"{BASE}/", "--out", str(self.out),
                             "--max-pages", "10", "--delay", "0",
                             "--allow-subdomains"], router)
        self.assertIn("https://cdn.docs.example.com/x", reqs)
        self.assertIn("host+subdomains", log_txt)
        self.assertEqual(code, 0)

    def test_in_scope_redirect_followed_and_counted(self):
        router = {
            f"{BASE}/": page("home", ["/g"]),
            f"{BASE}/g": {"status": 302, "body": "", "headers": {"Location": "/real"}},
            f"{BASE}/real": page("real-body"),
        }
        code, log_txt, reqs = run_crawler(
            self.candidate, ["--url", f"{BASE}/", "--out", str(self.out),
                             "--max-pages", "10", "--delay", "0"], router)
        self.assertEqual(reqs, [f"{BASE}/", f"{BASE}/g", f"{BASE}/real"])
        self.assertIn("redirects=1", log_txt)
        self.assertIn("Body: real-body", self.files()["real.md"])
        self.assertEqual(code, 0)


# ------------------------------------------------------ candidate: budgets
class TestCandidateBudgets(CrawlerCase):

    def test_request_budget_counts_errors_and_no_text(self):
        links = [f"/e{i}" for i in range(3)] + [f"/n{i}" for i in range(3)]
        router = {f"{BASE}/": page("home", links)}
        for i in range(3):
            router[f"{BASE}/e{i}"] = {"status": 404, "body": "gone"}
            router[f"{BASE}/n{i}"] = page("<NO-TEXT> empty")
        code, log_txt, reqs = run_crawler(
            self.candidate, ["--url", f"{BASE}/", "--out", str(self.out),
                             "--max-pages", "50", "--max-requests", "5",
                             "--delay", "0"], router)
        self.assertEqual(len(reqs), 5)  # budget includes the failed/no-text GETs
        self.assertIn("[BUDGET]", log_txt)
        self.assertIn("requests=5/5", log_txt)
        self.assertEqual(code, 2)  # request budget truncates the queue

    def test_consecutive_errors_abort_with_exit2(self):
        links = [f"/e{i}" for i in range(6)] + ["/good"]
        router = {f"{BASE}/": page("home", links), f"{BASE}/good": page("never-reached")}
        for i in range(6):
            router[f"{BASE}/e{i}"] = {"status": 404, "body": "gone"}
        code, log_txt, reqs = run_crawler(
            self.candidate, ["--url", f"{BASE}/", "--out", str(self.out),
                             "--max-pages", "50", "--max-requests", "100",
                             "--max-consecutive-errors", "3", "--delay", "0"], router)
        self.assertEqual(code, 2)
        self.assertIn("[ABORT]", log_txt)
        self.assertEqual(len(reqs), 4)  # / + 3 failing hops, then stop

    def test_default_budget_is_3x_max_pages(self):
        router = {f"{BASE}/": page("home")}
        code, log_txt, reqs = run_crawler(
            self.candidate, ["--url", f"{BASE}/", "--out", str(self.out),
                             "--max-pages", "10", "--delay", "0"], router)
        self.assertIn("max-requests=30", log_txt)


# ------------------------------------------------------ candidate: clean run compat
class TestCandidateNormalRun(CrawlerCase):

    def test_clean_site_extracts_mirrors_and_exits0(self):
        router = {
            f"{BASE}/": page("home", ["/docs/intro", "/img.png",
                                      "https://partner.example/x",
                                      "/track?utm_source=ad&id=7"]),
            f"{BASE}/docs/intro": page("intro-page"),
            f"{BASE}/img.png": page("should-not-be-requested"),
            "https://partner.example/x": page("should-not-be-requested"),
            f"{BASE}/track?id=7": page("track-page"),  # crawler fetches the NORMALIZED url
        }
        code, log_txt, reqs = run_crawler(
            self.candidate, ["--url", f"{BASE}/", "--out", str(self.out),
                             "--max-pages", "10", "--delay", "0"], router)
        self.assertEqual(code, 0)
        files = self.files()
        self.assertIn("index.md", files)
        self.assertIn("intro.md", files)
        intro = next(p for p in self.out.rglob("intro.md"))
        self.assertEqual(intro.parent.name, "docs")  # structure mirrored
        self.assertIn("<!-- source: https://docs.example.com/docs/intro -->",
                      files["intro.md"])
        self.assertIn("track__id_7.md", files)  # tracking stripped, real query kept
        self.assert_no_host(reqs, "partner.example")
        self.assertNotIn(f"{BASE}/img.png", reqs)

    def test_error_records_exit1(self):
        router = {
            f"{BASE}/": page("home", ["/missing"]),
            f"{BASE}/missing": {"status": 404, "body": "gone"},
        }
        code, log_txt, reqs = run_crawler(
            self.candidate, ["--url", f"{BASE}/", "--out", str(self.out),
                             "--max-pages", "10", "--delay", "0"], router)
        self.assertEqual(code, 1)
        self.assertIn("[ERR]", log_txt)


# ------------------------------------------------------ preimage gap evidence


class TestParentBoundaryCases(CrawlerCase):

    def test_small_default_budget_is_exact_and_exhaustion_is_partial(self):
        router = {f"{BASE}/": page("<NO-TEXT>", ["/a"]),
                  f"{BASE}/a": page("<NO-TEXT>", ["/b"]),
                  f"{BASE}/b": page("<NO-TEXT>", ["/c"])}
        code, text, reqs = run_crawler(self.candidate,
            ["--url", BASE+"/", "--out", str(self.out), "--max-pages", "1", "--delay", "0"], router)
        self.assertEqual(len(reqs), 3)
        self.assertIn("max-requests=3", text)
        self.assertEqual(code, 2)

    def test_redirect_budget_stops_before_next_request(self):
        router = {BASE+"/": {"status": 302, "headers": {"Location": "/x"}}, BASE+"/x": page("x")}
        code, text, reqs = run_crawler(self.candidate,
            ["--url", BASE+"/", "--out", str(self.out), "--max-requests", "1", "--delay", "0"], router)
        self.assertEqual(reqs, [BASE+"/"])
        self.assertEqual(code, 2)

    def test_non_http_redirect_is_not_requested(self):
        router = {BASE+"/": {"status": 302, "headers": {"Location": "ftp://docs.example.com/x"}}}
        code, text, reqs = run_crawler(self.candidate,
            ["--url", BASE+"/", "--out", str(self.out), "--delay", "0"], router)
        self.assertEqual(reqs, [BASE+"/"])
        self.assertIn("out of scope", text)

    def test_parent_segments_and_long_query_remain_inside_output(self):
        root = self.tmp/"safe"
        root.mkdir()
        path = self.candidate._url_to_file(BASE+"/../../escape/a?q="+"x"*400, root, {})
        self.assertTrue(path.resolve().is_relative_to(root.resolve()))
        self.assertLess(len(path.name), 160)

    def test_both_existing_paths_are_preserved(self):
        root = self.out/"docs.example.com"
        root.mkdir(parents=True)
        (root/"index.md").write_text("OLD", encoding="utf-8")
        digest = hashlib.sha1((BASE+"/").encode()).hexdigest()[:8]
        (root/f"index-{digest}.md").write_text("ALSO OLD", encoding="utf-8")
        code, text, reqs = run_crawler(self.candidate,
            ["--url", BASE+"/", "--out", str(self.out), "--delay", "0"], {BASE+"/": page("new")})
        self.assertEqual((root/"index.md").read_text(), "OLD")
        self.assertEqual((root/f"index-{digest}.md").read_text(), "ALSO OLD")
        self.assertIn("[CONFLICT]", text)

    def test_atomic_creation_does_not_overwrite_a_racing_file(self):
        original_map = self.candidate._url_to_file
        def racing_map(url, root, taken):
            target = original_map(url, root, taken)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("RACING WRITER", encoding="utf-8")
            return target
        with mock.patch.object(self.candidate, "_url_to_file", side_effect=racing_map):
            code, text, reqs = run_crawler(self.candidate,
                ["--url", BASE+"/", "--out", str(self.out), "--delay", "0"], {BASE+"/": page("new")})
        self.assertEqual(self.files()["index.md"], "RACING WRITER")
        self.assertEqual(code, 1)
        self.assertIn("[OUTPUT-ERR]", text)

    def test_invalid_request_budget_rejected_before_request(self):
        CTX["log"] = []
        with self.assertRaises(SystemExit) as exit_result, contextlib.redirect_stderr(io.StringIO()):
            run_crawler(self.candidate, ["--url", BASE+"/", "--out", str(self.out), "--max-requests", "-1"], {})
        self.assertEqual(exit_result.exception.code, 2)
        self.assertEqual(CTX["log"], [])
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
