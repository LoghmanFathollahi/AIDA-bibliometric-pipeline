'''
2_c_aida_ottmar_bib.py

   Builds the bib file of Ottmar Möhler's AIDA publications from the DOI list
   made by 2_b_aida_ottmar_check.py.

Purpose

   The DOI list holds one row per publication of his own list, each with its
   DOI. This script reads that table, so DOIs corrected by hand are used, and
   builds one BibTeX entry per DOI, from the most to the least reliable source:

      doi_org        the record from doi.org, the publisher's own metadata
      crossref_api   the same record through the Crossref API, if doi.org fails
      own_harvest    the record from data/openalex or data/webofscience
      citation       no record at all: built from his citation text and
                     marked unverified

   A publication listed twice under one DOI becomes one entry that keeps
   both of his numbers. Every entry gets his list number, the source and the
   DOI status as extra fields, and a key of the form year_firstauthor_journal
   as in the master bibliography. En dashes become the LaTeX form --, and
   & and % are escaped. Downloaded records are cached, so a second run needs
   no internet.

Usage

   python 2_c_aida_ottmar_bib.py
      build the bib file

   python 2_c_aida_ottmar_bib.py --offline
      no internet needed: cached records and the own harvests only

   python 2_c_aida_ottmar_bib.py --refresh
      download every record again, ignore the cache

   Run 2_b_aida_ottmar_check.py first. All paths come from config.py, the
   same on Linux and Windows.

Output

   <root>/data/author_self_correction/bib/Ottmar_Moehler.bib     one entry per publication
   <root>/data/author_self_correction/cache/bibtex_ottmar.json   cached BibTeX records
   the bib folder is created when it does not exist
   a summary on the screen: how many records each source gave
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import csv
import html
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
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
DATA_DIR = Path(getattr(config, "DATA_DIR", FALLBACK_ROOT / "data"))
SELF_CORRECTION_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_DIR",
                                   DATA_DIR / "author_self_correction"))
DOI_CSV = Path(getattr(config, "OTTMAR_DOI_FILE",
                       SELF_CORRECTION_DIR / "Ottmar_Moehler_AIDA_dois.csv"))
CACHE_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_CACHE_DIR", SELF_CORRECTION_DIR / "cache"))
BIBTEX_CACHE = CACHE_DIR / "bibtex_ottmar.json"
BIB_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_BIB_DIR", SELF_CORRECTION_DIR / "bib"))
BIB_FILE = Path(getattr(config, "OTTMAR_BIB_FILE", BIB_DIR / "Ottmar_Moehler.bib"))
HARVEST_DIRS = [Path(HARVEST_DIR) for HARVEST_DIR in getattr(
    config, "BIB_SOURCE_DIRS", [DATA_DIR / "openalex", DATA_DIR / "webofscience"])]

# --- requests
crossref_mailto = getattr(config, "crossref_mailto", "loghman.fathollahi@kit.edu")
request_timeout = int(getattr(config, "request_timeout", 30))
request_pause = float(getattr(config, "request_pause", 0.2))

# --- the source written into every bib entry
aida_source_text = "Ottmar Möhler, own publication list, sent 2026-10-13"

# --- doi.org and the Crossref API give BibTeX for a DOI
doi_org_url = "https://doi.org/"
crossref_bibtex_url = "https://api.crossref.org/works/%s/transform/application/x-bibtex"

# --- citation keys as in the master bibliography: year_firstauthor_journal
key_stopwords = {"of", "the", "and", "for", "in", "on", "at", "to", "und", "fur", "fuer",
                 "der", "die", "das", "de", "des", "du", "la", "le"}

# --- fields written first, in this order; all others follow, then the AIDA fields
field_order = ["author", "editor", "title", "journal", "booktitle", "year", "month", "volume",
               "number", "pages", "publisher", "issn", "isbn", "doi", "url"]
# fields with plain text, where & and % must be escaped for LaTeX
text_fields = {"title", "journal", "booktitle", "publisher", "series", "note"}

# --- patterns
doi_pattern = re.compile(r"10\.\d{4,9}/[^\s,;\"<>]+", re.IGNORECASE)


# ================================================== #
# TEXT HELPERS
# ================================================== #

def fold(text):
    # lower case ASCII: Möhler, Moehler and Mohler all become "mohler"
    text = (text or "").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char)).lower()
    return text.replace("ae", "a").replace("oe", "o").replace("ue", "u")


def clean_doi(text):
    # bare lower case DOI, resolver prefix and trailing punctuation removed
    match = doi_pattern.search((text or "").replace("\\_", "_"))
    return match.group(0).rstrip(".)]").lower() if match else ""


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
# READING BIBTEX RECORDS
# ================================================== #

def read_braced(text, start):
    # text[start] is "{"; returns the inner text and the index after the closing brace
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{" and text[index - 1:index] != "\\":
            depth += 1
        elif text[index] == "}" and text[index - 1:index] != "\\":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    raise ValueError("unbalanced braces")


def parse_record(text):
    # one BibTeX record into (entry_type, {field: value}), the field order kept
    # values may be {braced}, "quoted" or bare, such as month = oct
    head = re.search(r"@\s*(\w+)\s*\{\s*[^,]*,", text or "")
    if not head:
        return None, {}
    entry_type = head.group(1).lower()
    fields = {}
    index = head.end()
    while index < len(text):
        match = re.compile(r"\s*,?\s*([A-Za-z][\w:-]*)\s*=\s*").match(text, index)
        if not match:
            break
        name = match.group(1).lower()
        index = match.end()
        if index >= len(text):
            break
        if text[index] == "{":
            value, index = read_braced(text, index)
        elif text[index] == '"':
            end = text.find('"', index + 1)
            value, index = text[index + 1:end], end + 1
        else:
            bare = re.compile(r"[^,}\s]+").match(text, index)
            value, index = (bare.group(0), bare.end()) if bare else ("", index)
        fields[name] = " ".join(value.split())
    return entry_type, fields


def load_json(JSON_PATH):
    try:
        return json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_json(JSON_PATH, data):
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEMP_PATH = JSON_PATH.with_suffix(".tmp")
    TEMP_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    TEMP_PATH.replace(JSON_PATH)


def download_text(url, accept):
    # one GET with retries on rate limits and server errors; HTTPError 404 is passed on
    headers = {"Accept": accept,
               "User-Agent": "AIDA-bibliometrics/1.0 (mailto:%s)" % crossref_mailto}
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
            time.sleep(request_pause)
            return text
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
    return ""


def fetch_record(doi):
    # BibTeX for one DOI: doi.org first, then the Crossref API
    # returns (text, source); ("", "") when neither knows the DOI
    # a network failure raises URLError, so the caller can stop asking
    attempts = [
        (doi_org_url + urllib.parse.quote(doi, safe="/:;()._-"), "application/x-bibtex; charset=utf-8", "doi_org"),
        (crossref_bibtex_url % urllib.parse.quote(doi, safe=""), "application/x-bibtex", "crossref_api"),
    ]
    for url, accept, source in attempts:
        try:
            text = download_text(url, accept).strip()
        except urllib.error.HTTPError:
            continue
        if text.startswith("@") and parse_record(text)[0]:
            return text, source
    return "", ""


def harvest_records():
    # every harvested record with a DOI, by DOI, as a fallback when downloading fails
    records = {}
    for HARVEST_DIR in HARVEST_DIRS:
        if not HARVEST_DIR.is_dir():
            continue
        for BIB_FILE_PATH in HARVEST_DIR.rglob("*.bib"):
            text = BIB_FILE_PATH.read_text(encoding="utf-8-sig", errors="replace")
            for chunk in re.split(r"\n\s*(?=@)", "\n" + text):
                entry_type, fields = parse_record(chunk)
                doi = clean_doi(fields.get("doi", ""))
                if entry_type and doi and doi not in records:
                    records[doi] = (entry_type, fields)
    return records


# ================================================== #
# BUILDING THE ENTRIES
# ================================================== #

def from_citation(row):
    # last resort: an entry made from Ottmar's citation text, marked unverified
    citation = row["citation"]
    fields = {"author": "%s and others" % (row["first_author"] or "Anonymous"),
              "year": str(row["year"] or ""),
              "note": citation}
    if row.get("doi"):
        fields["doi"] = row["doi"]
    return "misc", fields


def tidy_value(name, value):
    # dashes become the LaTeX forms, special characters are escaped in text fields
    value = (value or "").replace("—", "---").replace("–", "--").replace("−", "-")
    if name in text_fields:
        value = html.unescape(value)
        value = re.sub(r"(?<!\\)&", r"\\&", value)
        value = re.sub(r"(?<!\\)%", r"\\%", value)
    if name == "pages":
        value = re.sub(r"\s*-+\s*", "--", value)
    return " ".join(value.split())


def key_surname(fields):
    # surname of the first author, lower case ASCII letters only
    first = re.split(r"\s+and\s+", fields.get("author") or fields.get("editor") or "")[0]
    surname = first.split(",")[0] if "," in first else (first.split() or [""])[-1]
    surname = re.sub(r"[^a-z]", "", fold(re.sub(r"\\.|[{}]", "", surname)))
    return surname or "anon"


def key_journal(entry_type, fields):
    # initials of the journal: Atmospheric Chemistry and Physics gives acp
    venue = fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or ""
    venue = re.sub(r"\([^)]*\)", " ", fold(venue))
    words = [word for word in re.split(r"[^a-z0-9]+", venue) if word and word not in key_stopwords]
    if not words:
        return entry_type or "misc"
    if len(words) == 1:
        return words[0][:12]
    return "".join(word if word.isdigit() else word[0] for word in words)


def format_entry(entry_type, key, fields):
    names = [name for name in field_order if fields.get(name)]
    names += [name for name in fields if name not in names and fields.get(name)]
    width = max(len(name) for name in names)
    lines = ["@%s{%s," % (entry_type, key)]
    lines += ["  %s = {%s}," % (name.ljust(width), fields[name]) for name in names]
    lines[-1] = lines[-1].rstrip(",")
    lines.append("}")
    return "\n".join(lines)


def build_bib(arguments):
    # one BibTeX entry per DOI of the table
    rows = read_csv_rows(DOI_CSV)
    if not rows:
        sys.exit("No DOI list at %s\nRun 2_b_aida_ottmar_check.py first." % DOI_CSV)
    print("DOI list: %d rows from %s" % (len(rows), DOI_CSV.name))

    # --- one group per DOI; rows without a DOI stay alone
    groups = defaultdict(list)
    for row in rows:
        doi = clean_doi(row.get("doi", ""))
        row["doi"] = doi
        groups[doi or "nodoi_%s" % row["number"]].append(row)

    cache = {} if arguments.refresh else load_json(BIBTEX_CACHE)
    offline = arguments.offline
    harvest = None
    entries = []
    counts = Counter()
    unverified = []
    merged = []

    for group_key, group in groups.items():
        group.sort(key=lambda row: int(row["number"]))
        first = group[0]
        doi = first["doi"]
        entry_type, fields, source = None, {}, ""

        # --- download, or take it from the cache
        if doi:
            if doi not in cache and not offline:
                try:
                    text, record_source = fetch_record(doi)
                    if text:
                        cache[doi] = {"text": text, "source": record_source}
                except (urllib.error.URLError, TimeoutError) as error:
                    print("   doi.org not reachable (%s), continuing with harvests and cache" % error)
                    offline = True
            if doi in cache:
                entry_type, fields = parse_record(cache[doi]["text"])
                source = cache[doi]["source"]

        # --- the own harvests, then his citation
        if not entry_type and doi:
            if harvest is None:
                harvest = harvest_records()
            if doi in harvest:
                entry_type, fields = harvest[doi][0], dict(harvest[doi][1])
                source = "own_harvest"
        if not entry_type:
            entry_type, fields = from_citation(first)
            source = "citation"
            unverified.append(first)

        # --- tidy the fields, the url always points to doi.org
        fields = {name: tidy_value(name, value) for name, value in fields.items()
                  if name not in ("key", "keywords") and value}
        if doi:
            fields["doi"] = doi
            fields["url"] = doi_org_url + doi
        if not fields.get("year") and first.get("year"):
            fields["year"] = str(first["year"])

        # --- where it came from
        numbers = [row["number"] for row in group]
        fields["ottmar_number"] = ", ".join(numbers)
        fields["aida_source"] = aida_source_text
        fields["record_source"] = source
        fields["doi_method"] = first.get("method") or "none"
        statuses = {row.get("status") or "" for row in group}
        if statuses != {"found"} or source == "citation":
            fields["doi_status"] = "unverified" if source == "citation" else "/".join(sorted(statuses))
        if len(group) > 1:
            merged.append(numbers)

        counts[source] += 1
        entries.append({"type": entry_type, "fields": fields,
                        "year": int(re.search(r"\d{4}", fields.get("year", "0") + "0000").group(0)),
                        "number": int(first["number"])})

    if not arguments.offline:
        save_json(BIBTEX_CACHE, cache)

    # --- newest first, then by his number, then keys as in the master bibliography
    entries.sort(key=lambda entry: (-entry["year"], -entry["number"]))
    used_keys = set()
    blocks = []
    for entry in entries:
        base = "%s_%s_%s" % (entry["fields"].get("year") or "noyear",
                             key_surname(entry["fields"]), key_journal(entry["type"], entry["fields"]))
        key, letter = base, ord("a")
        while key in used_keys:
            letter += 1
            key = "%s_%s" % (base, chr(letter))
        used_keys.add(key)
        blocks.append(format_entry(entry["type"], key, entry["fields"]))

    # --- write; the folder is created when it does not exist
    # the lines start with %, the BibTeX comment sign, so they are joined, not formatted
    header = ["% " + BIB_FILE.name,
              "% AIDA publications of Ottmar Möhler, from his own publication list",
              "% source   : " + aida_source_text,
              "% built by : 2_c_aida_ottmar_bib.py, " + date.today().isoformat(),
              "% entries  : {} from {} rows of his list".format(len(blocks), len(rows)),
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
        print("   unverified, built from his citation: #%s %s" % (row["number"], row["citation"][:60]))
    print("")
    print("Written: %s" % BIB_FILE)
    print("   %d entries" % len(blocks))


# ================================================== #
# MAIN
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Build the bib file of Ottmar Möhler's AIDA publications from his DOI list.")
    parser.add_argument("--offline", action="store_true",
                        help="no internet: cached records and own harvests only")
    parser.add_argument("--refresh", action="store_true", help="download every record again")
    return parser.parse_args()


def main():
    build_bib(parse_arguments())


if __name__ == "__main__":
    main()
