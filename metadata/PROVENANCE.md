# Provenance record — pdf-redact

Factual record of how this project was developed, kept for audit and
provenance purposes. Generated 2026-07-10 (UTC); update when circumstances
change. Machine-readable companions: [`git-history.json`](git-history.json)
(full commit log) and [`file-manifest.json`](file-manifest.json) (SHA-256
hashes of every tracked file at v1.0.0).

## Project

| | |
| --- | --- |
| Name | pdf-redact |
| Repository | https://github.com/emilgoh/pdf-redact |
| License | Apache License 2.0 (see `LICENSE`) |
| First release | v1.0.0, tagged 2026-07-10, commit `bfc04a1` |
| Author / copyright holder | Emil Goh <emilgoh@outlook.com> |

## Development timeline

All development took place 2026-07-09 → 2026-07-10 (UTC+8), across 7 commits
from the initial commit `c6ef9b3` to the v1.0.0 release `bfc04a1`. The full
log with hashes, timestamps, and authorship trailers is in
`git-history.json`.

## AI assistance disclosure

Portions of the code, tests, and documentation were written with the
assistance of **Claude Code** (Anthropic), using the Claude Fable 5 model,
operated and reviewed by the repository author. AI-assisted commits carry a
`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer; the
per-commit record is preserved in `git-history.json`. The author directed
the work, reviewed the changes, and is responsible for the released code.

No third-party proprietary source code was knowingly copied into this
project. All functionality is implemented against the documented public API
of the dependencies listed in `THIRD_PARTY_LICENSES.md`.

## Development environment

| Component | Version |
| --- | --- |
| Python | 3.9.6 (macOS system Python) |
| PyMuPDF | 1.26.5 |
| Tesseract (optional, OCR) | 5.5.2 |
| Platform | macOS 26.5 (Darwin 25.5.0) |

## Fitness-for-purpose note

pdf-redact removes matched text from PDF page content via PyMuPDF redaction
annotations (`apply_redactions()`). Its behavior is covered by an automated
test suite (38 tests at v1.0.0), but **no warranty is made that any given
redaction is complete** — see the Apache-2.0 warranty disclaimer (LICENSE
§7–8) and the Limitations section of the README. Users are advised to verify
output (e.g. with `--dry-run` and text extraction) before distributing
redacted documents.
