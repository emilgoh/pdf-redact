# pdf-redact

Free command-line tool to redact user-defined words from a PDF.

It **truly removes** the matched text — the underlying characters are deleted
from the page content and the area is painted over. Unlike drawing a black
rectangle, extracting text from the output will **not** reveal the redacted
words. The original file is never modified; a copy named `<name>_redacted.pdf`
is written.

## Install

Run the setup script — it creates a `.venv` and installs dependencies:

```bash
./setup.sh                 # create .venv and install
source ./setup.sh          # same, and leave the venv activated in your shell
```

Or do it manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Try it (demo)

A sample PDF with fake sensitive data is included at `samples/sample.pdf`.
Run the demo to redact it and see the proof that the words are truly removed:

```bash
./setup.sh      # first time only: create .venv + install deps
./demo.sh
```

`demo.sh` prints the text before and after redaction, then verifies that terms
like `John Smith`, `SSN`, and the SSN/email values are no longer extractable
from the output (`samples/sample_redacted.pdf`). Open that file to see the black
redaction boxes.

To regenerate the sample PDF itself: `python samples/make_sample.py`.

## Usage

```bash
python pdf_redact.py <input.pdf> <word> [more words ...] [-o OUTPUT]
```

Examples:

```bash
# Redact two terms; writes report_redacted.pdf next to the input
python pdf_redact.py report.pdf "John Smith" "SSN"

# Custom output path
python pdf_redact.py report.pdf "Confidential" -o clean.pdf
```

- Multiple words/phrases can be given at once. Quote any phrase containing spaces.
- Matching is **case-insensitive**, so every case variant of a term is caught.

## How it works

1. Opens the input PDF (the input is opened read-only; a copy is saved).
2. Searches each page for every requested word/phrase.
3. Marks each hit with a redaction annotation and calls `apply_redactions()`,
   which removes the underlying text/graphics and fills the area with black.
4. Saves the result as `<name>_redacted.pdf` (or your `-o` path).

## Limitations

- Works on **digital (text-based)** PDFs. Scanned/image-only PDFs contain no
  searchable text, so there is nothing to match — those would need OCR first.
- Matches contiguous text as laid out on the page; a term split across lines
  may not be found as a single phrase.

## TODO

Ideas for future improvements, roughly in order of usefulness:

- **OCR support for scanned/image PDFs** — run OCR (e.g. Tesseract) to locate
  text in scanned pages, then redact the corresponding image regions.
- **Regex / pattern-based redaction** — built-in patterns for SSNs, emails,
  phone numbers, credit card numbers, IP addresses, etc., not just literal
  word matches.
- **Redact images/photos** — support blacking out arbitrary rectangular
  regions (e.g. faces, signatures, logos) selected by coordinates or
  interactively, not just matched text.
- **Metadata scrubbing** — strip document metadata (author, title, creation
  tool, GPS/EXIF in embedded images) that can leak identity even after text
  redaction.
- **Batch/directory mode** — redact all PDFs in a folder in one invocation.
- **Config file for word lists** — load redaction terms from a `.txt`/YAML
  file instead of passing them all on the command line.
- **Whole-page / annotation-aware redaction** — also strip comments,
  highlights, form field values, and hidden layers (OCR text layer,
  bookmarks) that might reference sensitive content.
- **Dry-run / preview mode** — report matches and their locations without
  writing an output file, so users can verify before committing to a redact.
- **Custom fill color / redaction style** — currently hardcoded to black;
  allow custom colors or a "blur" style.
- **Progress output for large PDFs** — page-by-page progress bar for
  multi-hundred-page documents.
- **Unit/integration test suite** — automated tests (pytest) beyond the
  manual `demo.sh` script, including edge cases like text split across
  lines/columns.
- **Packaging** — publish as a pip-installable package (`pipx install
  pdf-redact`) with a proper console-script entry point instead of requiring
  a cloned repo + venv.
- **GUI or web front-end** — a simple drag-and-drop interface for
  non-technical users.
- **Fuzzy/partial matching** — optional Levenshtein-distance matching to
  catch typos or OCR errors in the target terms.
- **Undo/audit log** — record what was redacted (terms + page/location)
  without leaking the actual redacted content, for compliance audit trails.
