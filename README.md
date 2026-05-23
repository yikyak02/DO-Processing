# DO Sorting

Split Alstern delivery-order and invoice PDFs that have multiple documents
concatenated together into one PDF per document number.

Each page in the source PDFs has, in its corner:

```
DO Number  02610155          Page: 1 of 4
```

The scripts read those two fields off every page, group pages by document
number, sort them by `Page X of Y`, and write each group out as its own PDF.

## Scripts

| Script | What it does |
|---|---|
| `split_dos.py` | Splits multi-DO PDFs into one PDF per DO. Handles upside-down scans via OCR. |
| `split_invoices.py` | Splits multi-invoice PDFs into one PDF per invoice. |
| `combine_pdfs.py` | Pairs each invoice with its matching DO (by number) and writes one combined PDF per pair. |

The two splitters handle pages that appear out of order within a document
(e.g. page 4 before page 3) — they sort by `Page X of Y` before writing.
`split_dos.py` additionally handles scanned pages that are upside down by
OCR'ing at 180° and rotating the page in the output.

`combine_pdfs.py` is the final step — run it after both splitters.

## Setup

### 1. Python dependencies

```bash
pip3 install PyMuPDF
```

(If pip refuses on macOS due to PEP 668, use
`pip3 install --break-system-packages PyMuPDF`.)

### 2. Tesseract (only required for `split_dos.py`)

`split_dos.py` shells out to `tesseract` when it needs to recover text from
upside-down scanned pages. Install with Homebrew:

```bash
brew install tesseract
```

`split_invoices.py` does **not** need tesseract.

## Directory layout

The scripts expect this layout by default:

```
DO Sorting/
├── split_dos.py
├── split_invoices.py
├── data/
│   ├── DO/                      # input DO PDFs
│   │   ├── foo.pdf
│   │   └── bar.pdf
│   └── Invoice/                 # input invoice PDFs
│       └── Merged Invoices.pdf
└── output/                      # created automatically
    ├── DO/
    │   ├── foo/                 # one subdirectory per input file
    │   │   ├── DO_02610155.pdf
    │   │   ├── DO_02610408.pdf
    │   │   └── ...
    │   └── bar/
    │       └── ...
    ├── Invoice/
    │   └── Merged Invoices/
    │       ├── Invoice_02610155.pdf
    │       └── ...
    └── combined/                # invoice + matching DO, one PDF per number
        ├── 02610155.pdf
        ├── 02610408.pdf
        └── ...
```

## Usage

### Split DOs

```bash
# Process every PDF in ./data/DO/ — output to ./output/DO/
python3 split_dos.py

# Process specific files
python3 split_dos.py path/to/one.pdf path/to/two.pdf

# Custom output directory
python3 split_dos.py --out some_dir path/to/one.pdf
```

### Split invoices

```bash
# Process every PDF in ./data/Invoice/ — output to ./output/Invoice/
python3 split_invoices.py

# Process specific files
python3 split_invoices.py path/to/one.pdf

# Custom output directory
python3 split_invoices.py --out some_dir path/to/one.pdf
```

### Combine invoice + DO pairs

After running both splitters:

```bash
# Pair every invoice in ./output/Invoice/ with its DO in ./output/DO/ —
# write to ./output/combined/<num>.pdf
python3 combine_pdfs.py

# Custom locations
python3 combine_pdfs.py --invoices some/inv_dir --dos some/do_dir --out some_out
```

Pairing is by document number (the digits in `Invoice_<num>.pdf` and
`DO_<num>.pdf`). Each combined PDF contains the invoice pages first, then the
DO pages. If a number appears in multiple split-output subdirectories (e.g.
several scan batches), the first match wins and the rest are noted on stderr.
Invoices with no matching DO, and DOs with no matching invoice, are listed at
the end of the run.


## How the splitting works

1. **Read each page** of the input PDF.
2. **Try the embedded text layer** (`page.get_text`) and parse out
   `DO Number`/`Invoice Number` and `Page X of Y` via regex.
3. **(DOs only) If that fails**, OCR the page with tesseract at 0°, then at
   180°. If 180° succeeds, remember to rotate the output page.
4. **Group pages by document number**, sort each group by `Page X of Y`.
5. **Write one PDF per group** to the output directory.

## Edge cases the scripts handle

- **Out-of-order pages within a document** — sorted by `Page X of Y`.
- **Upside-down scanned pages** (DO script only) — detected via OCR, rotated
  in the output.
- **A page where no markers can be read** — falls back to the document number
  of the surrounding pages if they agree; otherwise written out as
  `UNKNOWN_at_pN.pdf` so nothing gets silently lost.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: No module named 'fitz'` | Run `pip3 install PyMuPDF`. |
| `ERROR: tesseract not found on PATH.` | Run `brew install tesseract`. |
| `No input PDFs found.` | Check that PDFs are under `data/DO/` or `data/Invoice/`, or pass paths explicitly on the command line. |
| Some pages tagged `UNKNOWN` | OCR couldn't find the markers. Open the PDF, check the top-left of those pages — they may have unusual formatting or be too low-quality to read. |
| Wrong pages grouped together | The DO/Invoice number on those pages may be wrong in the source PDF. The script trusts the printed markers; check the per-page log to see what it read. |
