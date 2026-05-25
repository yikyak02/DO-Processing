"""Split a multi-invoice PDF into one PDF per Invoice Number.

Each page has, in its top-left corner:
    Invoice Number  <number>

We deliberately ignore any `Page X of Y` marker on the page — invoices in
the source PDF are grouped by invoice number but the page-count field is
not reliable across the merged document. Instead, we group pages by
invoice number and keep them in the order they appear in the source PDF,
counting occurrences to report a page total per invoice.

Invoices are digital (not scanned), so the embedded text layer is reliable —
no OCR or rotation handling needed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz

INVOICE_RE = re.compile(r"Invoice\s*Number\s*[:\s]\s*(\S+)", re.IGNORECASE)
SOLD_TO_RE = re.compile(
    r"SOLD\s*TO\s*:\s*\n\s*DELIVER\s*TO\s*:\s*\n\s*(.+)",
    re.IGNORECASE,
)
WRIST_KEYWORD = "wrist far east"


@dataclass
class PageInfo:
    src_index: int
    invoice_number: str | None


def parse_invoice_number(text: str) -> str | None:
    m = INVOICE_RE.search(text)
    return m.group(1) if m else None


def analyze_page(page: fitz.Page, idx: int) -> PageInfo:
    text = page.get_text("text")
    return PageInfo(idx, parse_invoice_number(text))


def infer_missing(pages: list[PageInfo]) -> None:
    """Fill in invoice number for pages where detection failed, using neighbours."""
    n = len(pages)
    for i, p in enumerate(pages):
        if p.invoice_number:
            continue
        prev_inv = next(
            (pages[j].invoice_number for j in range(i - 1, -1, -1)
             if pages[j].invoice_number),
            None,
        )
        next_inv = next(
            (pages[j].invoice_number for j in range(i + 1, n)
             if pages[j].invoice_number),
            None,
        )
        if prev_inv and prev_inv == next_inv:
            p.invoice_number = prev_inv
        elif prev_inv and not next_inv:
            p.invoice_number = prev_inv
        elif next_inv and not prev_inv:
            p.invoice_number = next_inv


def split_pdf(input_path: Path, out_dir: Path) -> list[Path]:
    doc = fitz.open(input_path)

    # First pass: read the invoice number off every page.
    pages: list[PageInfo] = []
    for i in range(doc.page_count):
        info = analyze_page(doc[i], i)
        pages.append(info)

    infer_missing(pages)

    # Group by invoice number; preserve source order within each group.
    buckets: dict[str, list[PageInfo]] = {}
    for p in pages:
        key = p.invoice_number or f"UNKNOWN_at_p{p.src_index + 1}"
        buckets.setdefault(key, []).append(p)

    # Now we know the total page count for every invoice — log per-page with
    # a running "page X of Y" derived from occurrence counts.
    running: dict[str, int] = {}
    for p in pages:
        key = p.invoice_number or f"UNKNOWN_at_p{p.src_index + 1}"
        total = len(buckets[key])
        running[key] = running.get(key, 0) + 1
        idx_in_inv = running[key]
        if p.invoice_number:
            print(f"  page {p.src_index:3d}: Inv={p.invoice_number}  ({idx_in_inv}/{total})")
        else:
            print(f"  page {p.src_index:3d}: UNKNOWN")

    written: list[Path] = []
    for inv, group in buckets.items():
        out = fitz.open()
        for p in group:
            out.insert_pdf(doc, from_page=p.src_index, to_page=p.src_index)

        out_path = out_dir / f"Invoice_{inv}.pdf"
        out.save(out_path)
        out.close()
        written.append(out_path)
        print(f"  -> {out_path.name} ({len(group)} pages)")

    doc.close()
    return written


def collect_wrist_invoices(invoices_root: Path) -> None:
    """Copy Invoice_*.pdf whose 'Sold To' contains WRIST_KEYWORD into invoices_root/Wrist/."""
    dest_dir = invoices_root / "Wrist"
    copied = scanned = 0
    for pdf in sorted(invoices_root.rglob("Invoice_*.pdf")):
        if dest_dir in pdf.parents:
            continue
        scanned += 1
        with fitz.open(pdf) as doc:
            text = doc[0].get_text("text")
        m = SOLD_TO_RE.search(text)
        if not m:
            continue
        sold_to = m.group(1).strip()
        if WRIST_KEYWORD in sold_to.lower():
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pdf, dest_dir / pdf.name)
            copied += 1
            print(f"  {pdf.name}  ->  Wrist/   ({sold_to})")
    print(f"\n  Scanned {scanned} invoice(s), copied {copied} to {dest_dir}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="PDF files to split. Defaults to every PDF in ./data/Invoice.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("output/Invoice"),
        help="Directory to write split PDFs into (default: ./output/Invoice).",
    )
    args = parser.parse_args()

    inputs = args.inputs or sorted(Path("data/Invoice").glob("*.pdf"))
    if not inputs:
        print("No input PDFs found.", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)

    for src in inputs:
        sub = args.out / src.stem
        sub.mkdir(parents=True, exist_ok=True)
        print(f"\n== {src} ==")
        split_pdf(src, sub)

    print("\n== Collecting Wrist Far East invoices ==")
    collect_wrist_invoices(args.out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
