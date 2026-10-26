'''
2_a_aida_ottmar_list.py

   Extracts the AIDA publications from the publication list of Ottmar Möhler,
   in which he marked every AIDA related paper with a green highlight.

Purpose

   Ottmar sent his complete list of peer reviewed publications on 2026-10-13,
   with the 142 AIDA related papers highlighted in green. It is the most
   reliable source for which of his papers are AIDA work.

   This script reads that Word file and keeps only the green entries. The
   output is a copy of his own document with every other entry deleted, so
   the text, numbering and formatting stay exactly as he wrote them.
   Paragraphs without any highlight, such as the heading, are kept.
   His original file is never changed.

   An entry is one paragraph. Its colour is the highlight that covers most of
   its characters, so a paragraph with a few unmarked spaces still counts.
   The other colours in his list are not AIDA work and are dropped.

Usage

   python 2_a_aida_ottmar_list.py
      reads his list from <root>/data/author_self_correction/raw/

   python 2_a_aida_ottmar_list.py --input "D:\\mail\\Ottmar_Möhler_publications.docx"
      reads his list from another place

   python 2_a_aida_ottmar_list.py --dry-run
      count only, write nothing

   The file name in raw/ may be written with ö, oe or _ ; any file matching
   ottmar_file_pattern in config.py is found. All paths come from config.py,
   the same on Linux and Windows.

Output

   <root>/data/author_self_correction/Ottmar_Moehler_AIDA_publications.docx   Word file
   a short report on the screen: entries per colour and the AIDA entries kept
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

try:
    from docx import Document
    from docx.oxml.ns import qn
except ImportError:
    sys.exit("python-docx is missing, install it with:  python -m pip install python-docx")

# --- project config with standalone fallback
# the folder of this script is put on the import path, so config.py next to it
# is found from any working directory, on Linux and on Windows alike
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
try:
    import config
except ImportError:
    config = None

# --- project root when config.py is missing
# the same rule as in config.py: the folder above scripts, or AIDA_ROOT
FALLBACK_ROOT = Path(os.environ.get("AIDA_ROOT") or (
    SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == "scripts" else SCRIPT_DIR))


# ================================================== #
# PATHS AND SETTINGS
# ================================================== #

# --- paths
SELF_CORRECTION_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_DIR",
                                   FALLBACK_ROOT / "data" / "author_self_correction"))
RAW_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_RAW_DIR", SELF_CORRECTION_DIR / "raw"))
INPUT_DOCX = Path(getattr(config, "OTTMAR_LIST_FILE", RAW_DIR / "Ottmar_Möhler_publications.docx"))
OUTPUT_DOCX = Path(getattr(config, "OTTMAR_AIDA_FILE",
                           SELF_CORRECTION_DIR / "Ottmar_Moehler_AIDA_publications.docx"))

# --- settings
file_pattern = getattr(config, "ottmar_file_pattern", "Ottmar*publications*.docx")
expected_aida = int(getattr(config, "ottmar_expected_aida", 142))
aida_colors = {color.lower() for color in getattr(config, "ottmar_aida_highlight_colors",
                                                  ["green", "darkGreen"])}


# ================================================== #
# FINDING THE INPUT
# ================================================== #

def find_input(given_path):
    # --input wins, then the name in config.py, then any file matching the pattern
    if given_path:
        if not given_path.is_file():
            sys.exit("File not found: %s" % given_path)
        return given_path
    if INPUT_DOCX.is_file():
        return INPUT_DOCX

    # --- the name may differ in ö, oe or _ , so the folder is searched
    # Word lock files (~$name.docx) and the output itself are left out
    candidates = []
    if RAW_DIR.is_dir():
        for FILE_PATH in sorted(RAW_DIR.glob(file_pattern)):
            if FILE_PATH.name.startswith("~$") or "AIDA" in FILE_PATH.stem:
                continue
            candidates.append(FILE_PATH)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        sys.exit("Ottmar's list was not found. Put it in:\n   %s\n"
                 "or give it with --input" % RAW_DIR)
    sys.exit("More than one possible list in %s:\n   %s\nKeep one, or choose it with --input"
             % (RAW_DIR, "\n   ".join(FILE_PATH.name for FILE_PATH in candidates)))


# ================================================== #
# READING THE HIGHLIGHTS
# ================================================== #

def run_highlight(run_element):
    # Word stores the highlight as <w:highlight w:val="green"/> in the run properties
    # None means the run is not highlighted
    properties = run_element.find(qn("w:rPr"))
    if properties is None:
        return None
    highlight = properties.find(qn("w:highlight"))
    if highlight is None:
        return None
    value = highlight.get(qn("w:val"))
    return None if value in (None, "none") else value


def paragraph_color(paragraph):
    # the colour covering most visible characters of the paragraph
    # returns (colour or None, text, set of all colours in the paragraph)
    counts = Counter()
    for run_element in paragraph._element.iter(qn("w:r")):
        text = "".join(node.text or "" for node in run_element.iter(qn("w:t")))
        if text.strip():
            counts[run_highlight(run_element)] += len(text.strip())
    if not counts:
        return None, "", set()
    colors = {color for color in counts if color}
    return counts.most_common(1)[0][0], paragraph.text.strip(), colors


# ================================================== #
# EXTRACTION
# ================================================== #

def extract_aida(INPUT_PATH, OUTPUT_PATH, dry_run):
    # deletes every highlighted paragraph that is not an AIDA colour
    document = Document(str(INPUT_PATH))
    color_counts = Counter()
    kept = 0
    removed = []
    mixed = []

    # --- the list is copied first, removing while iterating would skip paragraphs
    for paragraph in list(document.paragraphs):
        color, text, colors = paragraph_color(paragraph)
        if not text:
            continue
        color_counts[color or "no highlight"] += 1
        if len(colors) > 1:
            mixed.append((text, sorted(colors)))

        # --- headings and other unmarked text stay, AIDA entries stay
        if color is None:
            continue
        if color.lower() in aida_colors:
            kept += 1
            continue
        removed.append(paragraph)

    # --- report
    print("Ottmar Möhler, %s" % INPUT_PATH.name)
    for color, count in color_counts.most_common():
        mark = "  kept" if color.lower() in aida_colors else ""
        print("   %-14s %4d%s" % (color, count, mark))
    print("   AIDA entries   %4d (Ottmar reported %d)" % (kept, expected_aida))
    if kept != expected_aida:
        print("   WARNING: the count differs from what Ottmar reported, check the highlights")
    for text, colors in mixed:
        print("   note: more than one colour (%s) in: %s" % (", ".join(colors), text[:80]))

    if dry_run:
        print("   dry run, nothing written")
        return kept

    for paragraph in removed:
        paragraph._element.getparent().remove(paragraph._element)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(OUTPUT_PATH))
    print("   written: %s" % OUTPUT_PATH)
    return kept


# ================================================== #
# MAIN
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Keep only the green (AIDA) entries of Ottmar Möhler's publication list.")
    parser.add_argument("--input", type=Path, default=None,
                        help="his Word file (default: searched in %s)" % RAW_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DOCX,
                        help="Word file to write (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    INPUT_PATH = find_input(arguments.input)
    OUTPUT_PATH = arguments.output

    if INPUT_PATH.suffix.lower() != ".docx":
        sys.exit("Only .docx can be read, save the list as .docx in Word first: %s" % INPUT_PATH)
    if OUTPUT_PATH.resolve() == INPUT_PATH.resolve():
        sys.exit("The output would overwrite Ottmar's original file: %s" % INPUT_PATH)
    extract_aida(INPUT_PATH, OUTPUT_PATH, arguments.dry_run)


if __name__ == "__main__":
    main()
