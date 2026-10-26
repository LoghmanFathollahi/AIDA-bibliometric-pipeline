'''
3_c_aida_author_packets.py

   Builds one review packet per AIDA author, so every scientist can check and
   correct their own entries without seeing or touching anybody else's.

Purpose

   The master bibliography is the single source of truth for paper level facts:
   does a paper belong to AIDA, is it peer reviewed, what is its DOI.
   Author level facts are different: the role on a paper, the affiliation at the
   time, and whether a name form is really that person. Only that person knows,
   and two people can never answer for the same slot.
   This script therefore keeps the master bib untouched and writes, per author,
   a small folder holding only that author's own data:

      AIDA_<Name>.html     the author profile page, with only their papers
      corrections.csv      one row per paper, empty answer columns to fill in
      missing_papers.txt   for DOIs or BibTeX entries of what is not listed
      README.txt           what to do, in a few lines

   Every author keeps a fixed id from authors_registry.csv. The id is assigned
   once and never reused, so a correction sent in March still attaches to the
   right person in December, even after a name merge or split.

   3_d_aida_merge_corrections.py reads the returned files back in.

Usage

   python 3_c_aida_author_packets.py
   python 3_c_aida_author_packets.py --min-papers 3        skip one paper authors
   python 3_c_aida_author_packets.py --only mohler_o       rebuild one packet
   python 3_c_aida_author_packets.py --kit-only            only KIT and IMK-AAF people
   python 3_c_aida_author_packets.py --zip                 one zip per author, ready to mail
   python 3_c_aida_author_packets.py --dry-run --verbose   list what would be written

   All folders come from config.py and are relative to the project root,
   so the script runs the same on Linux and Windows without editing paths.
   On Windows, close the packet files in Excel before a rebuild.

Output

   <root>/info/aut/review/authors_registry.csv     fixed author ids, never reused
   <root>/info/aut/review/_index.csv               tracking table: who, how many papers, sent, replied
   <root>/info/aut/review/<id>_<name>/             one folder per author, see above
   <root>/info/aut/review/<id>_<name>.zip          with --zip, the same folder as one archive
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import csv
import datetime
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import sys
import unicodedata
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
BASE_DIR = Path(getattr(config, "BASE_DIR", FALLBACK_ROOT))
MASTER_DIR = Path(getattr(config, "MASTER_BIB_DIR", BASE_DIR / "AIDA_master"))
INPUT_BIB_PATH = Path(getattr(config, "MASTER_BIB_FILE", MASTER_DIR / "AIDA_Master_bib.bib"))

# --- the author pages script is imported, so the parser and the page template are shared
AUTHORS_SCRIPT_PATH = Path(getattr(config, "AUTHORS_SCRIPT_FILE",
                                   SCRIPT_DIR / "3_b_aida_bib_authors.py"))
AUTHORS_OUTPUT_DIR = Path(getattr(config, "AUTHORS_HTML_DIR", BASE_DIR / "output" / "authors"))

# --- review folders
REVIEW_DIR = Path(getattr(config, "AUTHOR_REVIEW_DIR", BASE_DIR / "info" / "aut" / "review"))
REGISTRY_PATH = REVIEW_DIR / "authors_registry.csv"
INDEX_PATH = REVIEW_DIR / "_index.csv"

# --- packet settings
id_prefix = "aut"
id_digits = 4
min_papers_default = 1
contact_line = "Loghman Fathollahi, IMK-AAF, loghman.fathollahi@kit.edu"

# --- answers accepted in the correction file
yes_words = {"yes", "y", "true", "1", "correct", "ok", "richtig", "ja"}
no_words = {"no", "n", "false", "0", "wrong", "falsch", "nein"}

# --- role names as they appear in the packet
role_titles = [
    "First author and corresponding author",
    "First author, not corresponding",
    "Corresponding author, not first author",
    "Co-author",
]


# ================================================== #
# THE AUTHOR PAGES SCRIPT
# ================================================== #

def load_authors_module(SCRIPT_PATH):
    # 3_b_aida_bib_authors.py starts with a digit, so it is loaded by path
    if not SCRIPT_PATH.is_file():
        print("Author pages script not found: %s" % SCRIPT_PATH)
        print("Use --authors-script to give its path.")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("aida_bib_authors", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ================================================== #
# FOLDERS
# ================================================== #

def remove_folder(FOLDER_PATH):
    # Windows refuses to delete read only files, so the flag is cleared first
    # a file still open in Excel cannot be deleted at all, that is reported
    def clear_readonly(function, failed_path, _error):
        os.chmod(failed_path, stat.S_IWRITE)
        function(failed_path)

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(FOLDER_PATH, onexc=clear_readonly)
        else:
            shutil.rmtree(FOLDER_PATH, onerror=clear_readonly)
    except PermissionError as error:
        print("Cannot replace %s, a file in it is still open (%s)." % (FOLDER_PATH, error))
        print("Close it, for example in Excel, and run again.")
        sys.exit(1)


# ================================================== #
# NAMES AND IDS
# ================================================== #

def slug(text):
    # folder safe form of a display name: "Möhler, Ottmar" -> "moehler_ottmar"
    text = (text or "").replace("ß", "ss")
    for source, target in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"),
                           ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue")):
        text = text.replace(source, target)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text[:40] or "author"


def next_id(used_ids):
    # ids are handed out in order and never reused, even when an author disappears
    numbers = [int(item[len(id_prefix):]) for item in used_ids
               if item.startswith(id_prefix) and item[len(id_prefix):].isdigit()]
    return "%s%0*d" % (id_prefix, id_digits, (max(numbers) + 1) if numbers else 1)


def read_csv_rows(CSV_PATH):
    # a CSV edited in Excel on Windows may come back in cp1252 instead of
    # UTF-8, and with ; instead of , when Windows uses a German locale
    # so the encoding and the separator are both detected here
    if not CSV_PATH.is_file():
        return []
    try:
        raw_bytes = CSV_PATH.read_bytes()
    except OSError:
        return []
    text = ""
    for encoding_name in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw_bytes.decode(encoding_name)
            break
        except UnicodeDecodeError:
            continue
    header_line = text.splitlines()[0] if text else ""
    separator = max((",", ";", "	"), key=header_line.count)
    try:
        return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=separator))
    except csv.Error:
        return []


def write_csv(CSV_PATH, header, rows):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def value_of(row, *names):
    for name in names:
        for header, value in row.items():
            if header and header.strip().lower() == name:
                return (value or "").strip()
    return ""


# ================================================== #
# REGISTRY
# ================================================== #

registry_header = ["author_id", "author_key", "display_name", "aliases", "email",
                   "status", "first_seen", "last_seen", "notes"]


def load_registry(REGISTRY_PATH):
    # author_id is the fixed handle; author_key and the aliases may change over time
    entries = []
    for row in read_csv_rows(REGISTRY_PATH):
        author_id = value_of(row, "author_id")
        if not author_id:
            continue
        entries.append({
            "author_id": author_id,
            "author_key": value_of(row, "author_key"),
            "display_name": value_of(row, "display_name"),
            "aliases": [item.strip() for item in value_of(row, "aliases").split(";") if item.strip()],
            "email": value_of(row, "email"),
            "status": value_of(row, "status") or "active",
            "first_seen": value_of(row, "first_seen"),
            "last_seen": value_of(row, "last_seen"),
            "notes": value_of(row, "notes"),
        })
    return entries


def match_entry(entries, author, name_forms):
    # the key first, then any name form that an entry already lists
    for entry in entries:
        if entry["author_key"] and entry["author_key"] == author["k"]:
            return entry
    folded = {form.lower() for form in name_forms}
    for entry in entries:
        known = {entry["display_name"].lower()} | {item.lower() for item in entry["aliases"]}
        if folded & known:
            return entry
    return None


def update_registry(entries, data, today):
    # returns {author index: registry entry}; new authors get a new id
    used_ids = {entry["author_id"] for entry in entries}
    by_index = {}
    seen_ids = set()
    for index, author in enumerate(data["authors"]):
        name_forms = [author["n"]] + list(author.get("v") or [])
        entry = match_entry(entries, author, name_forms)
        if entry is None:
            entry = {"author_id": next_id(used_ids), "author_key": author["k"],
                     "display_name": author["n"], "aliases": [], "email": "",
                     "status": "active", "first_seen": today, "last_seen": today, "notes": ""}
            used_ids.add(entry["author_id"])
            entries.append(entry)
        # --- the key can change when aliases are edited, the id never does
        if entry["author_key"] != author["k"] and entry["author_key"]:
            entry["notes"] = ("key was %s; " % entry["author_key"]) + entry["notes"]
        entry["author_key"] = author["k"]
        entry["display_name"] = author["n"]
        for form in name_forms[1:]:
            if form and form not in entry["aliases"] and form != entry["display_name"]:
                entry["aliases"].append(form)
        entry["status"] = "active"
        entry["last_seen"] = today
        by_index[index] = entry
        seen_ids.add(entry["author_id"])

    # --- authors who left the bibliography keep their row and their id
    for entry in entries:
        if entry["author_id"] not in seen_ids and entry["status"] == "active":
            entry["status"] = "absent"
    return by_index


def save_registry(REGISTRY_PATH, entries):
    rows = [[entry["author_id"], entry["author_key"], entry["display_name"],
             "; ".join(entry["aliases"]), entry["email"], entry["status"],
             entry["first_seen"], entry["last_seen"], entry["notes"]]
            for entry in sorted(entries, key=lambda item: item["author_id"])]
    write_csv(REGISTRY_PATH, registry_header, rows)


# ================================================== #
# ONE AUTHOR SLICE OF THE DATA
# ================================================== #

def subset_data(module, data, author_index, page_name):
    # a copy of the data holding only the papers of one author, with all
    # indices renumbered, so the packet page shows nothing about anybody else
    papers = [paper for paper in data["papers"] if author_index in paper["a"]]

    author_ids, aff_ids, journal_ids, raw_ids = [], [], [], []

    def keep(values, value):
        if value not in values:
            values.append(value)
        return values.index(value)

    keep(author_ids, author_index)
    for paper in papers:
        for index in paper["a"]:
            keep(author_ids, index)
        for index in paper["pa"]:
            keep(aff_ids, index)
        for group in paper["aa"]:
            for index in group:
                keep(aff_ids, index)
        for pairs in paper.get("ar") or []:
            for pair in pairs:
                keep(aff_ids, pair[0])
                keep(raw_ids, pair[1])
        if paper["j"] >= 0:
            keep(journal_ids, paper["j"])

    author_map = {old: new for new, old in enumerate(author_ids)}
    aff_map = {old: new for new, old in enumerate(aff_ids)}
    journal_map = {old: new for new, old in enumerate(journal_ids)}
    raw_map = {old: new for new, old in enumerate(raw_ids)}

    new_papers = []
    for paper in papers:
        item = dict(paper)
        item["a"] = [author_map[index] for index in paper["a"]]
        item["c"] = [author_map[index] for index in paper["c"] if index in author_map]
        item["aa"] = [[aff_map[index] for index in group] for group in paper["aa"]]
        item["pa"] = [aff_map[index] for index in paper["pa"]]
        item["ar"] = [[[aff_map[pair[0]], raw_map[pair[1]]] for pair in pairs]
                      for pairs in (paper.get("ar") or [])]
        item["j"] = journal_map[paper["j"]] if paper["j"] >= 0 else -1
        new_papers.append(item)

    return {
        "papers": new_papers,
        "authors": [data["authors"][index] for index in author_ids],
        "affs": [data["affs"][index] for index in aff_ids],
        "journals": [data["journals"][index] for index in journal_ids],
        "raws": [data["raws"][index] for index in raw_ids],
        # the other pages are not in the packet, the links fall back to plain text
        "pages": {"authors": page_name, "affs": "", "map": ""},
        "focus": data["authors"][author_index]["k"],
        "source": data["source"], "built": data["built"],
    }


def author_page(module, data, author_index, entry, page_name):
    # the same profile page as AIDA_Authors.html, opened on this one author
    author = data["authors"][author_index]
    small = subset_data(module, data, author_index, page_name)
    note = ('<nav class="pages"><a href="corrections.csv">corrections.csv</a>'
            '<a href="missing_papers.txt">missing_papers.txt</a>'
            '<a href="README.txt">README.txt</a></nav>')
    replacements = {
        "__PAGE_TITLE__": "AIDA publications of %s" % module.html.escape(author["n"]),
        "__NAV__": note,
        "__SUMMARY_TEXT__": ("Review packet %s. %s, %s. Only your own papers are shown here. "
                             "Please check them and write the corrections into corrections.csv."
                             % (module.html.escape(entry["author_id"]),
                                module.html.escape(author["n"]),
                                "%d %s" % (len(small["papers"]),
                                           "paper" if len(small["papers"]) == 1 else "papers"))),
        "__SOURCE_NAME__": module.html.escape(data["source"]),
        "__BUILD_DATE__": data["built"],
        "__CORR_COVERAGE__": module.percent_text(
            sum(1 for paper in small["papers"] if paper["ck"]), len(small["papers"])),
        "__AFF_COVERAGE__": "",
        "__DATA_JSON__": module.data_json(small),
    }
    return module.fill_template(module.AUTHORS_TEMPLATE, replacements)


# ================================================== #
# THE CORRECTION FILE
# ================================================== #

corrections_header = [
    "author_id", "author_key", "author_name",
    "doi", "paper_key", "year", "type", "journal", "title",
    "listed_role", "role_correct", "correct_role",
    "listed_affiliation", "affiliation_correct", "correct_affiliation",
    "this_is_me", "belongs_to_aida", "peer_reviewed", "comment",
]


def corrections_rows(module, data, author_index, entry):
    rows = []
    papers = [paper for paper in data["papers"] if author_index in paper["a"]]
    papers.sort(key=lambda paper: (-(paper["y"] or 0), paper["tp"]))
    author = data["authors"][author_index]
    for paper in papers:
        position = paper["a"].index(author_index)
        role = module.role_index(position, author_index, set(paper["c"]))
        affiliations = "; ".join(data["affs"][index]["n"] for index in paper["aa"][position])
        rows.append([
            entry["author_id"], author["k"], author["n"],
            paper["doi"], paper["k"], paper["y"] or "", paper["ty"],
            data["journals"][paper["j"]] if paper["j"] >= 0 else "",
            paper["tp"],
            role_titles[role], "", "",
            affiliations or "(not known)", "", "",
            "", "", "", "",
        ])
    return rows


# ================================================== #
# THE PACKET TEXTS
# ================================================== #

readme_text = '''AIDA publication list, review packet for {name}
Packet id {author_id}, built {built} from {source}

