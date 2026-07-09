#!/usr/bin/env python3
"""Redact user-defined words from a PDF.

Produces a copy of the input named ``<original>_redacted.pdf`` in which every
occurrence of the given words/phrases is *truly* removed: the underlying text is
deleted from the page content (not merely hidden behind a black box) and the
area is painted over. Extracting text from the output will not reveal the
redacted words.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    sys.exit("PyMuPDF is required. Install it with:  pip install pymupdf")


def redact_pdf(
    input_path: Path,
    words: list[str],
    output_path: Path | None = None,
    *,
    fill=(0, 0, 0),
) -> Path:
    """Redact every occurrence of ``words`` in ``input_path``.

    Matching is case-insensitive (a redaction tool should catch every case
    variant of a sensitive term). Returns the path to the written redacted PDF.
    The input file is never modified — a copy is produced.
    """
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    words = [w for w in words if w.strip()]
    if not words:
        raise ValueError("No words provided to redact.")

    if output_path is None:
        output_path = input_path.with_name(f"{input_path.stem}_redacted{input_path.suffix}")

    doc = fitz.open(input_path)
    total_hits = 0
    try:
        for page in doc:
            page_hits = 0
            for word in words:
                for rect in page.search_for(word):
                    page.add_redact_annot(rect, fill=fill)
                    page_hits += 1
            if page_hits:
                # Removes underlying text/images in the marked areas, then draws the fill.
                page.apply_redactions()
                total_hits += page_hits

        doc.save(output_path, garbage=4, deflate=True)
    finally:
        doc.close()

    print(f"Redacted {total_hits} occurrence(s) across {len(words)} term(s).")
    print(f"Wrote: {output_path}")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Redact user-defined words from a PDF (true removal, not just black boxes)."
    )
    parser.add_argument("input", type=Path, help="Path to the input PDF file.")
    parser.add_argument(
        "words",
        nargs="+",
        help="One or more words/phrases to redact. Quote phrases containing spaces.",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output path (default: <input>_redacted.pdf next to the input).",
    )
    args = parser.parse_args(argv)

    try:
        redact_pdf(args.input, args.words, args.output)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
