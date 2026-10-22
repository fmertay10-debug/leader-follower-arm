#!/usr/bin/env bash
# Rebuilds the report from scratch: figures -> docx (pass 1) -> pdf -> page numbers -> docx (pass 2) -> pdf
# Usage: bash report/build_all.sh
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
SK=$(ls -d "$HOME"/.claude/skills/synced/*/docx 2>/dev/null | head -1)
W=report/_build; mkdir -p "$W"
$PY report/make_figures.py
$PY report/build_report.py --out "$W/Report.docx" --dump-headings "$W/headings.json"
(cd "$W" && python3 "$SK/scripts/office/soffice.py" --headless --convert-to pdf Report.docx >/dev/null 2>&1)
$PY report/toc_pages.py "$W/Report.pdf" "$W/headings.json" "$W/pages.json"
$PY report/build_report.py --pages "$W/pages.json" --out Report.docx
(cd "$W" && cp ../../Report.docx . && python3 "$SK/scripts/office/soffice.py" --headless --convert-to pdf Report.docx >/dev/null 2>&1)
$PY report/toc_pages.py "$W/Report.pdf" "$W/headings.json" "$W/pages2.json"
cmp -s "$W/pages.json" "$W/pages2.json" && echo "table of contents consistent" || { echo "page numbers changed, 3rd pass"; $PY report/build_report.py --pages "$W/pages2.json" --out Report.docx; (cd "$W" && cp ../../Report.docx . && python3 "$SK/scripts/office/soffice.py" --headless --convert-to pdf Report.docx >/dev/null 2>&1); }
cp "$W/Report.pdf" Report.pdf
rm -rf "$W"
echo "done: Report.docx, Report.pdf ($(pdfinfo Report.pdf | grep Pages))"
