'''
2_a_aida_robert_list.py

   Writes the publication list of Robert Wagner, as we sent it to him, into
   a Word file in the same form as Ottmar Möhler's list.

Purpose

   Robert answered with notes on the publication page we sent him
   (AIDA_Publication_List.html, made by 3_a_aida_bib_html.py). His notes
   point at entries of that page: "you included", "the row above". So his
   corrections can only be applied to exactly that list.

   This script reads the page, keeps every entry with Robert as an author
   and writes them as a Word file, one numbered citation per paragraph,
   newest first with the highest number, like Ottmar's own list:

      53. Zanatta, M.; ...; Wagner, R. (2025), Title, Journal, 2(1), 1-20,
          https://doi.org/10.5194/... [Other]

   Entries that are not articles get their type in brackets at the end,
   because most of the entries Robert wants removed are of that kind.
   Other people with the same surname (S. Wagner, U. Wagner) are left out:
   the author is matched with robert_author_pattern in config.py.

   The page is not changed. Step 2_b applies his notes to this list.

Usage

   python 2_a_aida_robert_list.py
      reads <root>/AIDA_lists/publications/AIDA_Publication_List.html

   python 2_a_aida_robert_list.py --input "D:\\mail\\AIDA_Publication_List.html"
      reads the page from another place

   All paths come from config.py, the same on Linux and Windows.

Output

   <root>/data/author_self_correction/Robert_Wagner_AIDA_publications.docx   Word file
   a short report on the screen: entries per type and per decade
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

try:
    from docx import Document
    from docx.shared import Pt
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
AIDA_ROOT = Path(getattr(config, "AIDA_ROOT", FALLBACK_ROOT))
INPUT_HTML = Path(getattr(config, "ROBERT_PUBLICATION_PAGE",
                          AIDA_ROOT / "AIDA_lists" / "publications" / "AIDA_Publication_List.html"))
SELF_CORRECTION_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_DIR",
                                   AIDA_ROOT / "data" / "author_self_correction"))
OUTPUT_DOCX = Path(getattr(config, "ROBERT_AIDA_FILE",
                           SELF_CORRECTION_DIR / "Robert_Wagner_AIDA_publications.docx"))

# --- settings
author_name = "Robert Wagner"
author_pattern = re.compile(getattr(config, "robert_author_pattern", r"\bWagner, R\."))
font_name = "Calibri"
font_size = 10


# ================================================== #
# READING THE PAGE
# ================================================== #

def read_records(INPUT_PATH):
    # 3_a_aida_bib_html.py stores every publication as JSON inside the page:
    #     const records = [ {...}, {...} ];
    # "</" is written as "<\/" there, so the page stays valid HTML
    text = INPUT_PATH.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"const records = (\[.*?\]);\s*\n", text, re.S)
    if not match:
        sys.exit("No publication data found in %s\nIs it the page made by 3_a_aida_bib_html.py?" % INPUT_PATH)
    records = json.loads(match.group(1).replace("<\\/", "</"))
    built = re.search(r"Generated from (.+?) on (\d{4}-\d{2}-\d{2})", text)
    source = "%s, %s" % (built.group(1), built.group(2)) if built else INPUT_PATH.name
    return records, source


# ================================================== #
# WRITING THE CITATIONS
# ================================================== #

def citation(record):
    # Authors (Year), Title, Journal, detail, DOI link [type]
    authors = record.get("authors_full") or record.get("authors_short") or ""
    year = record.get("year") or "n.d."
    parts = ["%s (%s)" % (authors, year), record.get("title_plain") or "(no title)"]
    if record.get("venue"):
        parts.append(record["venue"])
    if record.get("venue_detail"):
        parts.append(record["venue_detail"])
    text = ", ".join(part.strip(" ,") for part in parts if part.strip(" ,"))
    link = record.get("doi_url") or record.get("url") or ""
    if link:
        text += ", " + link
    if record.get("type_label") and record["type_label"] != "Article":
        text += " [%s]" % record["type_label"]
    return text


def write_docx(records, source, OUTPUT_PATH):
    document = Document()
    style = document.styles["Normal"]
    style.font.name = font_name
    style.font.size = Pt(font_size)
    document.add_paragraph("AIDA publications of %s, from the list we sent him (%s)" % (author_name, source))

    # --- newest first, the newest gets the highest number, as in Ottmar's list
    number = len(records)
    for record in records:
        document.add_paragraph("%d. %s" % (number, citation(record)))
        number -= 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        document.save(str(OUTPUT_PATH))
    except PermissionError:
        sys.exit("Cannot write %s, it is probably open in Word. Close it and run again." % OUTPUT_PATH)


# ================================================== #
# MAIN
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(description="Write Robert Wagner's publication list as a Word file.")
    parser.add_argument("--input", type=Path, default=INPUT_HTML,
                        help="the publication page (default: %(default)s)")
    parser.add_argument("--output", type=Path, default=OUTPUT_DOCX,
                        help="Word file to write (default: %(default)s)")
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    if not arguments.input.is_file():
        sys.exit("Publication page not found: %s" % arguments.input)

    records, source = read_records(arguments.input)
    mine = [record for record in records if author_pattern.search(record.get("authors_full") or "")]
    if not mine:
        sys.exit("No entry with %s found in %s" % (author_name, arguments.input.name))

    # --- the same order as on the page: newest first, then first author, then title
    mine.sort(key=lambda record: (-(record.get("year") or 0), (record.get("first_author") or "").lower(),
                                  (record.get("title_plain") or "").lower()))
    write_docx(mine, source, arguments.output)

    # --- report
    years = [record["year"] for record in mine if record.get("year")]
    print("%s, from %s (%s)" % (author_name, arguments.input.name, source))
    print("   %d of %d publications have him as an author, %s to %s"
          % (len(mine), len(records), min(years), max(years)))
    for label, count in Counter(record.get("type_label") or "Other" for record in mine).most_common():
        print("   %-16s %3d" % (label, count))
    without_link = [record for record in mine if not (record.get("doi_url") or record.get("url"))]
    for record in without_link:
        print("   no DOI or link: %s %s" % (record.get("year"), (record.get("title_plain") or "")[:60]))
    print("   written: %s" % arguments.output)


if __name__ == "__main__":
    main()
