#!/usr/bin/env python3
"""Redact user-defined words, patterns, and regions from PDFs.

Produces a copy of the input named ``<original>_redacted.pdf`` in which every
occurrence of the given words/phrases is *truly* removed: the underlying text is
deleted from the page content (not merely hidden behind a black box) and the
area is painted over. Extracting text from the output will not reveal the
redacted words.

Beyond literal words, the tool can redact built-in patterns (SSNs, emails,
phone numbers, credit cards, IP addresses), custom regexes, fixed rectangular
regions, and fuzzy matches; it can scrub document metadata, strip annotations
and form fields, process whole directories, preview matches without writing
(``--dry-run``), and write a JSON audit log that never contains the redacted
content itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import string
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    sys.exit("PyMuPDF is required. Install it with:  pip install pymupdf")

__version__ = "1.0.0"

#: Built-in regex patterns usable via ``--pattern NAME``.
PATTERNS: dict[str, str] = {
    "ssn": r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}",
    "phone": r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)",
    "credit-card": r"(?<!\d)(?:\d{4}[ -]?){3}\d{4}(?!\d)",
    "ip": r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])",
}

_COLORS = {
    "black": (0.0, 0.0, 0.0),
    "white": (1.0, 1.0, 1.0),
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 0.6, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "gray": (0.5, 0.5, 0.5),
    "grey": (0.5, 0.5, 0.5),
    "yellow": (1.0, 1.0, 0.0),
}

# Pages with at least this many pages get a progress line on stderr.
_PROGRESS_THRESHOLD = 10


def parse_fill(value):
    """Parse a fill color: a name, ``#rrggbb`` hex, ``R,G,B`` (0-255), or ``none``.

    Returns an RGB tuple of floats in 0..1, or ``False`` for ``none`` (text is
    still removed, but no box is drawn).
    """
    value = value.strip().lower()
    if value == "none":
        return False
    if value in _COLORS:
        return _COLORS[value]
    if value.startswith("#"):
        hexpart = value[1:]
        if len(hexpart) == 3:
            hexpart = "".join(c * 2 for c in hexpart)
        if len(hexpart) == 6 and all(c in string.hexdigits for c in hexpart):
            return tuple(int(hexpart[i : i + 2], 16) / 255 for i in (0, 2, 4))
        raise ValueError(f"Invalid hex color: {value!r}")
    parts = value.split(",")
    if len(parts) == 3:
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            raise ValueError(f"Invalid fill color: {value!r}") from None
        if all(0 <= n <= 255 for n in nums):
            # Accept 0-1 floats as-is, otherwise scale from 0-255.
            if max(nums) > 1:
                nums = [n / 255 for n in nums]
            return tuple(nums)
    raise ValueError(
        f"Invalid fill color: {value!r} (use a name like 'black', '#rrggbb', 'R,G,B', or 'none')"
    )


def parse_area(spec: str):
    """Parse an ``--area`` spec: ``PAGE:X0,Y0,X1,Y1`` or ``PAGE:all``.

    PAGE is a 1-based page number or ``all``. Coordinates are PDF points from
    the top-left of the page. Returns ``(page_index_or_None, rect_or_None)``
    where ``None`` means "all pages" / "whole page".
    """
    page_part, sep, rect_part = spec.partition(":")
    if not sep or not page_part or not rect_part:
        raise ValueError(f"Invalid area {spec!r} (expected PAGE:X0,Y0,X1,Y1 or PAGE:all)")
    if page_part.lower() == "all":
        page = None
    else:
        try:
            page = int(page_part) - 1
        except ValueError:
            raise ValueError(f"Invalid page number in area {spec!r}") from None
        if page < 0:
            raise ValueError(f"Page numbers are 1-based; got {page_part!r}")
    if rect_part.lower() == "all":
        rect = None
    else:
        try:
            coords = tuple(float(v) for v in rect_part.split(","))
        except ValueError:
            raise ValueError(f"Invalid coordinates in area {spec!r}") from None
        if len(coords) != 4:
            raise ValueError(f"Area needs 4 coordinates (X0,Y0,X1,Y1); got {rect_part!r}")
        rect = coords
    return page, rect


def parse_table(spec: str):
    """Parse a ``--table`` spec: ``PAGE`` or ``PAGE:N`` (both 1-based), or ``all``.

    Redacts entire detected tables: every table on the page, or only the N-th
    one. Returns ``(page_index_or_None, table_index_or_None)`` where ``None``
    means "all pages" / "every table".
    """
    page_part, sep, index_part = spec.partition(":")
    if page_part.lower() == "all":
        page = None
    else:
        try:
            page = int(page_part) - 1
        except ValueError:
            raise ValueError(f"Invalid table spec {spec!r} (expected PAGE, PAGE:N, or all)") from None
        if page < 0:
            raise ValueError(f"Page numbers are 1-based; got {page_part!r}")
    index = None
    if sep:
        try:
            index = int(index_part) - 1
        except ValueError:
            raise ValueError(f"Invalid table number in {spec!r}") from None
        if index < 0:
            raise ValueError(f"Table numbers are 1-based; got {index_part!r}")
    return page, index


def load_wordlist(path: Path) -> list[str]:
    """Load redaction terms from a text file: one per line, ``#`` comments allowed."""
    if not path.is_file():
        raise FileNotFoundError(f"Word list not found: {path}")
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def _digest(term: str) -> str:
    """Short SHA-256 of a term, so audit logs never contain the term itself."""
    return hashlib.sha256(term.encode("utf-8")).hexdigest()[:16]


