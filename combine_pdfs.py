"""Combine each invoice with its matching delivery order into a single PDF.

Walks the split-output folders, pairs invoices and DOs by document number
(the digits in `Invoice_<num>.pdf` and `DO_<num>.pdf`), and writes one
combined PDF per pair to ./output/combined/<num>.pdf.

Order in the combined PDF: invoice pages first, then DO pages.

Run `split_invoices.py` and `split_dos.py` first to populate the inputs.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import fitz

INVOICE_FILE_RE = re.compile(r"^Invoice_(\S+)\.pdf$", re.IGNORECASE)
DO_FILE_RE = re.compile(r"^DO_(\S+)\.pdf$", re.IGNORECASE)


def index_by_number(root: Path, pattern: re.Pattern) -> dict[str, Path]:
    """Return {document_number: pdf_path} by walking `root` recursively.

    If the same number appears in multiple files (e.g. the DO appears in
    several scan folders), the first match wins and the others are reported.
    """
    found: dict[str, Path] = {}
    for path in sorted(root.rglob("*.pdf")):
        m = pattern.match(path.name)
        if not m:
            continue
        num = m.group(1)
        if num in found:
            print(
                f"  note: duplicate {num} at {path} (already have {found[num]}) — keeping first",
                file=sys.stderr,
            )
            continue
        found[num] = path
    return found


def combine(invoice_path: Path, do_path: Path, out_path: Path) -> None:
    out = fitz.open()
    with fitz.open(invoice_path) as inv:
        out.insert_pdf(inv)
    with fitz.open(do_path) as do:
        out.insert_pdf(do)
    out.save(out_path)
    out.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--invoices",
        type=Path,
        default=Path("output/Invoice"),
        help="Directory containing split invoice PDFs (default: ./output/Invoice).",
    )
    parser.add_argument(
        "--dos",
        type=Path,
        default=Path("output/DO"),
        help="Directory containing split DO PDFs (default: ./output/DO).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("output/combined"),
        help="Directory to write combined PDFs into (default: ./output/combined).",
    )
    args = parser.parse_args()

    if not args.invoices.exists():
        print(f"ERROR: invoice directory not found: {args.invoices}", file=sys.stderr)
        return 1
    if not args.dos.exists():
        print(f"ERROR: DO directory not found: {args.dos}", file=sys.stderr)
        return 1

    print(f"Indexing invoices under {args.invoices} ...")
    invoices = index_by_number(args.invoices, INVOICE_FILE_RE)
    print(f"  found {len(invoices)} invoice(s)")

    print(f"Indexing DOs under {args.dos} ...")
    dos = index_by_number(args.dos, DO_FILE_RE)
    print(f"  found {len(dos)} DO(s)")

    args.out.mkdir(parents=True, exist_ok=True)

    combined = 0
    missing_do: list[str] = []
    for num, inv_path in invoices.items():
        do_path = dos.get(num)
        if do_path is None:
            missing_do.append(num)
            continue
        out_path = args.out / f"{num}.pdf"
        combine(inv_path, do_path, out_path)
        print(f"  -> {out_path.name}  (invoice: {inv_path.name} + DO: {do_path.name})")
        combined += 1

    unused_dos = sorted(set(dos) - set(invoices))

    print(f"\nCombined {combined} invoice+DO pair(s) -> {args.out}")
    if missing_do:
        print(f"Invoices with no matching DO ({len(missing_do)}): {', '.join(sorted(missing_do))}", file=sys.stderr)
    if unused_dos:
        print(f"DOs with no matching invoice ({len(unused_dos)}): {', '.join(unused_dos)}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
