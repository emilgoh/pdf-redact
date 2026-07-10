#!/usr/bin/env python3
"""Local web GUI for pdf-redact, for non-technical users.

Run with ``python pdf_redact_gui.py`` (or ``pdf-redact-gui`` if installed via
pip). It starts a small web server on 127.0.0.1 (localhost only) and opens
your browser: drag a PDF onto the page, type the terms to remove (one per
line), tick any built-in patterns, and click Redact. The redacted copy is
downloaded by the browser.

Everything runs locally — the PDF never leaves your machine. Uses the same
true-removal engine as the CLI.

(This replaced an earlier Tkinter GUI: Apple's system Python ships the ancient
Tcl/Tk 8.5, which renders blank windows on modern macOS. A browser front-end
has no such dependency.)
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pdf_redact import PATTERNS, __version__, parse_fill, parse_table, redact_pdf

_MAX_UPLOAD = 200 * 1024 * 1024  # 200 MB

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pdf-redact</title>
<style>
  body { font: 15px/1.5 -apple-system, system-ui, sans-serif; margin: 0 auto;
         max-width: 620px; padding: 24px; color: #222; }
  h1 { font-size: 22px; } h1 small { color: #888; font-weight: normal; }
  #drop { border: 2px dashed #bbb; border-radius: 8px; padding: 28px;
          text-align: center; color: #666; cursor: pointer; margin: 12px 0; }
  #drop.armed { border-color: #2a7; color: #2a7; }
  textarea { width: 100%; box-sizing: border-box; height: 96px; font: inherit; }
  fieldset { border: 1px solid #ddd; border-radius: 6px; margin: 12px 0; }
  label.inline { margin-right: 14px; white-space: nowrap; }
  button { font: inherit; padding: 8px 22px; border-radius: 6px; border: none;
           background: #222; color: #fff; cursor: pointer; }
  button:disabled { background: #999; }
  #status { margin-top: 12px; white-space: pre-wrap; }
  #status.err { color: #b00; }
  pre { background: #f6f6f6; padding: 10px; border-radius: 6px; overflow-x: auto; }
</style>
</head>
<body>
<h1>pdf-redact <small>v__VERSION__</small></h1>
<p>Truly removes matched text — it cannot be extracted from the output.
Everything runs locally; the file never leaves your machine.</p>

<div id="drop">Drop a PDF here, or click to choose a file
  <input type="file" id="file" accept=".pdf,application/pdf" hidden></div>

<label for="terms">Words / phrases to redact (one per line, case-insensitive)</label>
<textarea id="terms" placeholder="John Smith&#10;Confidential"></textarea>

<fieldset><legend>Built-in patterns</legend>__CHECKBOXES__</fieldset>

<fieldset><legend>Tables</legend>
  <label>Black out entire detected tables on pages:
    <input id="tables" size="16" placeholder="e.g. 2,5 — or all"></label>
</fieldset>

<fieldset><legend>Options</legend>
  <label class="inline"><input type="checkbox" id="scrub" checked> Scrub metadata</label>
  <label class="inline">Fill:
    <select id="fill">
      <option>black</option><option>white</option><option>gray</option>
      <option>red</option><option value="none">none (no box)</option>
    </select></label>
  <label class="inline"><input type="checkbox" id="preview"> Preview only (dry run)</label>
</fieldset>

<button id="go">Redact</button>
<div id="status"></div>
<pre id="matches" hidden></pre>

<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const status = document.getElementById('status');
const matches = document.getElementById('matches');

function setFile(f) {
  if (!f) return;
  fileInput.dataset.name = f.name;
  drop.textContent = f.name;
  drop.classList.add('armed');
  drop.file = f;
}
drop.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => setFile(fileInput.files[0]));
drop.addEventListener('dragover', e => { e.preventDefault(); });
drop.addEventListener('drop', e => { e.preventDefault(); setFile(e.dataTransfer.files[0]); });

document.getElementById('go').addEventListener('click', async () => {
  const file = drop.file;
  const terms = document.getElementById('terms').value;
  const patterns = [...document.querySelectorAll('input[name=pattern]:checked')]
                   .map(cb => cb.value).join(',');
  const tables = document.getElementById('tables').value.trim();
  status.className = ''; matches.hidden = true;
  if (!file) { status.className = 'err'; status.textContent = 'Choose a PDF first.'; return; }
  if (!terms.trim() && !patterns && !tables) {
    status.className = 'err';
    status.textContent = 'Enter at least one term, tick a pattern, or give table pages.'; return;
  }
  const preview = document.getElementById('preview').checked;
  const params = new URLSearchParams({
    terms, patterns, tables,
    fill: document.getElementById('fill').value,
    scrub: document.getElementById('scrub').checked ? '1' : '0',
    preview: preview ? '1' : '0',
    filename: file.name,
  });
  const btn = document.getElementById('go');
  btn.disabled = true; status.textContent = 'Working ...';
  try {
    const resp = await fetch('/redact?' + params, { method: 'POST', body: file });
    if (!resp.ok) {
      status.className = 'err'; status.textContent = await resp.text(); return;
    }
    const summary = decodeURIComponent(resp.headers.get('X-Redact-Summary') || '');
    if (preview) {
      matches.textContent = await resp.text(); matches.hidden = false;
      status.textContent = summary + ' (dry run — nothing written)';
    } else {
      const blob = await resp.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = file.name.replace(/\\.pdf$/i, '') + '_redacted.pdf';
      a.click();
      URL.revokeObjectURL(a.href);
      status.textContent = summary + ' Downloaded ' + a.download + '.';
    }
  } catch (err) {
    status.className = 'err'; status.textContent = 'Request failed: ' + err;
  } finally {
    btn.disabled = false;
  }
});
</script>
</body>
</html>
"""


