"""Test suite for pdf-redact (pytest).

Run from the repo root:  pip install -e ".[dev]" && pytest
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import fitz
import pytest

import pdf_redact as pr
import pdf_redact_gui as gui


# --------------------------------------------------------------------------- helpers

def make_pdf(path, lines, metadata=None, pages=1):
    """Write a simple text PDF for testing."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=12)
            y += 20
    if metadata:
        doc.set_metadata(metadata)
    doc.save(path)
    doc.close()
    return path


def text_of(path) -> str:
    with fitz.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


@pytest.fixture
def sample(tmp_path):
    return make_pdf(
        tmp_path / "sample.pdf",
        [
            "Patient name: John Smith",
            "SSN: 123-45-6789",
            "Email: john.smith@example.com",
            "Phone: (555) 123-4567",
            "Server: 192.168.1.42",
            "Card: 4111 1111 1111 1111",
            "Notes: contact JOHN SMITH or Jhon Smith.",
        ],
    )


# --------------------------------------------------------------------------- words

def test_word_truly_removed(sample):
    result = pr.redact_pdf(sample, ["John Smith"], quiet=True)
    out_text = text_of(result.output_path)
    assert "john smith" not in out_text.lower()
    assert len(result.matches) >= 2
    assert "Patient name" in out_text  # untargeted text survives


def test_matching_is_case_insensitive(sample):
    result = pr.redact_pdf(sample, ["john smith"], quiet=True)
    assert "JOHN SMITH" not in text_of(result.output_path)


def test_default_output_name_and_original_untouched(sample):
    before = sample.read_bytes()
    result = pr.redact_pdf(sample, ["SSN"], quiet=True)
    assert result.output_path == sample.with_name("sample_redacted.pdf")
    assert result.output_path.is_file()
    assert sample.read_bytes() == before


def test_no_terms_raises(sample):
    with pytest.raises(ValueError):
        pr.redact_pdf(sample, [], quiet=True)


def test_missing_input_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        pr.redact_pdf(tmp_path / "nope.pdf", ["x"], quiet=True)


# --------------------------------------------------------------------------- patterns / regex

def test_builtin_patterns(sample):
    result = pr.redact_pdf(
        sample, patterns=["ssn", "email", "phone", "ip", "credit-card"], quiet=True
    )
    out = text_of(result.output_path)
    for leaked in ("123-45-6789", "john.smith@example.com", "(555) 123-4567",
                   "192.168.1.42", "4111 1111 1111 1111"):
        assert leaked not in out
    kinds = {m.kind for m in result.matches}
    assert {"pattern:ssn", "pattern:email", "pattern:phone",
            "pattern:ip", "pattern:credit-card"} <= kinds


def test_unknown_pattern_raises(sample):
    with pytest.raises(ValueError, match="Unknown pattern"):
        pr.redact_pdf(sample, patterns=["nope"], quiet=True)


def test_custom_regex(tmp_path):
    pdf = make_pdf(tmp_path / "ids.pdf", ["Ref ID-1234 and ID-99 here."])
    result = pr.redact_pdf(pdf, regexes=[r"ID-\d+"], quiet=True)
    out = text_of(result.output_path)
    assert "ID-1234" not in out and "ID-99" not in out
    assert "Ref" in out


def test_invalid_regex_raises(sample):
    with pytest.raises(ValueError, match="Invalid regex"):
        pr.redact_pdf(sample, regexes=["("], quiet=True)


# --------------------------------------------------------------------------- fuzzy

def test_fuzzy_catches_typo(sample):
    result = pr.redact_pdf(sample, ["John"], fuzzy=0.7, quiet=True)
    out = text_of(result.output_path)
    assert "Jhon" not in out
    assert any(m.kind == "fuzzy" for m in result.matches)


def test_fuzzy_threshold_validated(sample):
    with pytest.raises(ValueError):
        pr.redact_pdf(sample, ["John"], fuzzy=1.5, quiet=True)


# --------------------------------------------------------------------------- areas

def test_area_whole_page(sample):
    result = pr.redact_pdf(sample, areas=[(0, None)], quiet=True)
    assert text_of(result.output_path).strip() == ""


