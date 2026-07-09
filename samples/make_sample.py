#!/usr/bin/env python3
"""Generate samples/sample.pdf — a small demo document with fake sensitive data.

Run this to (re)create the sample used by ../demo.sh:

    python samples/make_sample.py
"""

from pathlib import Path

import fitz  # PyMuPDF

OUT = Path(__file__).with_name("sample.pdf")

LINES = [
    "CONFIDENTIAL — Patient Intake Record",
    "",
    "Patient name: John Smith",
    "Date of birth: 1985-04-12",
    "SSN: 123-45-6789",
    "Email: john.smith@example.com",
    "Phone: (555) 123-4567",
    "",
    "Notes: Contact John Smith regarding follow-up.",
    "Insurance ID for john smith is confidential.",
]


def main() -> None:
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for i, line in enumerate(LINES):
        size = 16 if i == 0 else 12
        page.insert_text((72, y), line, fontsize=size)
        y += 24 if i == 0 else 20
    doc.save(OUT)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
