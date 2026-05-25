"""Clear all data from the input and output folders.

Empties every immediate subfolder under ./data/ and ./output/ (e.g. data/DO,
data/Invoice, data/PO, output/DO, output/Invoice, output/PO, output/combined)
plus any loose files at the top level. The subfolders themselves are kept so
the pipeline's expected layout is preserved.

By default prompts for confirmation after previewing what will be deleted.
Pass -y / --yes to skip the prompt for scripted use.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

TARGETS: list[Path] = [Path("data"), Path("output")]


def gather_plan(root: Path) -> dict:
    """Return a summary of what would be removed from root.

    Counts every file and every nested directory (not the immediate subfolders
    of root — those are kept). Returns {"missing": True} if root does not exist.
    """
    if not root.exists():
        return {"kept": [], "files": 0, "dirs": 0, "missing": True}
    kept: list[str] = []
    files = dirs = 0
    for child in root.iterdir():
        if child.is_file():
            files += 1
        elif child.is_dir():
            kept.append(child.name)
            for sub in child.rglob("*"):
                if sub.is_file():
                    files += 1
                elif sub.is_dir():
                    dirs += 1
    return {"kept": sorted(kept), "files": files, "dirs": dirs, "missing": False}


def clear_root(root: Path) -> None:
    """Empty everything inside root, keeping root's immediate subfolders."""
    if not root.exists():
        return
    for child in list(root.iterdir()):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            for sub in list(child.iterdir()):
                if sub.is_file():
                    sub.unlink()
                elif sub.is_dir():
                    shutil.rmtree(sub)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    args = parser.parse_args()

    plans = [(root, gather_plan(root)) for root in TARGETS]
    total_files = sum(p["files"] for _, p in plans)
    total_dirs = sum(p["dirs"] for _, p in plans)

    print("Will clear contents of:")
    for root, p in plans:
        if p["missing"]:
            print(f"  {root}/  (does not exist, skipping)")
            continue
        kept_note = (
            f"  (keeping subfolders: {', '.join(p['kept'])})" if p["kept"] else ""
        )
        print(f"  {root}/ — {p['files']} file(s), {p['dirs']} directory(ies){kept_note}")
    print(f"\nTotal: {total_files} file(s), {total_dirs} directory(ies) to remove.")

    if total_files == 0 and total_dirs == 0:
        print("Nothing to clear.")
        return 0

    if not args.yes:
        try:
            ans = input("\nProceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if ans not in ("y", "yes"):
            print("Aborted.")
            return 1

    print()
    for root, _ in plans:
        clear_root(root)
        if root.exists():
            print(f"  Cleared {root}/")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
