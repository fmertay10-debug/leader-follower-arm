#!/usr/bin/env python3
"""Finds the page numbers of the headings in the PDF -> pages.json. Usage: toc_pages.py report.pdf headings.json pages.json"""
import json
import re
import subprocess
import sys

pdf, heads_f, out = sys.argv[1:4]
heads = json.load(open(heads_f))
n = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout).group(1))
norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
texts = [subprocess.run(["pdftotext", "-f", str(i), "-l", str(i), "-layout", pdf, "-"], capture_output=True, text=True).stdout for i in range(1, n + 1)]
pages = {}
for h in heads:
    key = norm(h)[:38]
    hits = [i for i, t in enumerate(texts, 1) for ln in t.splitlines()
            if norm(ln).startswith(key) and "...." not in ln and ". . ." not in ln]
    pages[h] = str(max(hits)) if hits else "0"
    if not hits:
        print("! not found:", h)
json.dump(pages, open(out, "w"), ensure_ascii=False, indent=0)
print("pages:", n, "| headings:", len(pages))
