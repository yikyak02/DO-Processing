"""Sort customer invoices by Sold To, then merge with matching PO Excel files.

Phase 1 — sort:
    Walks output/Invoice/ recursively, reads the "Sold To" company off page 1
    of each invoice PDF, and copies the file into output/PO/<customer>/ if
    the customer matches one of the configured targets (Fuji Trading,
    Con-Lash Supplies). Matching is a case-insensitive substring check.

Phase 2 — merge with PO (and DO when available):
    Walks output/PO/<customer>/ subfolders. For each invoice:
      1. Reads the "PO NUMBER" field off page 1.
      2. Finds the matching customer folder under data/PO/ by substring
         (e.g. customer "Fuji Trading" matches data/PO/Fuji/).
      3. Looks for a file named PO_<po-number>_*.XLS in that folder.
      4. If found, converts the XLS to PDF via LibreOffice (`soffice
         --headless`).
      5. Looks for a matching DO under output/DO/ (any file named
         DO_<invoice-number>.pdf, recursively).
      6. Writes invoice + PO + DO (or invoice + PO if no DO match) as one
         combined file to output/PO/<customer>/combined/<invoice-filename>.

Requirements: LibreOffice on PATH (`brew install --cask libreoffice`).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz

SOLD_TO_RE = re.compile(
    r"SOLD\s*TO\s*:\s*\n\s*DELIVER\s*TO\s*:\s*\n\s*(.+)",
    re.IGNORECASE,
)
PO_NUMBER_RE = re.compile(r"PO\s*NUMBER\s*\n\s*(\S+)", re.IGNORECASE)
DATE_RE = re.compile(r"Date\s+(\d{2})/(\d{2})/(\d{4})", re.IGNORECASE)

# Invoice filenames look like "Invoice_<num>.pdf"; we use the <num> to find a
# matching split DO in output/DO/.../DO_<num>.pdf.
INVOICE_FILE_RE = re.compile(r"^Invoice_(\S+)\.pdf$", re.IGNORECASE)
DO_FILE_RE = re.compile(r"^DO_(\S+)\.pdf$", re.IGNORECASE)

TARGETS: list[tuple[str, list[str]]] = [
    ("Fuji Trading", ["fuji trading"]),
    ("Con-Lash Supplies", ["con-lash supplies", "con lash supplies"]),
]


# ---------- shared helpers -------------------------------------------------

def read_page1_text(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    try:
        return doc[0].get_text("text")
    finally:
        doc.close()


def extract_sold_to(text: str) -> str | None:
    m = SOLD_TO_RE.search(text)
    return m.group(1).strip() if m else None


def extract_po_number(text: str) -> str | None:
    m = PO_NUMBER_RE.search(text)
    return m.group(1).strip() if m else None


def extract_date_ddmmyyyy(text: str) -> str | None:
    m = DATE_RE.search(text)
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else None


def target_folder(company: str) -> str | None:
    lc = company.lower()
    for folder, keywords in TARGETS:
        if any(kw in lc for kw in keywords):
            return folder
    return None


# ---------- phase 1: sort invoices by customer -----------------------------

def sort_invoices(invoices_root: Path, po_root: Path) -> None:
    po_root.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {folder: 0 for folder, _ in TARGETS}
    scanned = skipped = 0
    for pdf in sorted(invoices_root.rglob("Invoice_*.pdf")):
        scanned += 1
        company = extract_sold_to(read_page1_text(pdf))
        if not company:
            print(f"  {pdf.name}: could not read Sold To field", file=sys.stderr)
            skipped += 1
            continue
        folder = target_folder(company)
        if folder is None:
            continue
        dest_dir = po_root / folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf, dest_dir / pdf.name)
        counts[folder] += 1
        print(f"  {pdf.name}  ->  PO/{folder}/   ({company})")

    print(f"\n  Scanned {scanned} invoice(s), skipped {skipped}.")
    for folder, n in counts.items():
        print(f"    {folder}: {n} file(s)")


# ---------- phase 2: merge each invoice with its PO Excel ------------------

def find_po_data_folder(customer_folder_name: str, po_data_root: Path) -> Path | None:
    """Substring-match a subfolder of po_data_root against the customer name.

    The data folder's name must appear as a substring of the customer
    folder name (case-insensitive). e.g. customer "Fuji Trading" matches
    data/PO/Fuji/ because "fuji" is in "fuji trading".
    """
    if not po_data_root.exists():
        return None
    lc = customer_folder_name.lower()
    for sub in po_data_root.iterdir():
        if sub.is_dir() and sub.name.lower() in lc:
            return sub
    return None


def find_po_xls(po_folder: Path, po_number: str) -> Path | None:
    """Find PO_<po_number>_*.XLS (case-insensitive) in po_folder."""
    matches = [
        p for p in po_folder.glob("PO_*")
        if re.match(rf"^PO_{re.escape(po_number)}(?:[_.].*)?\.xlsx?$", p.name, re.IGNORECASE)
    ]
    return matches[0] if matches else None


def xls_to_pdf(xls_path: Path, out_dir: Path) -> Path:
    """Convert an XLS/XLSX to PDF using LibreOffice headless. Returns PDF path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf",
         "--outdir", str(out_dir), str(xls_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    pdf = out_dir / (xls_path.stem + ".pdf")
    if not pdf.exists():
        raise RuntimeError(
            f"soffice produced no PDF for {xls_path}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return pdf


def combine_pdfs(parts: list[Path], out_path: Path) -> None:
    out = fitz.open()
    for part in parts:
        with fitz.open(part) as src:
            out.insert_pdf(src)
    out.save(out_path)
    out.close()


def index_dos(do_root: Path) -> dict[str, Path]:
    """Walk do_root recursively, return {do_number: pdf_path}. First match wins."""
    found: dict[str, Path] = {}
    if not do_root.exists():
        return found
    for path in sorted(do_root.rglob("*.pdf")):
        m = DO_FILE_RE.match(path.name)
        if not m:
            continue
        found.setdefault(m.group(1), path)
    return found


def invoice_number_from_filename(invoice_pdf: Path) -> str | None:
    m = INVOICE_FILE_RE.match(invoice_pdf.name)
    return m.group(1) if m else None


def merge_invoices_with_pos(
    po_root: Path, po_data_root: Path, do_root: Path
) -> None:
    if not shutil.which("soffice"):
        print(
            "ERROR: soffice not on PATH. Install LibreOffice "
            "(`brew install --cask libreoffice`) to enable PO merging.",
            file=sys.stderr,
        )
        return

    if not po_root.exists():
        print(f"  {po_root} does not exist — nothing to merge.", file=sys.stderr)
        return

    do_index = index_dos(do_root)
    print(f"  Indexed {len(do_index)} DO PDF(s) under {do_root}")

    merged = merged_with_do = 0
    unmerged_no_po_number: list[str] = []
    unmerged_no_xls_match: list[str] = []
    unmerged_soffice_failed: list[str] = []
    unmerged_no_data_folder: list[str] = []

    for customer_dir in sorted(p for p in po_root.iterdir() if p.is_dir()):
        if customer_dir.name == "combined":
            continue
        data_folder = find_po_data_folder(customer_dir.name, po_data_root)
        if data_folder is None:
            print(
                f"  {customer_dir.name}: no matching folder under {po_data_root}, "
                "skipping merge",
                file=sys.stderr,
            )
            unmerged_no_data_folder.append(customer_dir.name)
            continue

        combined_dir = customer_dir / "combined"

        # Reuse one temp dir per customer for the xls->pdf intermediates.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            for invoice_pdf in sorted(customer_dir.glob("Invoice_*.pdf")):
                page1_text = read_page1_text(invoice_pdf)
                po_num = extract_po_number(page1_text)
                if not po_num:
                    print(f"  {invoice_pdf.name}: no PO NUMBER field, skipping",
                          file=sys.stderr)
                    unmerged_no_po_number.append(
                        f"{customer_dir.name}/{invoice_pdf.name}"
                    )
                    continue
                xls = find_po_xls(data_folder, po_num)
                if xls is None:
                    print(f"  {invoice_pdf.name}: PO {po_num} not found in "
                          f"{data_folder}, skipping", file=sys.stderr)
                    unmerged_no_xls_match.append(
                        f"{customer_dir.name}/{invoice_pdf.name} (PO {po_num})"
                    )
                    continue
                try:
                    po_pdf = xls_to_pdf(xls, tmp_dir)
                except subprocess.CalledProcessError as e:
                    print(f"  {invoice_pdf.name}: soffice failed for {xls.name}: "
                          f"{e.stderr.strip()}", file=sys.stderr)
                    unmerged_soffice_failed.append(
                        f"{customer_dir.name}/{invoice_pdf.name} (XLS {xls.name})"
                    )
                    continue

                parts = [invoice_pdf, po_pdf]
                inv_num = invoice_number_from_filename(invoice_pdf)
                do_pdf = do_index.get(inv_num) if inv_num else None
                if do_pdf is not None:
                    parts.append(do_pdf)
                    merged_with_do += 1

                if customer_dir.name == "Con-Lash Supplies":
                    date_str = extract_date_ddmmyyyy(page1_text)
                    if date_str and inv_num:
                        out_name = f"{po_num}_{inv_num}_{date_str}.pdf"
                    else:
                        print(f"  {invoice_pdf.name}: could not extract date, "
                              f"using default filename", file=sys.stderr)
                        out_name = invoice_pdf.name
                else:
                    out_name = invoice_pdf.name

                combined_dir.mkdir(parents=True, exist_ok=True)
                out_path = combined_dir / out_name
                combine_pdfs(parts, out_path)
                merged += 1

                do_note = f"  +  {do_pdf.name}" if do_pdf else "  (no DO)"
                print(f"  {invoice_pdf.name}  +  {xls.name}{do_note}  ->  "
                      f"PO/{customer_dir.name}/combined/{out_name}")

    print(f"\n  Merged {merged} invoice+PO pair(s); "
          f"{merged_with_do} of those also include a DO.")

    total_unmerged = (
        len(unmerged_no_po_number)
        + len(unmerged_no_xls_match)
        + len(unmerged_soffice_failed)
    )
    if total_unmerged or unmerged_no_data_folder:
        print(f"\n  Unmerged invoices: {total_unmerged}")
        if unmerged_no_po_number:
            print(f"    No PO NUMBER field ({len(unmerged_no_po_number)}):")
            for name in unmerged_no_po_number:
                print(f"      - {name}")
        if unmerged_no_xls_match:
            print(f"    No matching XLS in data/PO ({len(unmerged_no_xls_match)}):")
            for name in unmerged_no_xls_match:
                print(f"      - {name}")
        if unmerged_soffice_failed:
            print(f"    LibreOffice conversion failed ({len(unmerged_soffice_failed)}):")
            for name in unmerged_soffice_failed:
                print(f"      - {name}")
        if unmerged_no_data_folder:
            print(f"    Customer folders with no data/PO subfolder "
                  f"({len(unmerged_no_data_folder)}):")
            for name in unmerged_no_data_folder:
                print(f"      - {name}")


# ---------- entry point ----------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--invoices",
        type=Path,
        default=Path("output/Invoice"),
        help="Root directory of split invoice PDFs (default: ./output/Invoice).",
    )
    parser.add_argument(
        "--po-out",
        type=Path,
        default=Path("output/PO"),
        help="Where to write sorted + merged PO PDFs (default: ./output/PO).",
    )
    parser.add_argument(
        "--po-data",
        type=Path,
        default=Path("data/PO"),
        help="Where the PO Excel files live (default: ./data/PO).",
    )
    parser.add_argument(
        "--dos",
        type=Path,
        default=Path("output/DO"),
        help="Root directory of split DO PDFs to merge in (default: ./output/DO).",
    )
    parser.add_argument(
        "--skip-sort",
        action="store_true",
        help="Skip phase 1 (sort invoices into customer folders).",
    )
    parser.add_argument(
        "--skip-merge",
        action="store_true",
        help="Skip phase 2 (merge each invoice with its matching PO XLS).",
    )
    args = parser.parse_args()

    if not args.skip_sort:
        if not args.invoices.exists():
            print(f"ERROR: invoice directory not found: {args.invoices}", file=sys.stderr)
            return 1
        print("== Phase 1: sort invoices by customer ==")
        sort_invoices(args.invoices, args.po_out)

    if not args.skip_merge:
        print("\n== Phase 2: merge invoices with PO Excel files (and DOs when available) ==")
        merge_invoices_with_pos(args.po_out, args.po_data, args.dos)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
