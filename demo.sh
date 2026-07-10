#!/usr/bin/env bash
#
# End-to-end demo: redact the bundled sample PDF and prove the words are gone.
#
#   ./demo.sh
#
# Assumes dependencies are installed (run ./setup.sh first). Uses .venv if present.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$SCRIPT_DIR"

# Prefer the project venv's Python if it exists, else fall back to python3.
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi

SAMPLE="samples/sample.pdf"
OUTPUT="samples/sample_redacted.pdf"

# Words/phrases we'll redact in this demo. The phone number is not listed —
# it is caught by the built-in "phone" pattern (-p phone) instead.
TERMS=("John Smith" "SSN" "123-45-6789" "john.smith@example.com")
PHONE="(555) 123-4567"

if [ ! -f "$SAMPLE" ]; then
    echo "Sample not found; generating it ..."
    "$PY" samples/make_sample.py
fi

echo "=== 1. Text in the ORIGINAL sample (sensitive data visible) ==="
"$PY" -c "import fitz,sys; print(chr(10).join(p.get_text() for p in fitz.open('$SAMPLE')))"

echo "=== 2. Running redaction (words + built-in phone pattern + metadata scrub) ==="
"$PY" pdf_redact.py "$SAMPLE" "${TERMS[@]}" -p phone --scrub-metadata -o "$OUTPUT"

echo
echo "=== 3. Text in the REDACTED output (sensitive data removed) ==="
"$PY" -c "import fitz; print(chr(10).join(p.get_text() for p in fitz.open('$OUTPUT')))"

echo "=== 4. Verifying the terms are truly gone (not just hidden) ==="
"$PY" - "$OUTPUT" "${TERMS[@]}" "$PHONE" <<'PY'
import sys, fitz
out, terms = sys.argv[1], sys.argv[2:]
text = "\n".join(p.get_text() for p in fitz.open(out)).lower()
failed = False
for t in terms:
    present = t.lower() in text
    print(f"  {t!r:32} extractable: {present}")
    failed |= present
if failed:
    print("FAIL: at least one term is still extractable.")
    sys.exit(1)
print("PASS: all terms removed from the output PDF.")
PY

echo
echo "Done. Open $OUTPUT to see the redaction boxes."
