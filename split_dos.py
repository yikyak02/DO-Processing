"""Split a multi-DO delivery-order PDF into one PDF per DO number.

Each page of the input PDF has, in its top-left corner:
    DO Number  <number>
    Page: X of Y

We use those fields to (a) group pages by DO and (b) order them correctly.
Some pages may be physically upside down (scanned that way); in that case
the embedded text is unreadable, so we OCR the page at 180 degrees and, when
we find the markers there, mark the page as needing a 180-degree rotation in
the output PDF.
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz

DO_RE = re.compile(r"DO\s*Number\s*[:\s]\s*(\d+)", re.IGNORECASE)
PAGE_RE = re.compile(r"Page\s*[:\s]*\s*(\d+)\s*of\s*(\d+)", re.IGNORECASE)


@dataclass
class PageInfo:
    src_index: int
    do_number: str | None
    page_num: int | None
    total_pages: int | None
    extra_rotation: int  # degrees to add when writing to output (0 or 180)


def parse_markers(text: str) -> tuple[str | None, int | None, int | None]:
    do_match = DO_RE.search(text)
    pg_match = PAGE_RE.search(text)
    do = do_match.group(1) if do_match else None
    if pg_match:
        return do, int(pg_match.group(1)), int(pg_match.group(2))
    return do, None, None


def ocr_text(page: fitz.Page, rotate: int) -> str:
    """Render the page (with optional extra rotation) to PNG, OCR with tesseract."""
    mat = fitz.Matrix(2, 2)  # 2x zoom for better OCR
    if rotate:
        mat = mat * fitz.Matrix(rotate)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    png_bytes = pix.tobytes("png")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "6"],
        input=png_bytes,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="replace")


def analyze_page(page: fitz.Page, idx: int) -> PageInfo:
    """Return the DO number, page-of info, and any extra rotation needed."""
    # 1. Try the embedded text layer first (fast path).
    text = page.get_text("text")
    do, pn, tp = parse_markers(text)
    if do and pn and tp:
        return PageInfo(idx, do, pn, tp, 0)

    # 2. Embedded text is missing or garbled -> OCR at 0 degrees.
    try:
        text0 = ocr_text(page, 0)
        do, pn, tp = parse_markers(text0)
        if do and pn and tp:
            return PageInfo(idx, do, pn, tp, 0)
    except subprocess.CalledProcessError:
        text0 = ""

    # 3. Try OCR at 180 degrees (page is physically upside down).
    try:
        text180 = ocr_text(page, 180)
        do, pn, tp = parse_markers(text180)
        if do and pn and tp:
            return PageInfo(idx, do, pn, tp, 180)
    except subprocess.CalledProcessError:
        pass

    # 4. Give up; caller will have to infer from neighbours.
    return PageInfo(idx, None, None, None, 0)


def infer_missing(pages: list[PageInfo]) -> None:
    """Fill in DO/page-num for pages where detection failed, using neighbours.

    Heuristic: if a page sits between two pages of the same DO and its
    position is consistent with a missing page number in that DO, assign it.
    """
    n = len(pages)
    for i, p in enumerate(pages):
        if p.do_number and p.page_num:
            continue
        # Find nearest known DO before and after.
        prev_do = next(
            (pages[j].do_number for j in range(i - 1, -1, -1) if pages[j].do_number),
            None,
        )
        next_do = next(
            (pages[j].do_number for j in range(i + 1, n) if pages[j].do_number),
            None,
        )
        if prev_do and prev_do == next_do:
            p.do_number = prev_do
        elif prev_do and not next_do:
            p.do_number = prev_do
        elif next_do and not prev_do:
            p.do_number = next_do
        # Page number stays None; we'll order unknown-page-num pages by src_index.


def split_pdf(input_path: Path, out_dir: Path) -> list[Path]:
    doc = fitz.open(input_path)
    pages: list[PageInfo] = []
    for i in range(doc.page_count):
        info = analyze_page(doc[i], i)
        pages.append(info)
        marker = (
            f"DO={info.do_number} pg={info.page_num}/{info.total_pages}"
            if info.do_number
            else "UNKNOWN"
        )
        rot_note = f" [rotate {info.extra_rotation}]" if info.extra_rotation else ""
        print(f"  page {i:3d}: {marker}{rot_note}")

    infer_missing(pages)

    # Group by DO, preserving stable order; unknowns get their own bucket.
    buckets: dict[str, list[PageInfo]] = {}
    for p in pages:
        key = p.do_number or f"UNKNOWN_at_p{p.src_index + 1}"
        buckets.setdefault(key, []).append(p)

    written: list[Path] = []
    for do, group in buckets.items():
        # Sort by reported page number; pages without a number go to the end
        # in their original input order.
        group.sort(key=lambda p: (p.page_num is None, p.page_num or 0, p.src_index))

        out = fitz.open()
        for p in group:
            out.insert_pdf(doc, from_page=p.src_index, to_page=p.src_index)
            new_page = out[-1]
            if p.extra_rotation:
                new_page.set_rotation((new_page.rotation + p.extra_rotation) % 360)

        out_path = out_dir / f"DO_{do}.pdf"
        out.save(out_path)
        out.close()
        written.append(out_path)
        print(f"  -> {out_path.name} ({len(group)} pages)")

    doc.close()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="PDF files to split. Defaults to every PDF in ./data/DO.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("output/DO"),
        help="Directory to write split PDFs into (default: ./output/DO).",
    )
    args = parser.parse_args()

    if not shutil.which("tesseract"):
        print("ERROR: tesseract not found on PATH.", file=sys.stderr)
        return 1

    inputs = args.inputs or sorted(Path("data/DO").glob("*.pdf"))
    if not inputs:
        print("No input PDFs found.", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)

    for src in inputs:
        sub = args.out / src.stem
        sub.mkdir(parents=True, exist_ok=True)
        print(f"\n== {src} ==")
        split_pdf(src, sub)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