def _page() -> bytes:
    checkboxes = "".join(
        f'<label class="inline"><input type="checkbox" name="pattern" value="{name}"> {name}</label>'
        for name in PATTERNS
    )
    html = _PAGE.replace("__VERSION__", __version__).replace("__CHECKBOXES__", checkboxes)
    return html.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = f"pdf-redact/{__version__}"

    def _send(self, code: int, body: bytes, content_type: str, extra=()) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, code: int, message: str) -> None:
        self._send(code, message.encode("utf-8"), "text/plain; charset=utf-8")

    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path in ("/", "/index.html"):
            self._send(200, _page(), "text/html; charset=utf-8")
        else:
            self._fail(404, "Not found")

    def do_POST(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/redact":
            self._fail(404, "Not found")
            return
        query = urllib.parse.parse_qs(parsed.query)

        def qval(name, default=""):
            return query.get(name, [default])[0]

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._fail(400, "No file received.")
            return
        if length > _MAX_UPLOAD:
            self._fail(413, "File too large (limit 200 MB).")
            return
        data = self.rfile.read(length)

        words = [line.strip() for line in qval("terms").splitlines() if line.strip()]
        patterns = [p for p in qval("patterns").split(",") if p]
        dry_run = qval("preview") == "1"
        # Take only the basename so the client can't influence paths.
        filename = Path(qval("filename", "input.pdf")).name or "input.pdf"

        try:
            fill = parse_fill(qval("fill", "black"))
            tables = [parse_table(tok.strip())
                      for tok in qval("tables").split(",") if tok.strip()]
            with tempfile.TemporaryDirectory(prefix="pdf-redact-") as tmpdir:
                in_path = Path(tmpdir) / filename
                in_path.write_bytes(data)
                result = redact_pdf(
                    in_path,
                    words,
                    fill=fill,
                    patterns=patterns,
                    tables=tables,
                    scrub_metadata=qval("scrub") == "1",
                    dry_run=dry_run,
                    quiet=True,
                )
                out_bytes = b"" if dry_run else result.output_path.read_bytes()
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            self._fail(400, str(exc))
            return
        except Exception as exc:  # e.g. corrupt/non-PDF input
            self._fail(400, f"Could not process {filename!r}: {exc}")
            return

        summary = (f"Redacted {len(result.matches)} occurrence(s) "
                   f"on {len(result.pages)} page(s).")
        if result.warnings:
            summary += " Warning: " + "; ".join(result.warnings) + "."
        extra = [("X-Redact-Summary", urllib.parse.quote(summary))]
        if dry_run:
            lines = [
                f"page {m.page:>3}  {m.kind:<16} {m.label}"
                for m in result.matches
            ] or ["(no matches)"]
            self._send(200, "\n".join(lines).encode("utf-8"),
                       "text/plain; charset=utf-8", extra)
        else:
            extra.append(("Content-Disposition",
                          f'attachment; filename="{Path(filename).stem}_redacted.pdf"'))
            self._send(200, out_bytes, "application/pdf", extra)

    def log_message(self, fmt, *args):  # keep the console tidy
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="pdf-redact-gui",
        description="Browser-based GUI for pdf-redact (local only; nothing is uploaded).",
    )
    parser.add_argument("--port", type=int, default=0,
                        help="Port to listen on (default: pick a free one).")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open the browser automatically.")
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"pdf-redact GUI running at {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