@dataclass
class Match:
    """One redacted region."""

    page: int  # 1-based page number
    rect: tuple  # (x0, y0, x1, y1)
    kind: str  # "word" | "fuzzy" | "pattern:<name>" | "regex" | "area" | "table"
    label: str  # human-readable; may contain the term (shown in --dry-run only)
    digest: "str | None"  # sha-256 prefix of the term/pattern; None for areas


@dataclass
class RedactionResult:
    input_path: Path
    output_path: "Path | None"  # None when dry_run
    matches: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def pages(self) -> list:
        return sorted({m.page for m in self.matches})


def _ocr_textpage(page):
    """OCR a page image via Tesseract; friendly error if Tesseract is missing."""
    try:
        return page.get_textpage_ocr(full=True, dpi=300)
    except Exception as exc:  # MuPDF raises generic RuntimeErrors here
        raise RuntimeError(
            "OCR failed — is Tesseract installed? "
            "(macOS: brew install tesseract; Debian/Ubuntu: apt install tesseract-ocr). "
            f"Original error: {exc}"
        ) from None


def _strip_annotations(page) -> None:
    """Delete all annotations (comments, highlights, ...) and form fields."""
    while True:
        annot = page.first_annot
        if not annot:
            break
        page.delete_annot(annot)
    for widget in list(page.widgets() or []):
        page.delete_widget(widget)


def _table_matches(page, tables, hits: set) -> list:
    """Detect tables on this page and match them against ``--table`` specs.

    ``tables`` is [(page_index_or_None, table_index_or_None), ...]; specs that
    matched at least one table anywhere are added to ``hits`` (for warnings).
    """
    relevant = [spec for spec in tables if spec[0] is None or spec[0] == page.number]
    if not relevant:
        return []
    found = page.find_tables().tables
    pageno = page.number + 1
    matches = []
    seen = set()
    for spec in relevant:
        _, want_index = spec
        for i, tab in enumerate(found):
            if want_index is not None and want_index != i:
                continue
            hits.add(spec)
            if i in seen:  # same table requested by overlapping specs
                continue
            seen.add(i)
            rect = fitz.Rect(tab.bbox)
            label = f"table {i + 1} ({tab.row_count} rows x {tab.col_count} cols)"
            matches.append(Match(pageno, tuple(rect), "table", label, None))
    return matches