def test_area_rect_only_hits_that_region(sample):
    # The first line sits around y=72; black out just that band.
    result = pr.redact_pdf(sample, areas=[(0, (0, 55, 612, 80))], quiet=True)
    out = text_of(result.output_path)
    assert "Patient name" not in out
    assert "123-45-6789" in out


def test_parse_area():
    assert pr.parse_area("2:10,20,30,40") == (1, (10.0, 20.0, 30.0, 40.0))
    assert pr.parse_area("all:all") == (None, None)
    assert pr.parse_area("1:all") == (0, None)
    for bad in ("3", "0:all", "1:1,2,3", "x:all", "1:a,b,c,d"):
        with pytest.raises(ValueError):
            pr.parse_area(bad)


# --------------------------------------------------------------------------- tables

def draw_table(page, rows, x0=72, y0=100, col_w=140, row_h=30):
    """Draw a bordered grid with cell text; returns the table bbox."""
    n_rows, n_cols = len(rows), len(rows[0])
    for i in range(n_rows + 1):
        page.draw_line((x0, y0 + i * row_h), (x0 + n_cols * col_w, y0 + i * row_h))
    for j in range(n_cols + 1):
        page.draw_line((x0 + j * col_w, y0), (x0 + j * col_w, y0 + n_rows * row_h))
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text((x0 + c * col_w + 6, y0 + r * row_h + 19), cell, fontsize=10)
    return fitz.Rect(x0, y0, x0 + n_cols * col_w, y0 + n_rows * row_h)


@pytest.fixture
def table_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Quarterly staff report", fontsize=14)
    draw_table(page, [["Name", "SSN"], ["John Smith", "123-45-6789"]])
    path = tmp_path / "table.pdf"
    doc.save(path)
    doc.close()
    return path


def test_parse_table():
    assert pr.parse_table("2") == (1, None)
    assert pr.parse_table("all") == (None, None)
    assert pr.parse_table("3:2") == (2, 1)
    assert pr.parse_table("all:1") == (None, 0)
    for bad in ("0", "x", "2:0", "2:x"):
        with pytest.raises(ValueError):
            pr.parse_table(bad)


def test_table_redacted_whole(table_pdf):
    result = pr.redact_pdf(table_pdf, tables=[(0, None)], quiet=True)
    out = text_of(result.output_path)
    for gone in ("Name", "SSN", "John Smith", "123-45-6789"):
        assert gone not in out
    assert "Quarterly staff report" in out  # text outside the table survives
    assert [m.kind for m in result.matches] == ["table"]
    assert result.warnings == []


def test_table_warning_when_none_found(sample):
    result = pr.redact_pdf(sample, tables=[(0, None)], quiet=True)
    assert result.matches == []
    assert any("no table detected on page 1" in w for w in result.warnings)


