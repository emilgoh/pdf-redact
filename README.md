# pdf-redact

Free command-line tool to redact user-defined words, patterns, and regions
from PDFs.

It **truly removes** the matched text — the underlying characters are deleted
from the page content and the area is painted over. Unlike drawing a black
rectangle, extracting text from the output will **not** reveal the redacted
words. The original file is never modified; a copy named `<name>_redacted.pdf`
is written.

Features:

- Literal **words/phrases** (case-insensitive) and word lists from a file
- Built-in **patterns**: SSNs, emails, phone numbers, credit cards, IP addresses
- Custom **regexes** and **fuzzy matching** for typos / OCR errors
- Fixed **rectangular regions** (e.g. photos, signatures) and whole pages
- **OCR** for scanned/image-only PDFs (via Tesseract)
- **Metadata scrubbing** and **annotation/form-field stripping**
- **Batch mode** (a whole directory at once), **dry-run preview**,
  JSON **audit log**, custom fill colors, progress output, and a simple **GUI**

## Install

Run the setup script — it creates a `.venv` and installs dependencies:

```bash
./setup.sh                 # create .venv and install
source ./setup.sh          # same, and leave the venv activated in your shell
```

Or install it as a package with a `pdf-redact` console command:

```bash
pip install .              # or:  pip install -e ".[dev]"  for development
```

## Try it (demo)

A sample PDF with fake sensitive data is included at `samples/sample.pdf`.
Run the demo to redact it and see the proof that the words are truly removed:

```bash
./setup.sh      # first time only: create .venv + install deps
./demo.sh
```

`demo.sh` prints the text before and after redaction, then verifies that terms
like `John Smith`, `SSN`, the SSN/email values, and the phone number (caught by
the built-in `phone` pattern, not listed literally) are no longer extractable
from the output (`samples/sample_redacted.pdf`). Open that file to see the
black redaction boxes.

To regenerate the sample PDF itself: `python samples/make_sample.py`.

## Usage

```bash
python pdf_redact.py <input.pdf | directory> [words ...] [options]
```

Examples:

```bash
# Redact two terms; writes report_redacted.pdf next to the input
python pdf_redact.py report.pdf "John Smith" "SSN"

# Built-in patterns: catch every SSN, email, and phone number
python pdf_redact.py report.pdf -p ssn -p email -p phone

# Custom regex (case-insensitive)
python pdf_redact.py report.pdf -r 'INV-\d{6}'

# Terms from a file (one per line, # comments), custom output path
python pdf_redact.py report.pdf -w terms.txt -o clean.pdf

# Preview matches without writing anything
python pdf_redact.py report.pdf "Confidential" --dry-run

# Black out a photo region on page 1, and all of page 3
python pdf_redact.py report.pdf --area "1:72,100,300,250" --area "3:all"

# Scanned PDF: OCR the pages, then black out the matched image regions
python pdf_redact.py scan.pdf "John Smith" --ocr

# Everything in a folder, plus metadata scrub and an audit log
python pdf_redact.py ./invoices/ -p credit-card --scrub-metadata --log audit.json

# Catch typos/OCR errors: 'Jhon', 'J0hn', ... at 80% similarity
python pdf_redact.py report.pdf "John" --fuzzy 0.8
```

### Options

| Option | Description |
| --- | --- |
| `words ...` | Literal words/phrases (quote phrases with spaces). Case-insensitive. |
| `-o, --output PATH` | Output file (default `<input>_redacted.pdf`); a directory in batch mode. |
| `-w, --wordlist FILE` | Load terms from a text file (one per line, `#` comments). Repeatable. |
| `-p, --pattern NAME` | Built-in pattern: `ssn`, `email`, `phone`, `credit-card`, `ip`. Repeatable. |
| `-r, --regex REGEX` | Custom regex (case-insensitive). Repeatable. |
| `--area PAGE:X0,Y0,X1,Y1` | Redact a fixed rectangle (PDF points, origin top-left). `PAGE` is 1-based or `all`; the rect may be `all` for the whole page. Repeatable. |
| `--fuzzy RATIO` | Also redact near-matches of single-word terms (similarity 0–1, e.g. `0.8`). |
| `--fill COLOR` | Box color: name, `#rrggbb`, `R,G,B` (0–255), or `none` (default `black`). |
| `--ocr` | OCR pages with no extractable text (scanned PDFs). Requires [Tesseract](https://tesseract-ocr.github.io/) (`brew install tesseract`). |
| `--scrub-metadata` | Remove document metadata (author, title, creation tool, XMP). |
| `--strip-annotations` | Delete all annotations (comments, highlights) and form fields. |
| `-n, --dry-run` | Print matches and locations; write nothing. |
| `--log FILE` | JSON audit log of what was redacted (pages + locations; terms are stored only as SHA-256 digests, never in plain text). |
| `-q, --quiet` | Suppress per-file output. |

Documents with 10+ pages show a page-by-page progress line on stderr.

## GUI

For non-technical users there is a browser-based front-end:

```bash
python pdf_redact_gui.py     # or:  pdf-redact-gui  (if pip-installed)
```

It starts a small local web server (127.0.0.1 only — the PDF never leaves
your machine) and opens your browser. Drag a PDF onto the page, type the
terms (one per line), tick any built-in patterns, and click **Redact**; the
redacted copy is downloaded. A "Preview only" checkbox shows the matches
without writing anything. It uses the same true-removal engine as the CLI.

> Why a browser instead of a native window? Apple's system Python ships the
> ancient Tcl/Tk 8.5, which renders blank Tkinter windows on modern macOS —
> a browser front-end has no such dependency.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The suite covers word/pattern/regex/fuzzy/area redaction, batch mode, dry-run,
metadata scrubbing, annotation stripping, the audit log (including that it
never leaks the terms), and OCR (skipped automatically if Tesseract is not
installed).

## How it works

1. Opens the input PDF (the input is opened read-only; a copy is saved).
2. Searches each page for every requested word/phrase, pattern/regex match,
   fuzzy near-match, and fixed area. Pages without extractable text are OCRed
   first when `--ocr` is given.
3. Marks each hit with a redaction annotation and calls `apply_redactions()`,
   which removes the underlying text/graphics/image pixels and fills the area.
4. Optionally scrubs metadata and strips annotations/form fields, then saves
   the result as `<name>_redacted.pdf` (or your `-o` path).

## Limitations

- Matches contiguous text as laid out on the page; a term split across lines
  may not be found as a single phrase.
- **Tables:** text inside table cells is redacted like any other text, and
  cell borders/other cells are left intact. But matching is per cell — a
  phrase spanning multiple cells won't match, and redacting a *label* (e.g.
  `SSN`) does not redact the *value* in the adjacent cell. Target the values
  themselves (e.g. `-p ssn`) or use `--area` for whole rows/columns.
- `--scrub-metadata` clears document-level metadata (Info dictionary + XMP).
  EXIF data *inside* embedded images is not touched.
- `--ocr` requires Tesseract and only OCRs pages that have no extractable
  text; accuracy depends on scan quality (combine with `--fuzzy` to catch OCR
  misreads).
- Built-in patterns are pragmatic, not exhaustive — always check the output
  (use `--dry-run` first) before sharing a redacted document.

## Future ideas

- Publish to PyPI (`pipx install pdf-redact`) — packaging metadata is already
  in `pyproject.toml`.
- Interactive (click-and-drag) region selection instead of coordinates.
- Deep image scrubbing: EXIF/GPS removal inside embedded images.
- Cross-line/cross-column phrase matching.
