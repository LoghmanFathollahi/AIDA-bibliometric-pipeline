'''
2_b_aida_robert_check.py

   Applies Robert Wagner's notes to his publication list and completes the
   DOI of every paper in the corrected list.

Purpose

   2_a_aida_robert_list.py wrote his list as we sent it to him (53 entries).
   He answered with free text notes on that list. This script reads both and
   produces his corrected list:

      remove     the entries he names: discussion pages, supplements,
                 datasets, early versions of papers whose final article is
                 in the list. Each note must point at exactly one entry,
                 otherwise nothing is removed and the note is marked "check".
      add        the papers he calls missing. A paper that is in fact already
                 in his list is not added twice. The DOI is taken from
                 Ottmar's DOI list when the paper is there.
      question   decided in config.py: robert_keep_proceedings
      APC        decided in config.py: robert_keep_apc_papers
      role       not a change of the list; his candidate papers are named
                 in the notes table, for the author pages later

   Then every paper without a DOI is searched, as for Ottmar: first the own
   harvests, then Crossref. A match must have the title in common and Robert
   among its authors; the year must agree when it is known.

   The search helpers are loaded from 2_b_aida_ottmar_check.py, so both
   authors are matched by the same rules.

   A DOI written by hand into the doi column with method "manual" is kept
   unchanged in every later run. Crossref answers are cached.

Usage

   python 2_b_aida_robert_check.py
      apply his notes and complete the DOI list

   python 2_b_aida_robert_check.py --no-crossref
      no internet: list, notes, Ottmar's DOI list, harvests and cache only

   python 2_b_aida_robert_check.py --refresh
      ask Crossref again, also for entries already in the cache

   Run 2_a_aida_robert_list.py first; his notes go into
   <root>/data/author_self_correction/raw/. All paths come from config.py,
   the same on Linux and Windows.

Output

   <root>/data/author_self_correction/Robert_Wagner_AIDA_dois.csv      his corrected list, one row
                                                                       per paper, removed rows marked
   <root>/data/author_self_correction/Robert_Wagner_corrections.csv    one row per note, with what
                                                                       was done with it
   <root>/data/author_self_correction/cache/crossref_robert.json      cached Crossref answers
   a summary on the screen
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import csv
import importlib.util
import io
import json
import os
import re
import sys
import unicodedata
import urllib.error
from collections import Counter
from pathlib import Path

try:
    from docx import Document
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
DATA_DIR = Path(getattr(config, "DATA_DIR", FALLBACK_ROOT / "data"))
SELF_CORRECTION_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_DIR", DATA_DIR / "author_self_correction"))
RAW_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_RAW_DIR", SELF_CORRECTION_DIR / "raw"))
LIST_DOCX = Path(getattr(config, "ROBERT_AIDA_FILE", SELF_CORRECTION_DIR / "Robert_Wagner_AIDA_publications.docx"))
NOTES_DOCX = Path(getattr(config, "ROBERT_NOTES_FILE", RAW_DIR / "Robert_Wagner_publications.docx"))
CORRECTIONS_CSV = Path(getattr(config, "ROBERT_CORRECTIONS_FILE", SELF_CORRECTION_DIR / "Robert_Wagner_corrections.csv"))
DOI_CSV = Path(getattr(config, "ROBERT_DOI_FILE", SELF_CORRECTION_DIR / "Robert_Wagner_AIDA_dois.csv"))
OTTMAR_DOI_CSV = Path(getattr(config, "OTTMAR_DOI_FILE", SELF_CORRECTION_DIR / "Ottmar_Moehler_AIDA_dois.csv"))
CACHE_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_CACHE_DIR", SELF_CORRECTION_DIR / "cache"))
CROSSREF_CACHE = CACHE_DIR / "crossref_robert.json"
HARVEST_DIRS = [Path(HARVEST_DIR) for HARVEST_DIR in getattr(
    config, "BIB_SOURCE_DIRS", [DATA_DIR / "openalex", DATA_DIR / "webofscience"])]
# the DOI search rules are shared with Ottmar's step
OTTMAR_CHECK_SCRIPT = SCRIPT_DIR / "2_b_aida_ottmar_check.py"

# --- decisions, see config.py
keep_proceedings = bool(getattr(config, "robert_keep_proceedings", True))
keep_apc_papers = bool(getattr(config, "robert_keep_apc_papers", True))
decision_date = "2026-10-14"

# --- matching
file_pattern = getattr(config, "robert_file_pattern", "Robert*Wagner*.docx")
author_surname = "wagner"
title_accept = float(getattr(config, "doi_title_accept", 0.90))
title_check = float(getattr(config, "doi_title_check", 0.80))

journal_names = {
    "ACP": "Atmospheric Chemistry and Physics",
    "AMT": "Atmospheric Measurement Techniques",
    "AR": "Aerosol Research",
    "GMD": "Geoscientific Model Development",
    "ESSD": "Earth System Science Data",
}


# --- words that mark a removal, and the reason each kind of removal gets
remove_words = ["remove", "delete", "not a peer-reviewed", "not peer-reviewed",
                "should not be listed"]

reason_rules = [
    ("supplement", "supplement, not a separate paper"),
    ("dataset", "dataset, not a paper"),
    ("discussion", "discussion page, the final paper is in the list"),
    ("proceedings", "conference proceedings, a peer reviewed paper exists"),
    ("peer-reviewed", "not peer reviewed, the real article is in the list"),
    ("real", "earlier version, the real paper is in the list"),
]


# --- table layouts
doi_columns = ["number", "doi", "method", "status", "change", "reason", "score", "year",
               "first_author", "matched_title", "citation", "note"]
notes_columns = ["item", "action", "year", "first_author", "journal", "title", "reason",
                 "note", "ottmar_number", "doi", "applied", "his_text"]


# ================================================== #
# SHARED SEARCH RULES
# ================================================== #

def load_shared():
    # 2_b_aida_ottmar_check.py starts with a digit, so it is loaded by path
    if not OTTMAR_CHECK_SCRIPT.is_file():
        sys.exit("Needed next to this script: %s" % OTTMAR_CHECK_SCRIPT.name)
    spec = importlib.util.spec_from_file_location("aida_ottmar_check", OTTMAR_CHECK_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_shared()


# ================================================== #
# TEXT HELPERS
# ================================================== #

def squash(text):
    # letters and digits only, lower case ASCII, used to compare titles
    # British -isation and American -ization count as the same word
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(char for char in text if not unicodedata.combining(char)).lower()
    text = re.sub(r"is(ation|ing|ed|e)\b", r"iz\1", text)
    return re.sub(r"[^a-z0-9]", "", text)


def citation_year(citation):
    # the publication year of a citation, never a page number or a part of a DOI:
    # "(2005)" first, then ", 2021." at the end, then the first year outside DOI and URL
    text = re.sub(r"\S*10\.\d{4,9}/\S+|https?://\S+", " ", citation or "")
    match = (re.search(r"\((19\d\d|20\d\d)\)", text)
             or re.search(r",\s*(19\d\d|20\d\d)\.?\s*$", text))
    if match:
        return match.group(1)
    found = years_in(text)
    return found[0] if found else ""


def years_in(text):
    return re.findall(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)", text or "")


def quoted(text):
    # the longest text in curly or straight quotes, without the trailing dots
    # one word quotes such as “other” or “real” are only emphasis and skipped
    parts = re.findall(r"[“\"]([^”\"]+)[”\"]", text or "")
    parts = [part.strip(" .…") for part in parts if len(part.split()) > 1]
    return max(parts, key=len) if parts else ""


def reason_for(text):
    lower = text.lower()
    for word, reason in reason_rules:
        if word in lower:
            return reason
    return "listed as wrong by the author"


def read_csv_rows(CSV_PATH):
    # a table saved by Excel on Windows may be cp1252 and use ; as separator
    if not CSV_PATH.is_file():
        return []
    raw_bytes = CSV_PATH.read_bytes()
    text = ""
    for encoding_name in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw_bytes.decode(encoding_name)
            break
        except UnicodeDecodeError:
            continue
    header_line = text.splitlines()[0] if text else ""
    separator = max((",", ";", "\t"), key=header_line.count)
    return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=separator))



# ================================================== #
# READING HIS LIST
# ================================================== #

def read_list(LIST_PATH):
    # the numbered citations written by 2_a_aida_robert_list.py
    #   53. Zanatta, M.; ...; Wagner, R. (2025), Title, Journal, ..., https://doi.org/... [Other]
    entries = []
    for paragraph in Document(str(LIST_PATH)).paragraphs:
        text = " ".join(paragraph.text.split())
        match = re.match(r"^(\d{1,4})\.\s+(.*)$", text)
        if not match:
            continue
        citation = match.group(2)
        kind = re.search(r"\[([^\]]+)\]$", citation)
        entries.append({
            "number": int(match.group(1)),
            "citation": citation,
            "doi": shared.clean_doi(citation),
            "year": citation_year(citation),
            "first_author": citation.split(";")[0].split(",")[0].strip(),
            "type": kind.group(1) if kind else "Article",
            "change": "kept", "reason": "", "note": "",
        })
    return entries


# ================================================== #
# READING HIS NOTES
# ================================================== #

def find_notes(given_path):
    # --input-notes wins, then the name in config.py, then any file matching the pattern
    if given_path:
        if not given_path.is_file():
            sys.exit("File not found: %s" % given_path)
        return given_path
    if NOTES_DOCX.is_file():
        return NOTES_DOCX
    candidates = []
    if RAW_DIR.is_dir():
        candidates = [FILE_PATH for FILE_PATH in sorted(RAW_DIR.glob(file_pattern))
                      if not FILE_PATH.name.startswith("~$")]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        sys.exit("Robert's notes were not found. Put them in:\n   %s\nor give them with --notes" % RAW_DIR)
    sys.exit("More than one possible file in %s:\n   %s\nKeep one, or choose it with --notes"
             % (RAW_DIR, "\n   ".join(FILE_PATH.name for FILE_PATH in candidates)))


def read_lines(INPUT_PATH):
    # one item per line; a paragraph may hold several lines with soft line breaks
    lines = []
    for paragraph in Document(str(INPUT_PATH)).paragraphs:
        for line in paragraph.text.split("\n"):
            line = " ".join(line.split())
            if line:
                lines.append(line)
    return lines


def parse_missing(line):
    # "Missing paper (uses APC chamber): ACP - Title"  and the forms copied from web pages:
    #   "Title - Author - 2024 - Journal - Wiley Online Library"
    #   "Title | Nature Geoscience"
    #   "Title-Web of Science Core Collection"
    row = {"action": "add", "reason": "missing in our list"}
    head, _, text = line.partition(":")
    note = re.search(r"\(([^)]*)\)", head)
    if note:
        row["note"] = note.group(1)
    text = text.strip()

    short = re.match(r"^([A-Z]{2,5}) - (.*)$", text)
    if short and short.group(1) in journal_names:
        row["journal"], text = journal_names[short.group(1)], short.group(2)

    wiley = re.match(r"^(.*) - ([^-]+?) - (\d{4}) - (.*?) - Wiley Online Library$", text)
    if wiley:
        text = wiley.group(1)
        row.update(first_author=wiley.group(2).strip(), year=wiley.group(3), journal=wiley.group(4).strip())
    elif " | " in text:
        text, journal = text.rsplit(" | ", 1)
        row["journal"] = journal.strip()
    text = re.sub(r"\s*-\s*Web of Science Core Collection$", "", text)
    row["title"] = text.strip()
    return row


def parse_target(line, pending):
    # the entry that a "following link" sentence or the dataset sentence points at
    #   "2025: AR - Peer review - Title"   or   "Datasets from MIPAS ..."
    row = {"action": "remove", "reason": pending["reason"], "year": pending.get("year", ""),
           "first_author": pending.get("first_author", "")}
    year = re.match(r"^(\d{4}):\s*(.*)$", line)
    text = line
    if year:
        row["year"], text = year.group(1), year.group(2)
    short = re.match(r"^([A-Z]{2,5}) - (?:Peer review - )?(.*)$", text)
    if short and short.group(1) in journal_names:
        row["journal"], text = journal_names[short.group(1)], short.group(2)
    row["title"] = text.strip(" .…")
    return row


def sort_lines(lines):
    rows = []
    pending = None

    def add(row, line):
        row["his_text"] = line
        rows.append(row)

    for line in lines:
        lower = line.lower()

        # --- a line right after "the following link ..." is the entry to remove
        if pending is not None:
            add(parse_target(line, pending), pending["text"] + " / " + line)
            pending = None
            continue

        # --- a note in brackets belongs to the row above
        if line.startswith("(") and line.endswith(")") and rows:
            rows[-1]["note"] = "; ".join(part for part in (rows[-1].get("note"), line.strip("()")) if part)
            rows[-1]["his_text"] += " / " + line
            continue

        # --- "This should be removed ..." only confirms the row above
        if lower.startswith("this should be removed") and rows:
            rows[-1]["his_text"] += " / " + line
            continue

        if lower.startswith("missing paper"):
            add(parse_missing(line), line)
            continue

        # --- the next line is the entry to remove
        if "following link" in lower or ("included a link to" in lower and "dataset" in lower):
            pending = {"text": line, "reason": reason_for(line)}
            link_year = re.search(r"link in (\d{4})", line)
            who = re.search(r"\(([A-ZÄÖÜ][\w-]+) in (\d{4})\)", line)
            if link_year:
                pending["year"] = link_year.group(1)
            if who:
                pending["first_author"], pending["year"] = who.group(1), who.group(2)
            continue

        # --- his role: one row per year he names
        if "corresponding" in lower and "co-author" in lower:
            for year in years_in(line):
                add({"action": "role", "year": year,
                     "reason": "listed as Corresponding, not first; he is co-author only",
                     "note": "new role: Co-author"}, line)
            continue

        # --- a question for us
        if "?" in line:
            add({"action": "question", "year": ", ".join(years_in(line)), "reason": "decision needed",
                 "title": line.split("?")[0].strip() + "?"}, line)
            continue

        # --- a removal named in the same line
        if any(word in lower for word in remove_words):
            author = (re.search(r"([A-ZÄÖÜ][\w-]+) et al", line)
                      or re.search(r"\d{4} ([A-ZÄÖÜ][\w-]+) paper", line))
            url = re.search(r"https?://\S+", line)
            add({"action": "remove", "year": (years_in(line) or [""])[0],
                 "first_author": author.group(1) if author else "",
                 "title": quoted(line) or (url.group(0) if url else ""),
                 "reason": reason_for(line)}, line)
            continue

        # --- nothing fits: kept for a look by hand
        add({"action": "check", "reason": "not understood, read his text"}, line)

    if pending is not None:
        add({"action": "check", "reason": "the entry this line points at is missing"}, pending["text"])
    return rows



# ================================================== #
# APPLYING HIS NOTES
# ================================================== #

def find_in_list(note, entries, use_year=True):
    # the entries of his list that a note points at; ideally exactly one
    reason = note.get("reason", "")
    author = squash(note.get("first_author", ""))
    if reason.startswith("supplement"):
        found = [entry for entry in entries if "supplement" in entry["citation"].lower()]
    else:
        key = squash(note.get("title", ""))
        if len(key) < 12:
            return []
        found = [entry for entry in entries if key[:60] in squash(entry["citation"])]
        if not found:
            found = [entry for entry in entries if key[:30] in squash(entry["citation"])
                     and (not author or author in squash(entry["first_author"]))]

    # --- narrow down step by step, but never to nothing
    def narrow(test):
        nonlocal found
        if len(found) > 1:
            kept = [entry for entry in found if test(entry)]
            found = kept or found

    if not reason.startswith("supplement"):
        narrow(lambda entry: "supplement" not in entry["citation"].lower())
    if author:
        narrow(lambda entry: author in squash(entry["first_author"]))
    if use_year and note.get("year"):
        narrow(lambda entry: entry["year"] == note["year"])
    if reason.startswith("discussion"):
        narrow(lambda entry: entry["type"] != "Article")
        narrow(lambda entry: shared.is_discussion(entry["doi"]))
    return found


def apply_notes(entries, notes):
    # returns the added rows; entries and notes are changed in place
    added = []
    next_number = max(entry["number"] for entry in entries) + 1

    # --- removals first: an entry he removes, such as a dataset whose title holds the
    # title of the real paper, must not count as that paper when the additions are checked
    order = {"remove": 0, "question": 1, "add": 2, "role": 3}
    for note in sorted(notes, key=lambda note: order.get(note["action"], 4)):
        action = note["action"]

        if action == "remove":
            found = [entry for entry in find_in_list(note, entries) if entry["change"] != "removed"]
            if len(found) == 1:
                found[0]["change"] = "removed"
                found[0]["reason"] = note["reason"]
                note["applied"] = "removed #%d" % found[0]["number"]
            elif not found:
                note["applied"] = "check: no entry of his list matches"
            else:
                note["applied"] = "check: several entries match, nothing removed (%s)" % (
                    ", ".join("#%d" % entry["number"] for entry in found))

        elif action == "question":
            # his proceedings: entries of those years that are not articles or have no DOI
            years = years_in(note.get("year", ""))
            targets = [entry for entry in entries if entry["year"] in years and entry["change"] == "kept"
                       and (entry["type"] != "Article" or not entry["doi"])]
            for entry in targets:
                if keep_proceedings:
                    entry["change"] = "kept_by_decision"
                    entry["reason"] = "proceedings, kept by decision of %s" % decision_date
                else:
                    entry["change"] = "removed"
                    entry["reason"] = "proceedings, removed by decision of %s" % decision_date
            note["applied"] = "%s: %s" % ("kept" if keep_proceedings else "removed",
                                          ", ".join("#%d" % entry["number"] for entry in targets) or "none found")

        elif action == "add":
            present = [entry for entry in find_in_list(note, entries, use_year=False)
                       if entry["change"] != "removed"]
            apc = "apc" in (note.get("note") or "").lower()
            if present:
                note["applied"] = "already in his list as #%d, not added" % present[0]["number"]
            elif apc and not keep_apc_papers:
                note["applied"] = "not added, APC papers excluded by decision of %s" % decision_date
            else:
                parts = ["%s (%s)" % (note.get("first_author") or "", note.get("year") or ""),
                         note.get("title", ""), note.get("journal", "")]
                row = {"number": next_number, "doi": note.get("doi", ""),
                       "citation": ", ".join(part for part in parts if part.strip(" ()")),
                       "year": note.get("year", ""), "first_author": note.get("first_author", ""),
                       "type": "Article", "change": "added",
                       "reason": "missing, named by Robert" + (", APC chamber, kept by decision of %s"
                                                                % decision_date if apc else ""),
                       "note": note.get("note", ""),
                       "method": "ottmar_list" if note.get("doi") else ""}
                added.append(row)
                note["applied"] = "added as #%d" % next_number
                next_number += 1

        elif action == "role":
            # the list has no roles; his papers of that year where he is not first author
            candidates = [entry for entry in entries if entry["year"] == note.get("year")
                          and squash(entry["first_author"]) != author_surname]
            note["applied"] = "role, candidates: %s" % (
                ", ".join("#%d" % entry["number"] for entry in candidates) or "none")

        else:
            note["applied"] = "read by hand"
    return added


# ================================================== #
# OTTMAR'S LIST
# ================================================== #

def link_to_ottmar(notes):
    # a missing paper whose title appears in one of Ottmar's citations gets his number and DOI
    ottmar_rows = read_csv_rows(OTTMAR_DOI_CSV)
    if not ottmar_rows:
        print("   Ottmar's DOI list not found, run 2_b_aida_ottmar_check.py first: %s" % OTTMAR_DOI_CSV)
        return
    citations = [(squash(row.get("citation", "")), row) for row in ottmar_rows]
    for note in notes:
        if note["action"] != "add" or len(squash(note.get("title", ""))) < 20:
            continue
        # 60 letters of the title, or 30 letters and the first author, as some citations are short
        key = squash(note["title"])
        author = squash(note.get("first_author", ""))
        match = next((row for citation, row in citations if key[:60] in citation), None)
        if match is None:
            match = next((row for citation, row in citations
                          if key[:30] in citation and (not author or author in citation)), None)
        if match:
            note["ottmar_number"] = match.get("number", "")
            note["doi"] = match.get("doi", "")
            note["year"] = note.get("year") or citation_year(match.get("citation", ""))
            note["first_author"] = note.get("first_author") or match.get("first_author", "")


# ================================================== #
# COMPLETING THE DOIS
# ================================================== #

def judge(row, title, years, surnames):
    # Robert must be an author, the title must be in the citation, the year must agree when known
    score = shared.title_cover(title, row["citation"])
    is_his = author_surname in {shared.fold(name) for name in surnames if name}
    year = int(row["year"]) if str(row.get("year") or "").isdigit() else None
    year_exact = year is None or year in years
    year_near = year is None or any(abs(year - other) <= 1 for other in years)
    if score >= title_accept and is_his and year_exact:
        return "found", score
    if score >= title_check and is_his and year_near:
        return "check", score
    return "", score


def harvest_records():
    # every harvested record with a DOI, a title and authors
    records = []
    for HARVEST_DIR in HARVEST_DIRS:
        if not HARVEST_DIR.is_dir():
            continue
        for BIB_FILE in HARVEST_DIR.rglob("*.bib"):
            for fields in shared.bib_entries(BIB_FILE.read_text(encoding="utf-8-sig", errors="replace")):
                doi = shared.clean_doi(fields.get("doi", ""))
                if doi and fields.get("title") and fields.get("author"):
                    year = years_in(fields.get("year", ""))
                    records.append({"doi": doi, "title": fields["title"],
                                    "years": [int(year[0])] if year else [],
                                    "surnames": [shared.surname_of(name) for name in
                                                 re.split(r"\s+and\s+", fields["author"])],
                                    "journal": fields.get("journal", ""), "where": BIB_FILE.name})
    return records


def search_doi(row, harvest, cache, use_crossref):
    # returns (best candidate or None, method); the cache is changed in place
    candidates = []
    for record in harvest:
        status, score = judge(row, record["title"], record["years"], record["surnames"])
        candidates.append({"doi": record["doi"], "status": status, "score": score, "title": record["title"],
                           "journal": record["journal"], "note": "from %s" % record["where"]})
    best = shared.best_of(candidates)
    if best and best["status"] == "found":
        return best, "own_harvest"

    key = squash(row["citation"])[:120]
    if key not in cache and use_crossref:
        cache[key] = shared.crossref_search(row["citation"])
    if key in cache:
        candidates = []
        for item in cache[key]:
            title = " ".join(item.get("title") or [])
            surnames = [author.get("family", "") for author in item.get("author") or []]
            status, score = judge(row, title, shared.item_years(item), surnames)
            candidates.append({"doi": (item.get("DOI") or "").lower(), "status": status, "score": score,
                               "title": title, "journal": " ".join(item.get("container-title") or []),
                               "note": ""})
        found = shared.best_of(candidates)
        if found and (best is None or (shared.status_rank[found["status"]], found["score"])
                      > (shared.status_rank[best["status"]], best["score"])):
            return found, "crossref"
    return best, "own_harvest" if best else ""


# ================================================== #
# WRITING THE TABLES
# ================================================== #

def write_csv(CSV_PATH, columns, rows):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEMP_PATH = CSV_PATH.with_suffix(".tmp")
    with open(TEMP_PATH, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    try:
        TEMP_PATH.replace(CSV_PATH)
    except PermissionError:
        sys.exit("Cannot write %s, it is probably open in Excel. Close it and run again." % CSV_PATH)


def manual_rows():
    # rows marked "manual" by hand survive every run unchanged
    kept = {}
    for row in read_csv_rows(DOI_CSV):
        if (row.get("method") or "").strip().lower() == "manual" and (row.get("number") or "").strip().isdigit():
            row["doi"] = shared.clean_doi(row.get("doi", "")) or (row.get("doi") or "").strip()
            kept[int(row["number"])] = row
    return kept


# ================================================== #
# MAIN
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(description="Apply Robert Wagner's notes to his list and complete the DOIs.")
    parser.add_argument("--list", type=Path, default=LIST_DOCX,
                        help="output of 2_a_aida_robert_list.py (default: %(default)s)")
    parser.add_argument("--notes", type=Path, default=None,
                        help="his notes (default: searched in %s)" % RAW_DIR)
    parser.add_argument("--no-crossref", action="store_true",
                        help="no internet: list, notes, Ottmar's DOI list, harvests and cache only")
    parser.add_argument("--refresh", action="store_true", help="ask Crossref again, ignore the cache")
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    if not arguments.list.is_file():
        sys.exit("Not found: %s\nRun 2_a_aida_robert_list.py first." % arguments.list)

    # --- his list and his notes
    entries = read_list(arguments.list)
    NOTES_PATH = find_notes(arguments.notes)
    notes = sort_lines(read_lines(NOTES_PATH))
    for item, note in enumerate(notes, start=1):
        note["item"] = item
    print("Robert Wagner: %d entries in his list, %d notes in %s" % (len(entries), len(notes), NOTES_PATH.name))
    link_to_ottmar(notes)

    # --- apply them
    added = apply_notes(entries, notes)
    rows = entries + added

    # --- the DOI of every paper that stays
    manual = manual_rows()
    try:
        cache = {} if arguments.refresh else json.loads(CROSSREF_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    use_crossref = not arguments.no_crossref
    harvest = None

    for row in rows:
        row.setdefault("method", "")
        if row["change"] == "removed":
            row["status"] = "removed"
            continue
        if row["number"] in manual:
            hand = manual[row["number"]]
            row.update(doi=hand["doi"], method="manual", status="found" if hand["doi"] else "not_found",
                       note=hand.get("note") or row.get("note", ""))
            continue
        if row["doi"]:
            row["method"] = row["method"] or "in_list"
            row["status"] = "check" if shared.is_discussion(row["doi"]) else "found"
            continue

        if harvest is None:
            harvest = harvest_records()
        try:
            best, method = search_doi(row, harvest, cache, use_crossref)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            print("   Crossref not reachable (%s), continuing without it" % error)
            use_crossref = False
            best, method = search_doi(row, harvest, cache, False)
        if best is None:
            row["status"] = "not_found"
            row["note"] = "; ".join(part for part in (row.get("note"), "no DOI found, add it by hand "
                                                       "and set method to manual") if part)
        else:
            row.update(doi=best["doi"], method=method, status=best["status"], score="%.2f" % best["score"],
                       matched_title=best["title"])

    if not arguments.no_crossref:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CROSSREF_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")

    # --- the same DOI twice among the papers that stay is suspicious
    doi_count = Counter(row["doi"] for row in rows if row["doi"] and row["change"] != "removed")
    for row in rows:
        if row["doi"] and row["change"] != "removed" and doi_count[row["doi"]] > 1:
            row["status"] = "check"
            row["note"] = "; ".join(part for part in (row.get("note"), "same DOI as another paper") if part)

    rows.sort(key=lambda row: -row["number"])
    write_csv(DOI_CSV, doi_columns, rows)
    write_csv(CORRECTIONS_CSV, notes_columns, notes)

    # --- summary
    print("")
    print("His list")
    for change, count in Counter(row["change"] for row in rows).most_common():
        print("   %-17s %3d" % (change, count))
    staying = [row for row in rows if row["change"] != "removed"]
    print("   papers in the corrected list: %d" % len(staying))
    print("DOI status of these papers")
    for status, count in Counter(row["status"] for row in staying).most_common():
        print("   %-17s %3d" % (status, count))
    problems = [note for note in notes if note.get("applied", "").startswith("check")]
    for note in problems:
        print("   note %d not applied: %s" % (note["item"], note["applied"]))
    print("")
    print("Written: %s" % DOI_CSV)
    print("Written: %s" % CORRECTIONS_CSV)


if __name__ == "__main__":
    main()