def _page_matches(page, words, regex_specs, areas, fuzzy, ocr) -> list:
    """Collect every match on one page. ``regex_specs`` is [(kind, pattern), ...]."""
    matches = []
    pageno = page.number + 1

    textpage = None
    if ocr and not page.get_text().strip():
        textpage = _ocr_textpage(page)

    for word in words:
        for rect in page.search_for(word, textpage=textpage):
            matches.append(Match(pageno, tuple(rect), "word", word, _digest(word.lower())))

    if regex_specs:
        text = page.get_text(textpage=textpage)
        for kind, pattern in regex_specs:
            hits = {m.group(0) for m in re.finditer(pattern, text, re.IGNORECASE)}
            for hit in hits:
                for rect in page.search_for(hit, textpage=textpage):
                    matches.append(Match(pageno, tuple(rect), kind, hit, _digest(hit.lower())))

    if fuzzy:
        single_words = [w for w in words if " " not in w]
        for x0, y0, x1, y1, token, *_ in page.get_text("words", textpage=textpage):
            norm = token.strip(string.punctuation).casefold()
            if not norm:
                continue
            for word in single_words:
                target = word.casefold()
                if norm == target:  # exact hits are already covered by search_for
                    continue
                if SequenceMatcher(None, norm, target).ratio() >= fuzzy:
                    matches.append(
                        Match(pageno, (x0, y0, x1, y1), "fuzzy",
                              f"{token!r} ~ {word!r}", _digest(word.lower()))
                    )
                    break

    for area_page, area_rect in areas:
        if area_page is None or area_page == page.number:
            rect = fitz.Rect(area_rect) if area_rect else page.rect
            matches.append(Match(pageno, tuple(rect), "area", f"area {tuple(rect)}", None))

    return matches