def test_table_index_selection(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    draw_table(page, [["Alpha", "One"]], y0=100)
    draw_table(page, [["Beta", "Two"]], y0=300)
    path = tmp_path / "two_tables.pdf"
    doc.save(path)
    doc.close()

    result = pr.redact_pdf(path, tables=[(0, 1)], quiet=True)  # second table only
    out = text_of(result.output_path)
    assert "Alpha" in out
    assert "Beta" not in out
    assert len(result.matches) == 1


def test_table_cli_and_dry_run(table_pdf, capsys):
    assert pr.main([str(table_pdf), "--table", "1", "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "table 1 (2 rows x 2 cols)" in printed
    assert pr.main([str(table_pdf), "--table", "1", "-q"]) == 0
    assert "John Smith" not in text_of(table_pdf.with_name("table_redacted.pdf"))


# --------------------------------------------------------------------------- fill

def test_parse_fill():
    assert pr.parse_fill("black") == (0.0, 0.0, 0.0)
    assert pr.parse_fill("white") == (1.0, 1.0, 1.0)
    assert pr.parse_fill("#ff0000") == (1.0, 0.0, 0.0)
    assert pr.parse_fill("255,0,0") == (1.0, 0.0, 0.0)
    assert pr.parse_fill("none") is False
    for bad in ("chartreuse-ish", "#12345", "1,2", "300,0,0"):
        with pytest.raises(ValueError):
            pr.parse_fill(bad)


def test_fill_none_still_removes_text(sample):
    result = pr.redact_pdf(sample, ["SSN"], fill=False, quiet=True)
    assert "SSN" not in text_of(result.output_path)


# --------------------------------------------------------------------------- metadata / annotations

def test_scrub_metadata(tmp_path):
    pdf = make_pdf(tmp_path / "meta.pdf", ["secret data"],
                   metadata={"author": "Jane Doe", "title": "Top Secret"})
    result = pr.redact_pdf(pdf, ["secret"], scrub_metadata=True, quiet=True)
    with fitz.open(result.output_path) as doc:
        assert not doc.metadata.get("author")
        assert not doc.metadata.get("title")


def test_metadata_kept_without_flag(tmp_path):
    pdf = make_pdf(tmp_path / "meta.pdf", ["secret data"], metadata={"author": "Jane Doe"})
    result = pr.redact_pdf(pdf, ["secret"], quiet=True)
    with fitz.open(result.output_path) as doc:
        assert doc.metadata.get("author") == "Jane Doe"


def test_strip_annotations(tmp_path):
    pdf_path = tmp_path / "annot.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "hello world")
    annot = page.add_highlight_annot(fitz.Rect(70, 60, 150, 80))
    annot.update()
    doc.save(pdf_path)
    doc.close()

    result = pr.redact_pdf(pdf_path, ["world"], strip_annotations=True, quiet=True)
    with fitz.open(result.output_path) as out:
        assert not list(out[0].annots())


# --------------------------------------------------------------------------- dry run

def test_dry_run_writes_nothing(sample, capsys):
    result = pr.redact_pdf(sample, ["John Smith"], dry_run=True, quiet=True)
    assert result.output_path is None
    assert not sample.with_name("sample_redacted.pdf").exists()
    assert len(result.matches) >= 2


# --------------------------------------------------------------------------- wordlist / CLI

def test_wordlist_cli(sample, tmp_path):
    wl = tmp_path / "terms.txt"
    wl.write_text("# comment line\nJohn Smith\n\nSSN\n")
    assert pr.main([str(sample), "-w", str(wl), "-q"]) == 0
    out = text_of(sample.with_name("sample_redacted.pdf"))
    assert "John Smith" not in out and "SSN" not in out


def test_cli_dry_run_prints_matches(sample, capsys):
    assert pr.main([str(sample), "John Smith", "--dry-run"]) == 0
    captured = capsys.readouterr().out
    assert "dry run" in captured and "John Smith" in captured
    assert not sample.with_name("sample_redacted.pdf").exists()


def test_cli_batch_directory(tmp_path):
    make_pdf(tmp_path / "a.pdf", ["alpha secret"])
    make_pdf(tmp_path / "b.pdf", ["beta secret"])
    assert pr.main([str(tmp_path), "secret", "-q"]) == 0
    for name in ("a", "b"):
        out = tmp_path / f"{name}_redacted.pdf"
        assert out.is_file()
        assert "secret" not in text_of(out)


def test_cli_batch_output_directory(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    make_pdf(src / "a.pdf", ["hush now"])
    outdir = tmp_path / "out"
    assert pr.main([str(src), "hush", "-o", str(outdir), "-q"]) == 0
    assert (outdir / "a_redacted.pdf").is_file()


def test_cli_batch_skips_previous_outputs(tmp_path):
    make_pdf(tmp_path / "a.pdf", ["seekrit"])
    make_pdf(tmp_path / "a_redacted.pdf", ["seekrit"])
    assert pr.main([str(tmp_path), "seekrit", "-q"]) == 0
    # only one new file was produced, and the old _redacted was not re-redacted
    assert not (tmp_path / "a_redacted_redacted.pdf").exists()


# --------------------------------------------------------------------------- audit log

def test_audit_log_records_without_leaking(sample, tmp_path):
    log = tmp_path / "audit.json"
    assert pr.main([str(sample), "John Smith", "-q", "--log", str(log)]) == 0
    raw = log.read_text()
    assert "John Smith" not in raw and "john smith" not in raw
    payload = json.loads(raw)
    matches = payload["files"][0]["matches"]
    assert payload["files"][0]["total_matches"] == len(matches) >= 2
    expected = hashlib.sha256(b"john smith").hexdigest()[:16]
    assert all(m["term_sha256"] == expected for m in matches)
    assert all(m["page"] == 1 and len(m["rect"]) == 4 for m in matches)


# --------------------------------------------------------------------------- web gui

@pytest.fixture
def gui_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_gui_serves_form(gui_server):
    with urllib.request.urlopen(gui_server + "/") as resp:
        body = resp.read().decode()
    assert resp.status == 200
    assert "pdf-redact" in body and "ssn" in body  # pattern checkboxes rendered


def test_gui_redact_roundtrip(gui_server, sample, tmp_path):
    params = urllib.parse.urlencode(
        {"terms": "John Smith", "patterns": "ssn,email", "fill": "black",
         "scrub": "1", "filename": "sample.pdf"}
    )
    req = urllib.request.Request(f"{gui_server}/redact?{params}",
                                 data=sample.read_bytes(), method="POST")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "application/pdf"
        assert "Redacted" in urllib.parse.unquote(resp.headers["X-Redact-Summary"])
        out = tmp_path / "gui_out.pdf"
        out.write_bytes(resp.read())
    text = text_of(out)
    assert "John Smith" not in text
    assert "123-45-6789" not in text
    assert "john.smith@example.com" not in text


def test_gui_table_redaction(gui_server, table_pdf, tmp_path):
    params = urllib.parse.urlencode({"tables": "1", "filename": "table.pdf"})
    req = urllib.request.Request(f"{gui_server}/redact?{params}",
                                 data=table_pdf.read_bytes(), method="POST")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        out = tmp_path / "gui_table_out.pdf"
        out.write_bytes(resp.read())
    text = text_of(out)
    assert "John Smith" not in text and "SSN" not in text
    assert "Quarterly staff report" in text


def test_gui_table_warning_in_summary(gui_server, sample):
    params = urllib.parse.urlencode(
        {"tables": "1", "preview": "1", "filename": "sample.pdf"}
    )
    req = urllib.request.Request(f"{gui_server}/redact?{params}",
                                 data=sample.read_bytes(), method="POST")
    with urllib.request.urlopen(req) as resp:
        summary = urllib.parse.unquote(resp.headers["X-Redact-Summary"])
    assert "no table detected" in summary


def test_gui_preview_mode(gui_server, sample):
    params = urllib.parse.urlencode(
        {"terms": "John Smith", "preview": "1", "filename": "sample.pdf"}
    )
    req = urllib.request.Request(f"{gui_server}/redact?{params}",
                                 data=sample.read_bytes(), method="POST")
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode()
    assert "page" in body and "John Smith" in body


def test_gui_rejects_bad_requests(gui_server, sample):
    # no terms and no patterns
    params = urllib.parse.urlencode({"terms": "", "filename": "sample.pdf"})
    req = urllib.request.Request(f"{gui_server}/redact?{params}",
                                 data=sample.read_bytes(), method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400

    # not a PDF
    params = urllib.parse.urlencode({"terms": "x", "filename": "junk.pdf"})
    req = urllib.request.Request(f"{gui_server}/redact?{params}",
                                 data=b"this is not a pdf", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 400


# --------------------------------------------------------------------------- ocr

@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract not installed")
def test_ocr_scanned_page(tmp_path):
    # Render a text PDF to an image-only PDF (no extractable text), then OCR-redact.
    text_pdf = make_pdf(tmp_path / "text.pdf", ["TOP SECRET CODENAME"])
    scanned = tmp_path / "scanned.pdf"
    with fitz.open(text_pdf) as src, fitz.open() as dst:
        pix = src[0].get_pixmap(dpi=200)
        page = dst.new_page(width=src[0].rect.width, height=src[0].rect.height)
        page.insert_image(page.rect, pixmap=pix)
        dst.save(scanned)
    assert text_of(scanned).strip() == ""  # confirm image-only

    result = pr.redact_pdf(scanned, ["CODENAME"], ocr=True, quiet=True)
    assert any(m.kind == "word" for m in result.matches)