WHAT THIS IS

  We are building one complete list of all publications of the AIDA cloud
  chamber. This folder holds only your own entries. Please check them and
  send the folder back. It takes about ten minutes.

HOW TO CHECK

  1. Open {page} in any web browser. No installation and no internet
     connection are needed.
     It shows your papers per year, your role on each one, your affiliations
     and your co-authors.

  2. Open corrections.csv in Excel or LibreOffice. There is one row per paper.
     The columns starting with "listed_" show what we have now.
     Leave a line empty when it is correct. Fill in only what is wrong:

       role_correct          no, if the role in "listed_role" is wrong
       correct_role          then write the right one, see the list below
       affiliation_correct   no, if "listed_affiliation" is wrong
       correct_affiliation   then write the right institute for that paper
       this_is_me            no, if this paper is not yours at all,
                             for example another person with your surname
       belongs_to_aida       no, if the paper has nothing to do with AIDA,
                             or if the row is a discussion page, a supplement
                             or a dataset record instead of the article
       peer_reviewed         no, for conference contributions and reports
       comment               anything else you want to tell us

     The four possible roles are
       {roles}

  3. Open missing_papers.txt and add every AIDA paper of yours that is not in
     the list. One DOI per line is enough. You can also paste whole BibTeX
     entries exported from your reference manager.

  4. Send corrections.csv and missing_papers.txt back to
     {contact}

