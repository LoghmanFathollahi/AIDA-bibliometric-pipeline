'''
2_c_aida_robert_bib.py

   Builds the bib file of Robert Wagner's AIDA publications from his
   corrected list made by 2_b_aida_robert_check.py.

Purpose

   The corrected list holds one row per paper: the entries of the list we
   sent him that he kept, the papers he added, and the entries he removed.
   Removed rows are skipped. For every other row one BibTeX entry is built,
   from the most to the least reliable source:

      doi_org        the record from doi.org, the publisher's own metadata
      crossref_api   the same record through the Crossref API, if doi.org fails
      own_harvest    the record from data/openalex or data/webofscience
      our_list       no DOI: the entry as it stands on our publication page,
                     with its exact authors, title and venue
      citation       nothing at all: built from the citation text and
                     marked unverified

   A record already downloaded for Ottmar is taken from his cache, so the
   papers both authors share are not downloaded twice.

   Every entry gets his list number, the source, and what happened to it
   (kept, added, kept by decision) as extra fields, and a key of the form
   year_firstauthor_journal as in the master bibliography. A paper listed
   twice under one DOI becomes one entry with both numbers.

   The BibTeX helpers are loaded from 2_c_aida_ottmar_bib.py, so both bib
   files are built by the same rules.

Usage

   python 2_c_aida_robert_bib.py
      build the bib file

   python 2_c_aida_robert_bib.py --offline
      no internet needed: cached records, own harvests and our list only

   python 2_c_aida_robert_bib.py --refresh
      download every record again, ignore the caches

   Run 2_b_aida_robert_check.py first. All paths come from config.py, the
   same on Linux and Windows.

Output

   <root>/data/author_self_correction/bib/Robert_Wagner.bib      one entry per paper
   <root>/data/author_self_correction/cache/bibtex_robert.json   cached BibTeX records
   the bib folder is created when it does not exist
   a summary on the screen: how many records each source gave
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import importlib.util
import json
import os
import re
import sys
import urllib.error
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

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
SELF_CORRECTION_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_DIR",
                                   AIDA_ROOT / "data" / "author_self_correction"))
DOI_CSV = Path(getattr(config, "ROBERT_DOI_FILE", SELF_CORRECTION_DIR / "Robert_Wagner_AIDA_dois.csv"))
PUBLICATION_PAGE = Path(getattr(config, "ROBERT_PUBLICATION_PAGE",
                                AIDA_ROOT / "AIDA_lists" / "publications" / "AIDA_Publication_List.html"))
CACHE_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_CACHE_DIR", SELF_CORRECTION_DIR / "cache"))
BIBTEX_CACHE = CACHE_DIR / "bibtex_robert.json"
OTTMAR_BIBTEX_CACHE = CACHE_DIR / "bibtex_ottmar.json"
BIB_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_BIB_DIR", SELF_CORRECTION_DIR / "bib"))
BIB_FILE = Path(getattr(config, "ROBERT_BIB_FILE", BIB_DIR / "Robert_Wagner.bib"))
# the BibTeX rules are shared with Ottmar's step
OTTMAR_BIB_SCRIPT = SCRIPT_DIR / "2_c_aida_ottmar_bib.py"

# --- the source written into every bib entry
aida_source_text = "Robert Wagner, notes on our AIDA publication list, sent 2026-10-14"

# --- our page's type labels as BibTeX entry types, for entries without a DOI
type_map = {"Article": "article", "Conference paper": "inproceedings", "Proceedings": "proceedings",
            "Book chapter": "incollection", "Book": "book", "PhD thesis": "phdthesis",
            "Report": "techreport", "Dataset": "misc", "Other": "misc"}


# ================================================== #
# SHARED BIBTEX RULES
# ================================================== #

def load_shared():
    # 2_c_aida_ottmar_bib.py starts with a digit, so it is loaded by path
    if not OTTMAR_BIB_SCRIPT.is_file():
        sys.exit("Needed next to this script: %s" % OTTMAR_BIB_SCRIPT.name)
    spec = importlib.util.spec_from_file_location("aida_ottmar_bib", OTTMAR_BIB_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_shared()
# a venue written into howpublished gets the same escaping of & and % as a journal name
shared.text_fields.add("howpublished")


# ================================================== #
# OUR PUBLICATION PAGE
# ================================================== #

def squash(text):
    return re.sub(r"[^a-z0-9]", "", shared.fold(text or ""))


def page_records():
    # the publications of our page, read only for entries without a DOI
    if not PUBLICATION_PAGE.is_file():
        return []
    text = PUBLICATION_PAGE.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"const records = (\[.*?\]);\s*\n", text, re.S)
    return json.loads(match.group(1).replace("<\\/", "</")) if match else []


def from_page(row, records):
    # the page entry whose title is in the row's citation and whose year agrees
    citation = squash(row.get("citation"))
    for record in records:
        title = squash(record.get("title_plain"))
        if len(title) >= 15 and title in citation and str(record.get("year") or "") == str(row.get("year") or ""):
            # "Möhler, O.; Benz, S." becomes "Möhler, O. and Benz, S."
            fields = {"author": " and ".join(name.strip() for name in
                                             (record.get("authors_full") or "").split(";") if name.strip()),
                      "title": record.get("title_plain", ""),
                      "year": str(record.get("year") or "")}
            entry_type = type_map.get(record.get("type_label"), "misc")
            venue = record.get("venue", "")
            if venue:
                if entry_type == "article":
                    fields["journal"] = venue
                elif entry_type in ("inproceedings", "incollection"):
                    fields["booktitle"] = venue
                else:
                    fields["howpublished"] = venue
            if record.get("url"):
                fields["url"] = record["url"]
            return entry_type, fields
    return None, {}


# ================================================== #
# BUILDING THE BIB FILE
# ================================================== #

def build_bib(arguments):
    rows = [row for row in shared.read_csv_rows(DOI_CSV) if (row.get("change") or "") != "removed"]
    if not rows:
        sys.exit("No corrected list at %s\nRun 2_b_aida_robert_check.py first." % DOI_CSV)
    print("Corrected list: %d papers from %s" % (len(rows), DOI_CSV.name))

    # --- one group per DOI; rows without a DOI stay alone
    groups = defaultdict(list)
    for row in rows:
        doi = shared.clean_doi(row.get("doi", ""))
        row["doi"] = doi
        groups[doi or "nodoi_%s" % row["number"]].append(row)

    cache = {} if arguments.refresh else shared.load_json(BIBTEX_CACHE)
    ottmar_cache = {} if arguments.refresh else shared.load_json(OTTMAR_BIBTEX_CACHE)
    offline = arguments.offline
    harvest = None
    page = None
    entries = []
    counts = Counter()
    unverified = []
    merged = []

    for group in groups.values():
        group.sort(key=lambda row: int(row["number"]))
        first = group[0]
        doi = first["doi"]
        entry_type, fields, source = None, {}, ""

        # --- his cache, Ottmar's cache, then a download
        if doi:
            if doi not in cache and doi in ottmar_cache:
                cache[doi] = ottmar_cache[doi]
            if doi not in cache and not offline:
                try:
                    text, record_source = shared.fetch_record(doi)
                    if text:
                        cache[doi] = {"text": text, "source": record_source}
                except (urllib.error.URLError, TimeoutError) as error:
                    print("   doi.org not reachable (%s), continuing with harvests and cache" % error)
                    offline = True
            if doi in cache:
                entry_type, fields = shared.parse_record(cache[doi]["text"])
                source = cache[doi]["source"]

        # --- the own harvests, then our page, then the citation
        if not entry_type and doi:
            if harvest is None:
                harvest = shared.harvest_records()
            if doi in harvest:
                entry_type, fields = harvest[doi][0], dict(harvest[doi][1])
                source = "own_harvest"
        if not entry_type:
            if page is None:
                page = page_records()
            entry_type, fields = from_page(first, page)
            source = "our_list" if entry_type else ""
        if not entry_type:
            entry_type, fields = shared.from_citation(first)
            source = "citation"
            unverified.append(first)

        # --- tidy the fields, the url always points to doi.org when there is a DOI
        fields = {name: shared.tidy_value(name, value) for name, value in fields.items()
                  if name not in ("key", "keywords") and value}
        if doi:
            fields["doi"] = doi
            fields["url"] = shared.doi_org_url + doi
        if not fields.get("year") and first.get("year"):
            fields["year"] = str(first["year"])

        # --- where it came from and what happened to it
        numbers = [row["number"] for row in group]
        fields["robert_number"] = ", ".join(numbers)
        fields["aida_source"] = aida_source_text
        fields["record_source"] = source
        fields["doi_method"] = first.get("method") or "none"
        fields["change"] = first.get("change") or "kept"
        if first.get("reason"):
            fields["change_reason"] = shared.tidy_value("note", first["reason"])
        statuses = {row.get("status") or "" for row in group}
        if source == "citation":
            fields["doi_status"] = "unverified"
        elif statuses != {"found"} and doi:
            fields["doi_status"] = "/".join(sorted(statuses))
        if len(group) > 1:
            merged.append(numbers)

        counts[source] += 1
        year = re.search(r"\d{4}", fields.get("year", "") + "0000").group(0)
        entries.append({"type": entry_type, "fields": fields, "year": int(year), "number": int(first["number"])})

    if not arguments.offline:
        shared.save_json(BIBTEX_CACHE, cache)

    # --- newest first, then by his number, then keys as in the master bibliography
    entries.sort(key=lambda entry: (-entry["year"], -entry["number"]))
    used_keys = set()
    blocks = []
    for entry in entries:
        base = "%s_%s_%s" % (entry["fields"].get("year") or "noyear",
                             shared.key_surname(entry["fields"]),
                             shared.key_journal(entry["type"], entry["fields"]))
        key, letter = base, ord("a")
        while key in used_keys:
            letter += 1
            key = "%s_%s" % (base, chr(letter))
        used_keys.add(key)
        blocks.append(shared.format_entry(entry["type"], key, entry["fields"]))

    # --- write; the lines start with %, the BibTeX comment sign, so they are joined, not formatted
    header = ["% " + BIB_FILE.name,
              "% AIDA publications of Robert Wagner, our list corrected with his notes",
              "% source   : " + aida_source_text,
              "% built by : 2_c_aida_robert_bib.py, " + date.today().isoformat(),
              "% entries  : {} from {} papers of his corrected list".format(len(blocks), len(rows)),
              ""]
    BIB_FILE.parent.mkdir(parents=True, exist_ok=True)
    TEMP_PATH = BIB_FILE.with_suffix(".tmp")
    TEMP_PATH.write_text("\n".join(header) + "\n" + "\n\n".join(blocks) + "\n", encoding="utf-8")
    try:
        TEMP_PATH.replace(BIB_FILE)
    except PermissionError:
        sys.exit("Cannot write %s, it is probably open in another program. Close it and run again." % BIB_FILE)

    # --- summary
    print("")
    print("Records by source")
    for source, count in counts.most_common():
        print("   %-13s %4d" % (source, count))
    for numbers in merged:
        print("   one entry for his numbers %s, same DOI" % " and ".join(numbers))
    for row in unverified:
        print("   unverified, built from the citation: #%s %s" % (row["number"], row["citation"][:60]))
    print("")
    print("Written: %s" % BIB_FILE)
    print("   %d entries" % len(blocks))


# ================================================== #
# MAIN
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Build the bib file of Robert Wagner's AIDA publications from his corrected list.")
    parser.add_argument("--offline", action="store_true",
                        help="no internet: cached records, own harvests and our list only")
    parser.add_argument("--refresh", action="store_true", help="download every record again")
    return parser.parse_args()


def main():
    build_bib(parse_arguments())


if __name__ == "__main__":
    main()