def redact_pdf(
    input_path,
    words=(),
    output_path=None,
    *,
    fill=(0, 0, 0),
    patterns=(),
    regexes=(),
    areas=(),
    tables=(),
    fuzzy=None,
    ocr=False,
    scrub_metadata=False,
    strip_annotations=False,
    dry_run=False,
    quiet=False,
) -> RedactionResult:
    """Redact ``input_path`` and return a :class:`RedactionResult`.

    Word/phrase matching is case-insensitive (a redaction tool should catch
    every case variant of a sensitive term). The input file is never modified —
    a copy is produced (default: ``<name>_redacted.pdf`` next to the input).

    ``patterns`` are names from :data:`PATTERNS`; ``regexes`` are custom Python
    regular expressions; ``areas`` are ``(page_index_or_None, rect_or_None)``
    pairs as returned by :func:`parse_area`; ``tables`` are
    ``(page_index_or_None, table_index_or_None)`` pairs as returned by
    :func:`parse_table` — each detected table is redacted whole (its full
    bounding box, borders included); ``fuzzy`` is a 0-1 similarity
    threshold applied to single-word terms. With ``dry_run`` nothing is written.
    """
    input_path = Path(input_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    words = [w for w in words if w and w.strip()]
    unknown = sorted(set(patterns) - set(PATTERNS))
    if unknown:
        raise ValueError(f"Unknown pattern(s): {', '.join(unknown)}. Available: {', '.join(PATTERNS)}")
    regex_specs = [(f"pattern:{name}", PATTERNS[name]) for name in patterns]
    for rx in regexes:
        try:
            re.compile(rx)
        except re.error as exc:
            raise ValueError(f"Invalid regex {rx!r}: {exc}") from None
        regex_specs.append(("regex", rx))
    if fuzzy is not None and not 0 < fuzzy <= 1:
        raise ValueError("--fuzzy threshold must be in (0, 1]")
    if not (words or regex_specs or areas or tables):
        raise ValueError(
            "Nothing to redact: give words, --wordlist, --pattern, --regex, --area, or --table."
        )

    if output_path is None:
        output_path = input_path.with_name(f"{input_path.stem}_redacted{input_path.suffix}")
    output_path = Path(output_path)

    doc = fitz.open(input_path)
    all_matches = []
    warnings = []
    table_hits = set()
    try:
        page_count = doc.page_count
        show_progress = not quiet and page_count >= _PROGRESS_THRESHOLD
        for page in doc:
            if show_progress:
                print(f"\r  scanning page {page.number + 1}/{page_count} ...",
                      end="", file=sys.stderr, flush=True)
            if strip_annotations and not dry_run:
                _strip_annotations(page)
            matches = _page_matches(page, words, regex_specs, areas, fuzzy, ocr)
            if tables:
                matches.extend(_table_matches(page, tables, table_hits))
            all_matches.extend(matches)
            if matches and not dry_run:
                for m in matches:
                    page.add_redact_annot(fitz.Rect(m.rect), fill=fill)
                # Removes underlying text/image pixels in the marked areas,
                # then draws the fill.
                page.apply_redactions()
        if show_progress:
            print("\r" + " " * 40 + "\r", end="", file=sys.stderr, flush=True)

        for spec in tables:
            if spec not in table_hits:
                t_page, t_index = spec
                where = "any page" if t_page is None else f"page {t_page + 1}"
                which = "table" if t_index is None else f"table #{t_index + 1}"
                warnings.append(f"no {which} detected on {where}")

        if not dry_run:
            if scrub_metadata:
                doc.set_metadata({})
                doc.del_xml_metadata()
            doc.save(output_path, garbage=4, deflate=True)
    finally:
        doc.close()

    return RedactionResult(input_path, None if dry_run else output_path, all_matches, warnings)


def _print_result(result: RedactionResult, dry_run: bool) -> None:
    n = len(result.matches)
    if dry_run:
        print(f"{result.input_path}: {n} match(es) (dry run, nothing written)")
        for warning in result.warnings:
            print(f"  Warning: {warning}")
        for m in result.matches:
            label = m.label if len(m.label) <= 48 else m.label[:45] + "..."
            rect = ", ".join(f"{v:.1f}" for v in m.rect)
            print(f"  page {m.page:>3}  {m.kind:<16} {label:<48} ({rect})")
        return
    pages = len(result.pages)
    print(f"{result.input_path}: redacted {n} occurrence(s) on {pages} page(s).")
    for warning in result.warnings:
        print(f"  Warning: {warning}")
    if n == 0:
        print("  Warning: no matches found — the output is an unmodified copy.")
    print(f"  Wrote: {result.output_path}")


def _write_log(log_path: Path, results) -> None:
    """Write a JSON audit log. Terms are recorded only as SHA-256 digests."""
    payload = {
        "tool": f"pdf-redact {__version__}",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": [
            {
                "input": str(r.input_path),
                "output": str(r.output_path) if r.output_path else None,
                "total_matches": len(r.matches),
                "matches": [
                    {
                        "page": m.page,
                        "rect": [round(v, 2) for v in m.rect],
                        "kind": m.kind,
                        "term_sha256": m.digest,
                    }
                    for m in r.matches
                ],
            }
            for r in results
        ],
    }
    log_path = Path(log_path)
    log_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _resolve_inputs(input_path: Path, output: "Path | None"):
    """Yield (input, output) pairs; supports a single PDF or a directory of PDFs."""
    if input_path.is_dir():
        files = sorted(
            p for p in input_path.glob("*.pdf") if not p.stem.endswith("_redacted")
        )
        if not files:
            raise FileNotFoundError(f"No PDF files found in directory: {input_path}")
        if output is not None:
            if output.exists() and not output.is_dir():
                raise ValueError(f"With a directory input, -o must be a directory: {output}")
            output.mkdir(parents=True, exist_ok=True)
            return [(f, output / f"{f.stem}_redacted{f.suffix}") for f in files]
        return [(f, None) for f in files]
    return [(input_path, output)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="pdf-redact",
        description="Redact words, patterns, and regions from a PDF "
                    "(true removal, not just black boxes).",
        epilog="Built-in patterns: " + ", ".join(PATTERNS),
    )
    parser.add_argument("input", type=Path,
                        help="Input PDF file, or a directory to redact every PDF in it.")
    parser.add_argument("words", nargs="*",
                        help="Words/phrases to redact. Quote phrases containing spaces.")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output path (default: <input>_redacted.pdf next to the input). "
                             "With a directory input, an output directory.")
    parser.add_argument("-w", "--wordlist", type=Path, action="append", default=[],
                        metavar="FILE",
                        help="Load terms from a text file (one per line, # comments). Repeatable.")
    parser.add_argument("-p", "--pattern", action="append", default=[],
                        choices=sorted(PATTERNS), metavar="NAME",
                        help=f"Redact a built-in pattern ({', '.join(PATTERNS)}). Repeatable.")
    parser.add_argument("-r", "--regex", action="append", default=[], metavar="REGEX",
                        help="Redact matches of a custom regex (case-insensitive). Repeatable.")
    parser.add_argument("--area", action="append", default=[], metavar="PAGE:X0,Y0,X1,Y1",
                        help="Redact a fixed rectangle (points, origin top-left). "
                             "PAGE is 1-based or 'all'; the rect may be 'all' for the whole page. "
                             "Repeatable.")
    parser.add_argument("--table", action="append", default=[], metavar="PAGE[:N]",
                        help="Detect tables on a page and redact them whole (borders included). "
                             "PAGE is 1-based or 'all'; add :N to pick only the N-th table on "
                             "the page. Repeatable.")
    parser.add_argument("--fuzzy", type=float, default=None, metavar="RATIO",
                        help="Also redact near-matches of single-word terms at this similarity "
                             "ratio (0-1, e.g. 0.8) — catches typos and OCR errors.")
    parser.add_argument("--fill", default="black", metavar="COLOR",
                        help="Redaction box color: name, #rrggbb, R,G,B (0-255), or 'none' "
                             "(default: black).")
    parser.add_argument("--ocr", action="store_true",
                        help="OCR pages that have no extractable text (scanned PDFs); "
                             "requires Tesseract.")
    parser.add_argument("--scrub-metadata", action="store_true",
                        help="Also remove document metadata (author, title, creation tool, XMP).")
    parser.add_argument("--strip-annotations", action="store_true",
                        help="Also delete all annotations (comments, highlights) and form fields.")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Report matches and their locations without writing any file.")
    parser.add_argument("--log", type=Path, default=None, metavar="FILE",
                        help="Write a JSON audit log (pages/locations only — never the terms).")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress per-file output.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    try:
        fill = parse_fill(args.fill)
        areas = [parse_area(spec) for spec in args.area]
        tables = [parse_table(spec) for spec in args.table]
        words = list(args.words)
        for wl in args.wordlist:
            words.extend(load_wordlist(wl))

        results = []
        for in_file, out_file in _resolve_inputs(args.input, args.output):
            result = redact_pdf(
                in_file,
                words,
                out_file,
                fill=fill,
                patterns=args.pattern,
                regexes=args.regex,
                areas=areas,
                tables=tables,
                fuzzy=args.fuzzy,
                ocr=args.ocr,
                scrub_metadata=args.scrub_metadata,
                strip_annotations=args.strip_annotations,
                dry_run=args.dry_run,
                quiet=args.quiet,
            )
            results.append(result)
            if not args.quiet:
                _print_result(result, args.dry_run)

        if args.log:
            _write_log(args.log, results)
            if not args.quiet:
                print(f"Audit log written to {args.log}")
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        sys.exit(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
