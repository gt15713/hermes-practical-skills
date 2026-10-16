"""Exercise the real requests/trafilatura path against a loopback-only HTML site."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "crawl_site.py"
PARAGRAPH = (
    "This example document explains how a small team maintains useful notes. "
    "Each record keeps the original observation separate from an interpretation. "
    "Sources are checked before a new explanation is added, and earlier records "
    "are preserved so that readers can understand how the conclusion developed. "
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/second"):
            self.send_error(404)
            return
        title = "First article" if self.path == "/" else "Second article"
        link = '<a href="/second">Second article</a>' if self.path == "/" else ""
        text = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f"<title>{title}</title></head><body><article><h1>{title}</h1>"
            + "".join(f"<p>{PARAGRAPH}Section {n}.</p>" for n in range(4))
            + link + "</article></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(text)))
        self.end_headers()
        self.wfile.write(text)

    def log_message(self, *_):
        pass


class RealDependencyIntegration(unittest.TestCase):
    def test_real_extraction_and_repeat_preserves_existing_files(self):
        with tempfile.TemporaryDirectory(prefix="site-crawler-integration-") as tmp:
            output = Path(tmp).resolve()
            self.assertTrue(output.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/"
            args = [sys.executable, str(SCRIPT), "--url", url, "--out", str(output),
                    "--max-pages", "2", "--max-requests", "6", "--delay", "0"]
            env = {**os.environ, "NO_PROXY": "127.0.0.1,localhost", "PYTHONUTF8": "1"}
            try:
                first = subprocess.run(args, capture_output=True, text=True,
                                       encoding="utf-8", timeout=30, env=env)
                self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                files = list(output.rglob("*.md"))
                self.assertEqual(len(files), 2, first.stdout)
                original = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
                for p in files:
                    text = p.read_text("utf-8")
                    self.assertIn("<!-- source: " + url, text)
                    self.assertIn("original observation separate from an interpretation", text)
                second = subprocess.run(args, capture_output=True, text=True,
                                        encoding="utf-8", timeout=30, env=env)
                self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
                self.assertEqual(len(list(output.rglob("*.md"))), 4, second.stdout)
                for p, digest in original.items():
                    self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), digest)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
