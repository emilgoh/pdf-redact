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