NOTES

  "Role" is counted per paper and the four roles add up to your paper total.
  First author means the first name in the author list, corresponding author
  means the contact author of the paper.

  Corresponding authors are only known where Web of Science or OpenAlex give
  them, which is mostly for journal articles. For older papers this may be
  missing, and the paper then shows as co-author. Please correct those.

  Your answers about your own role and your own affiliation are applied
  directly. Answers about whether a paper belongs to AIDA are collected from
  everyone and decided together, because several authors share one paper.

  Many thanks,
  {contact}
'''

missing_text = '''# Missing AIDA papers of {name}
# Packet {author_id}, built {built}
#
# Add every AIDA paper of yours that is not in {page}.
# One DOI per line is enough, for example
#
#   10.5194/acp-16-2025-2016
#
# You can also paste whole BibTeX entries exported from your reference
# manager. Both forms work, and you can mix them.
#
# Lines starting with # are ignored.
# A short note after the DOI is welcome, for example
#
#   10.1029/2020JD032808   uses the APC chamber
#
# ---------------------------------------------------------------------

'''


# ================================================== #
# DATASET
# ================================================== #

# --- affiliations counted as "our institute" for --kit-only
home_patterns = [
    r"karlsruhe inst", r"karlsruher inst", r"\bkit\b",
    r"forschungszentrum karlsruhe", r"imk", r"institute of meteorology and climate",
]


def build_data(module, arguments):
    # the same pipeline as 3_b_aida_bib_authors.py, without the network and the maps
    INPUT_PATH = Path(arguments.input)
    if not INPUT_PATH.is_file():
        print("Input file not found: %s" % INPUT_PATH)
        sys.exit(1)
    AUTHORS_DIR = Path(arguments.authors_dir)
    CACHE_DIR = AUTHORS_DIR / "cache"

    print("Reading %s" % INPUT_PATH)
    entries = module.parse_bibtex(INPUT_PATH.read_text(encoding="utf-8", errors="replace"),
                                  arguments.verbose)
    records = [module.build_base_record(entry) for entry in entries]
    print("  %d entries" % len(records))
    if not records:
        print("No entries found, nothing to do.")
        sys.exit(1)

    # --- the caches written by 3_b are reused, no request is made here
    works_cache = module.load_cache(CACHE_DIR / "openalex_works.json")
    institution_cache = module.load_cache(CACHE_DIR / "openalex_institutions.json")
    if not works_cache:
        print("  no OpenAlex cache in %s, roles come from the bib file only" % CACHE_DIR)

    alias_keys, forced_names = module.load_author_aliases(AUTHORS_DIR / "author_aliases.csv")
    author_registry = module.AuthorRegistry(alias_keys, forced_names)
    affiliation_registry = module.AffiliationRegistry(
        module.load_affiliation_aliases(AUTHORS_DIR / "affiliation_aliases.csv"))
    overrides = module.load_corresponding_overrides(AUTHORS_DIR / "corresponding_overrides.csv")
    if overrides:
        print("  %d corresponding author corrections already applied" % len(overrides))

    papers, journals, raw_texts, stats = module.build_dataset(
        records, works_cache, institution_cache, author_registry, affiliation_registry, overrides)
    remap = affiliation_registry.merge_by_ror()
    data = module.finalize_data(papers, journals, raw_texts, author_registry,
                                affiliation_registry, remap, INPUT_PATH.name)
    print("  %d papers, %d authors, %d affiliations"
          % (len(data["papers"]), len(data["authors"]), len(data["affs"])))
    return data


def author_paper_count(data):
    counts = {}
    for paper in data["papers"]:
        for author_index in paper["a"]:
            counts[author_index] = counts.get(author_index, 0) + 1
    return counts


def is_home_author(data, author_index):
    # True when at least one of this author's affiliations is KIT or its predecessor
    for paper in data["papers"]:
        if author_index not in paper["a"]:
            continue
        position = paper["a"].index(author_index)
        for index in paper["aa"][position]:
            name = data["affs"][index]["n"].lower()
            if any(re.search(pattern, name) for pattern in home_patterns):
                return True
    return False


# ================================================== #
# WRITING THE PACKETS
# ================================================== #

index_header = ["author_id", "author_name", "author_key", "email", "papers", "first_year",
                "last_year", "folder", "built", "sent_on", "replied_on", "status", "notes"]


def load_index(INDEX_PATH):
    # the tracking table is kept whole, so sent_on, replied_on and the rows of
    # authors that are not rebuilt this time all survive
    kept = {}
    for row in read_csv_rows(INDEX_PATH):
        author_id = value_of(row, "author_id")
        if author_id:
            kept[author_id] = {"row": [value_of(row, name) for name in index_header],
                               "sent_on": value_of(row, "sent_on"),
                               "replied_on": value_of(row, "replied_on"),
                               "status": value_of(row, "status"),
                               "notes": value_of(row, "notes")}
    return kept


def write_packet(module, data, author_index, entry, PACKET_DIR, today, write_enabled):
    author = data["authors"][author_index]
    page_name = "AIDA_%s.html" % slug(author["n"])
    rows = corrections_rows(module, data, author_index, entry)
    years = [paper["y"] for paper in data["papers"]
             if author_index in paper["a"] and paper["y"]]
    if not write_enabled:
        return page_name, len(rows), years

    if PACKET_DIR.is_dir():
        # a rebuild replaces the packet; returned files live in the corrections folder
        remove_folder(PACKET_DIR)
    PACKET_DIR.mkdir(parents=True, exist_ok=True)

    (PACKET_DIR / page_name).write_text(
        author_page(module, data, author_index, entry, page_name), encoding="utf-8")
    write_csv(PACKET_DIR / "corrections.csv", corrections_header, rows)
    fields = {"name": author["n"], "author_id": entry["author_id"], "built": data["built"],
              "source": data["source"], "page": page_name, "contact": contact_line,
              "roles": "\n       ".join(role_titles)}
    (PACKET_DIR / "README.txt").write_text(readme_text.format(**fields), encoding="utf-8")
    (PACKET_DIR / "missing_papers.txt").write_text(missing_text.format(**fields), encoding="utf-8")
    return page_name, len(rows), years


# ================================================== #
# ARGUMENTS
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="One review packet per AIDA author, built from the master bib file.")
    parser.add_argument("--input", default=str(INPUT_BIB_PATH), help="master BibTeX file")
    parser.add_argument("--authors-dir", default=str(AUTHORS_OUTPUT_DIR),
                        help="folder of 3_b: caches, alias files and corresponding_overrides.csv")
    parser.add_argument("--authors-script", default=str(AUTHORS_SCRIPT_PATH),
                        help="path of 3_b_aida_bib_authors.py")
    parser.add_argument("--review-dir", default=str(REVIEW_DIR), help="folder for the packets")
    parser.add_argument("--min-papers", type=int, default=min_papers_default,
                        help="skip authors with fewer papers")
    parser.add_argument("--only", action="append", default=[],
                        help="build only this author key or id, can be repeated")
    parser.add_argument("--kit-only", action="store_true",
                        help="only authors with a KIT or Forschungszentrum Karlsruhe affiliation")
    parser.add_argument("--zip", action="store_true", help="also write one zip per packet")
    parser.add_argument("--dry-run", action="store_true", help="run everything, write nothing")
    parser.add_argument("--verbose", action="store_true", help="more details while running")
    return parser.parse_args()


# ================================================== #
# MAIN
# ================================================== #

def main():
    arguments = parse_arguments()
    module = load_authors_module(Path(arguments.authors_script))
    REVIEW_PATH = Path(arguments.review_dir)
    REGISTRY_FILE = REVIEW_PATH / "authors_registry.csv"
    INDEX_FILE = REVIEW_PATH / "_index.csv"
    write_enabled = not arguments.dry_run
    today = datetime.date.today().isoformat()

    data = build_data(module, arguments)

    # --- fixed ids, assigned once and never reused
    entries = load_registry(REGISTRY_FILE)
    known_before = len(entries)
    by_index = update_registry(entries, data, today)
    print("Author registry: %d rows (%d new)" % (len(entries), len(entries) - known_before))

    # --- who gets a packet
    counts = author_paper_count(data)
    wanted = {item.strip().lower() for item in arguments.only}
    selected = []
    for index, author in enumerate(data["authors"]):
        entry = by_index[index]
        if wanted and not ({author["k"].lower(), entry["author_id"].lower()} & wanted):
            continue
        if not wanted:
            if counts.get(index, 0) < arguments.min_papers:
                continue
            if arguments.kit_only and not is_home_author(data, index):
                continue
        selected.append((index, entry))
    selected.sort(key=lambda pair: -counts.get(pair[0], 0))
    print("Packets to build: %d" % len(selected))
    if not selected:
        print("Nothing selected. Check --only, --min-papers or --kit-only.")
        return

    kept = load_index(INDEX_FILE)
    # rows of authors that are not rebuilt this time are carried over unchanged
    index_rows = {author_id: item["row"] for author_id, item in kept.items()}
    for author_index, entry in selected:
        author = data["authors"][author_index]
        folder_name = "%s_%s" % (entry["author_id"], slug(author["n"]))
        PACKET_DIR = REVIEW_PATH / folder_name
        page_name, paper_count, years = write_packet(
            module, data, author_index, entry, PACKET_DIR, today, write_enabled)
        if write_enabled and arguments.zip:
            shutil.make_archive(str(REVIEW_PATH / folder_name), "zip",
                                root_dir=str(REVIEW_PATH), base_dir=folder_name)
        previous = kept.get(entry["author_id"], {})
        index_rows[entry["author_id"]] = [
            entry["author_id"], author["n"], author["k"], entry["email"], paper_count,
            min(years) if years else "", max(years) if years else "",
            folder_name, today, previous.get("sent_on", ""), previous.get("replied_on", ""),
            previous.get("status", "not sent"), previous.get("notes", ""),
        ]
        if arguments.verbose:
            print("  %-10s %-32s %3d papers" % (entry["author_id"], author["n"][:32], paper_count))

    if not write_enabled:
        print("")
        print("Dry run, nothing written. Packets would go to %s" % REVIEW_PATH)
        return

    save_registry(REGISTRY_FILE, entries)
    write_csv(INDEX_FILE, index_header,
              sorted(index_rows.values(), key=lambda row: (-int(row[4] or 0), row[0])))
    print("")
    print("Written to %s" % REVIEW_PATH)
    print("  authors_registry.csv   %d authors with a fixed id" % len(entries))
    print("  _index.csv             tracking table, fill in sent_on and replied_on")
    print("  %d packet folders%s" % (len(selected), ", each also zipped" if arguments.zip else ""))


if __name__ == "__main__":
    main()
