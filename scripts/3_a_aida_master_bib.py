'''
3_a_aida_master_bib.py

   Builds the AIDA master bibliography from the BibTeX files harvested
   from OpenAlex and Web of Science in three stages: merge, deduplicate
   and filter. The lists the authors confirmed themselves in step 2 decide
   before the filters do.

Purpose

   Stage 1: read every .bib file under data/openalex and data/webofscience
            (keywords and researchers subfolders included) and merge all
            entries into one file.
   Stage 2: merge duplicated entries into one entry per paper. Duplicates
            share a DOI, or share a normalised title and year when a DOI
            is missing. The most complete copy is the base; fields it lacks
            are filled from the other copies, and keyword and affiliation
            lists are combined. Every entry then gets a new citation key
            year_firstauthor_journalabbreviation, e.g. 2003_weingartner_jas.
   Exclusion: papers listed in data/noAIDA.txt (by DOI, or by a citation
            text that contains the title) are removed in every stage, so
            they appear in no output file. A duplicate group is removed
            completely when any of its copies is listed.
   Authors: before filtering, the authors' own confirmed lists are applied
            (author_confirmed_lists in config.py, the bib files of step 2).
            They are the main source, and the other sources follow them:
            a paper in an author's list is kept, the filters are skipped,
            and it gets the field aida_confirmed_by; a paper in a list but
            not found by any harvest is added; a paper an author removed is
            removed; a paper with an author on it whose list is "complete"
            but does not hold it is removed. Authors without a confirmed list
            change nothing, their papers go through the filters as before.
            When one author confirms a paper and another removed it, it is
            kept and reported as a conflict.
   Stage 3: filter the deduplicated file with the filter selected in
            config.py (master_bib_filter = 1 to 6). Terms are searched in
            title, abstract and keywords; when two are required they may
            sit in different fields:
            Filter 1: AIDA term
            Filter 2: campaign name
            Filter 3: AIDA term and campaign name
            Filter 4: AIDA term and KIT affiliation
            Filter 5: campaign name and KIT affiliation
            Filter 6: AIDA term, campaign name and KIT affiliation
            Several filters can be selected, e.g. master_bib_filter = [4, 5].
            Each filter then writes its own files, and all entries kept by at
            least one of them are combined into the final bib file.

Usage

   python 3_a_aida_master_bib.py                      run all three stages
   python 3_a_aida_master_bib.py --filter 2           override the filter in config.py
   python 3_a_aida_master_bib.py --filter 4 5         run filters 4 and 5 and combine them
   python 3_a_aida_master_bib.py --stage filter       rerun only stage 3
   python 3_a_aida_master_bib.py --no-kit-filter      skip the affiliation check
   python 3_a_aida_master_bib.py --dry-run --verbose  run without writing files

   All folders come from config.py and are relative to the project root,
   so the script runs the same on Linux and Windows without editing paths.

Output

   Written to MASTER_BIB_DIR (default <root>/AIDA_master), in three folders:

   master/
   AIDA_Master_bib.bib                  stage 3, the final master (BibTeX)

   authors/
   <First_Last>.bib                     one master per researcher of AIDA Scientists.txt,
                                        his papers of the final master (BibTeX)

   supplementary/
   AIDA_Master_bib_first_satge.bib      stage 1, merged entries (BibTeX)
   AIDA_Master_bib_NoDuplicate.bib      stage 2, one merged entry per paper (BibTeX)
   AIDA_Master_bib_dedup_report.txt     stage 1 and 2 statistics (text)
   AIDA_Master_bib_filter_summary.txt   stage 3 statistics (text)
   AIDA_Master_bib_filter_log.csv       stage 3 decision per entry (CSV)
   AIDA_Master_bib_removed.bib          stage 3 removed entries (BibTeX)
   AIDA_Master_bib_author_decisions.csv stage 3, every decision of the authors' lists (CSV)
   AIDA_Master_bib_authors_summary.txt  papers per researcher, and who has none (text)

   With several filters, every filter writes its bib file and the three
   files above it into supplementary/, with _filterN after AIDA_Master_bib
   (e.g. AIDA_Master_bib_filter4.bib), the final master in master/ is the
   union of all selected filters, and supplementary/ also gets
   AIDA_Master_bib_combined_summary.txt counts, overlaps and entry list (text)
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import csv
import functools
import io
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# config.py is optional: every setting below has a default,
# so the script also runs standalone
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
# CONFIGURATION
# ================================================== #

def get_setting(name, default):
    # Look the name up in config.py as written, in upper case and in lower case,
    # so the script works with either naming style
    if config is not None:
        for candidate in (name, name.upper(), name.lower()):
            if hasattr(config, candidate):
                return getattr(config, candidate)
    return default


# --- input and output directories
AIDA_DATA_DIR = Path(get_setting("AIDA_DATA_DIR", FALLBACK_ROOT / "data"))
AIDA_CAMPAIGNS_DIR = Path(get_setting("AIDA_CAMPAIGNS_DIR", AIDA_DATA_DIR / "AIDA_campaigns"))
OPENALEX_BIB_DIR = Path(get_setting("OPENALEX_BIB_DIR", AIDA_DATA_DIR / "openalex"))
WOS_BIB_DIR = Path(get_setting("WOS_BIB_DIR", AIDA_DATA_DIR / "webofscience"))
MASTER_BIB_DIR = Path(get_setting("MASTER_BIB_DIR", FALLBACK_ROOT / "AIDA_master"))
# the master itself, one master per author, and every other file, each in its own folder
MASTER_BIB_MAIN_DIR = Path(get_setting("MASTER_BIB_MAIN_DIR", MASTER_BIB_DIR / "master"))
MASTER_BIB_AUTHORS_DIR = Path(get_setting("MASTER_BIB_AUTHORS_DIR", MASTER_BIB_DIR / "authors"))
MASTER_BIB_SUPPLEMENTARY_DIR = Path(get_setting("MASTER_BIB_SUPPLEMENTARY_DIR", MASTER_BIB_DIR / "supplementary"))

BIB_SOURCE_DIRS = [
    Path(SOURCE_DIR)
    for SOURCE_DIR in get_setting("BIB_SOURCE_DIRS", [OPENALEX_BIB_DIR, WOS_BIB_DIR])
]
CAMPAIGN_FILES = [
    Path(CAMPAIGN_FILE)
    for CAMPAIGN_FILE in get_setting(
        "CAMPAIGN_FILES",
        [AIDA_CAMPAIGNS_DIR / "campaign_instrument_matrix.csv",
         AIDA_CAMPAIGNS_DIR / "campaigns.csv"],
    )
]

# --- output files
MASTER_BIB_FIRST_STAGE_FILE = Path(get_setting(
    "MASTER_BIB_FIRST_STAGE_FILE", MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_first_satge.bib"))
MASTER_BIB_NODUP_FILE = Path(get_setting(
    "MASTER_BIB_NODUP_FILE", MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_NoDuplicate.bib"))
MASTER_BIB_DEDUP_REPORT_FILE = Path(get_setting(
    "MASTER_BIB_DEDUP_REPORT_FILE", MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_dedup_report.txt"))
MASTER_BIB_FILE = Path(get_setting(
    "MASTER_BIB_FILE", MASTER_BIB_MAIN_DIR / "AIDA_Master_bib.bib"))
MASTER_BIB_FILTER_SUMMARY_FILE = Path(get_setting(
    "MASTER_BIB_FILTER_SUMMARY_FILE", MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_filter_summary.txt"))
MASTER_BIB_FILTER_LOG_FILE = Path(get_setting(
    "MASTER_BIB_FILTER_LOG_FILE", MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_filter_log.csv"))
MASTER_BIB_REMOVED_FILE = Path(get_setting(
    "MASTER_BIB_REMOVED_FILE", MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_removed.bib"))
# Papers that must never appear in any output, one DOI or citation per item
NOAIDA_FILE = Path(get_setting("NOAIDA_FILE", AIDA_DATA_DIR / "noAIDA.txt"))
MASTER_BIB_COMBINED_SUMMARY_FILE = Path(get_setting(
    "MASTER_BIB_COMBINED_SUMMARY_FILE", MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_combined_summary.txt"))
MASTER_BIB_AUTHOR_LOG_FILE = Path(get_setting(
    "MASTER_BIB_AUTHOR_LOG_FILE", MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_author_decisions.csv"))
MASTER_BIB_AUTHORS_SUMMARY_FILE = Path(get_setting(
    "MASTER_BIB_AUTHORS_SUMMARY_FILE", MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_authors_summary.txt"))
# the AIDA researchers, one name per line; each gets a master bib of his own papers
RESEARCHERS_FILE = Path(get_setting("RESEARCHERS_FILE", AIDA_DATA_DIR / "AIDA Scientists.txt"))
researchers_file_variants = ["AIDA Scientists.txt", "AIDA Scientists .txt", "aida_scientists.txt"]

# --- the authors' own confirmed lists, see config.py; an empty list changes nothing
author_confirmed_lists = list(get_setting("author_confirmed_lists", []))
# fields of the authors' bib files that only describe their own step, not the paper
author_bookkeeping_fields = {"ottmar_number", "robert_number", "aida_source", "record_source",
                             "doi_method", "doi_status", "change", "change_reason"}

# --- filter settings
# Definition of every filter number used by master_bib_filter in config.py
# terms: "aida", "campaign", "aida_and_campaign" or "aida_or_campaign"
# kit  : True requires at least one KIT affiliation
filter_definitions = {
    "1": {"terms": "aida", "kit": False},
    "2": {"terms": "campaign", "kit": False},
    "3": {"terms": "aida_and_campaign", "kit": False},
    "4": {"terms": "aida", "kit": True},
    "5": {"terms": "campaign", "kit": True},
    "6": {"terms": "aida_and_campaign", "kit": True},
}
term_rule_texts = {
    "aida": "AIDA term",
    "campaign": "campaign name",
    "aida_and_campaign": "AIDA term AND campaign name",
    "aida_or_campaign": "AIDA term OR campaign name",
}


def parse_filter_list(value):
    # Accepts 4, "4", [4, 5] or "4, 5"; repeated numbers are used once
    items = list(value) if isinstance(value, (list, tuple, set)) else re.split(r"[,;\s]+", str(value))
    modes = []
    for item in items:
        text = str(item).strip()
        if text and text not in modes:
            modes.append(text)
    return modes


# One filter (e.g. 4) or several (e.g. [4, 5]) from config.py
filter_modes = parse_filter_list(get_setting("master_bib_filter", 1))

aida_keywords = list(get_setting("aida_keywords", [
    "Aerosol Interaction and Dynamics in the Atmosphere",
]))
aida_keywords_exact_case = list(get_setting("aida_keywords_exact_case", ["AIDA"]))

campaign_column = str(get_setting("campaign_column", "campaign"))
campaign_exact_case = bool(get_setting("campaign_exact_case", True))
campaign_min_length = int(get_setting("campaign_min_length", 3))
campaign_exclude = list(get_setting("campaign_exclude", []))

kit_affiliation = list(get_setting("kit_affiliation", [
    "Karlsruhe Institute of Technology",
    "Karlsruhe Inst Technol",
    "Karlsruher Institut fur Technologie",
    "Forschungszentrum Karlsruhe",
    "Research Center Karlsruhe",
    "Research Centre Karlsruhe",
    "Res Ctr Karlsruhe",
    "University of Karlsruhe",
    "Univ Karlsruhe",
    "Universitat Karlsruhe",
]))
kit_affiliation_exact_case = list(get_setting("kit_affiliation_exact_case", ["KIT", "FZK"]))
# Own name, so it does not collide with require_kit_affiliation of the WoS filter
require_kit_affiliation = bool(get_setting("master_bib_require_kit_affiliation", True))
keep_if_affiliation_missing = bool(get_setting("keep_if_affiliation_missing", False))
save_removed_entries = bool(get_setting("save_removed_entries", True))

# --- BibTeX field names searched by the filter (compared in lower case)
bib_title_fields = [name.lower() for name in get_setting("bib_title_fields", ["title"])]
bib_abstract_fields = [name.lower() for name in get_setting("bib_abstract_fields", ["abstract"])]
bib_keyword_fields = [name.lower() for name in get_setting(
    "bib_keyword_fields", ["keywords", "author-keywords", "keywords-plus"])]
bib_affiliation_fields = [name.lower() for name in get_setting(
    "bib_affiliation_fields", ["affiliation", "affiliations"])]

# --- deduplication settings
dedup_year_tolerance = int(get_setting("dedup_year_tolerance", 1))
dedup_min_title_length = int(get_setting("dedup_min_title_length", 20))

# --- citation key settings
# Optional manual journal abbreviations, e.g. {"Journal of Aerosol Science": "jas"};
# journals not listed get the initials of their significant words
journal_abbreviations = dict(get_setting("master_bib_journal_abbreviations", {}))
# Fields searched in this order for the journal name
venue_fields = ["journal", "journaltitle", "booktitle", "series", "school", "institution", "publisher"]
# Words skipped when building initials
journal_stopwords = {"of", "the", "and", "for", "in", "on", "at", "to", "und", "fur", "fuer",
                     "der", "die", "das", "de", "des", "du", "la", "le"}

# --- fixed internal settings
location_names = ["title", "abstract", "keywords"]
verbose_mode = False


# ================================================== #
# GENERAL HELPERS
# ================================================== #

def log(message, detail=False):
    # Detail messages are printed only with --verbose
    if detail and not verbose_mode:
        return
    print(message)


def percent(count, total):
    return f"{100.0 * count / total:.1f}%" if total else "0.0%"


def format_table(header, rows, left_columns=1):
    # The first left_columns columns are left aligned, all other columns right aligned
    all_rows = [header] + rows
    widths = [max(len(str(row[i])) for row in all_rows) for i in range(len(header))]
    lines = []
    for row_index, row in enumerate(all_rows):
        cells = []
        for i, cell in enumerate(row):
            text = str(cell)
            cells.append(text.ljust(widths[i]) if i < left_columns else text.rjust(widths[i]))
        lines.append("   " + "   ".join(cells))
        if row_index == 0:
            lines.append("   " + "   ".join("-" * width for width in widths))
    return lines


def read_text_file(FILE_PATH):
    # Try UTF-8 first; fall back to Latin-1 so a badly encoded export never stops the run
    try:
        return FILE_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        log(f"   warning: {FILE_PATH} is not UTF-8, read as Latin-1")
        return FILE_PATH.read_text(encoding="latin-1")


# ================================================== #
# BIBTEX PARSING
# ================================================== #

@dataclass
class BibEntry:
    entry_type: str
    key: str
    fields: dict
    raw: str
    source: str
    index: int = 0


entry_start_re = re.compile(r"@\s*([A-Za-z]+)\s*([{(])")
field_name_re = re.compile(r"\s*([A-Za-z0-9_\-:.+]+)\s*=\s*")
bare_value_re = re.compile(r"[^,#\s]+")
source_comment_re = re.compile(r"^%\s*source:\s*(.+?)\s*$", re.MULTILINE)
non_entry_types = {"comment", "preamble", "string"}


# --- low level scanning

def find_block_end(text, start, open_char):
    # Scan from just after an opening delimiter and return the index just after
    # the matching closing delimiter, or -1 when the block never closes
    brace_depth = 1 if open_char == "{" else 0
    paren_depth = 1 if open_char == "(" else 0
    i = start
    n = len(text)
    while i < n:
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
            if open_char == "{" and brace_depth == 0:
                return i + 1
        elif open_char == "(" and brace_depth == 0:
            if char == "(":
                paren_depth += 1
            elif char == ")":
                paren_depth -= 1
                if paren_depth == 0:
                    return i + 1
        i += 1
    return -1


def find_top_level_comma(text, start):
    # Return the index of the next comma that is not inside braces or quotes
    brace_depth = 0
    in_quotes = False
    i = start
    n = len(text)
    while i < n:
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
        elif char == '"' and brace_depth == 0:
            in_quotes = not in_quotes
        elif char == "," and brace_depth == 0 and not in_quotes:
            return i
        i += 1
    return -1


def read_field_value(text, pos):
    # Read one field value: {braced}, "quoted" or bare (number or @string macro),
    # including concatenations joined with #
    parts = []
    n = len(text)
    while pos < n:
        while pos < n and text[pos].isspace():
            pos += 1
        if pos >= n:
            break
        char = text[pos]
        if char == "{":
            end = find_block_end(text, pos + 1, "{")
            if end == -1:
                parts.append(text[pos + 1:])
                return "".join(parts), n
            parts.append(text[pos + 1:end - 1])
            pos = end
        elif char == '"':
            end = pos + 1
            depth = 0
            while end < n:
                current = text[end]
                if current == "\\":
                    end += 2
                    continue
                if current == "{":
                    depth += 1
                elif current == "}":
                    depth -= 1
                elif current == '"' and depth == 0:
                    break
                end += 1
            parts.append(text[pos + 1:end])
            pos = end + 1
        else:
            match = bare_value_re.match(text, pos)
            if not match:
                break
            parts.append(match.group(0))
            pos = match.end()
        # Continue only if the next non space character is a # concatenation
        look = pos
        while look < n and text[look].isspace():
            look += 1
        if look < n and text[look] == "#":
            pos = look + 1
            continue
        break
    return "".join(parts), pos


# --- entry level parsing

def parse_entry_body(body):
    # Split an entry body into its citation key and a dict of lower case field names
    comma = find_top_level_comma(body, 0)
    if comma == -1:
        return body.strip(), {}
    key = body[:comma].strip()
    pos = comma + 1
    # An entry without a key starts directly with "name = value"
    if "=" in key:
        key = ""
        pos = 0
    fields = {}
    n = len(body)
    while pos < n:
        match = field_name_re.match(body, pos)
        if not match:
            next_comma = find_top_level_comma(body, pos)
            if next_comma == -1:
                break
            pos = next_comma + 1
            continue
        name = match.group(1).lower()
        value, pos = read_field_value(body, match.end())
        value = " ".join(value.split())
        # A repeated field name is kept by joining both values
        if fields.get(name) and value:
            fields[name] = fields[name] + " ; " + value
        elif value or name not in fields:
            fields[name] = value
        next_comma = find_top_level_comma(body, pos)
        if next_comma == -1:
            break
        pos = next_comma + 1
    return key, fields


def parse_bib_text(text, default_source):
    # Parse all entries in a BibTeX text.
    # A "% source: ..." comment directly before an entry overrides default_source
    entries = []
    stats = Counter()
    pos = 0
    gap_start = 0
    while True:
        at = text.find("@", pos)
        if at == -1:
            break
        match = entry_start_re.match(text, at)
        if not match:
            pos = at + 1
            continue
        entry_type = match.group(1).lower()
        end = find_block_end(text, match.end(), match.group(2))
        if end == -1:
            stats["unclosed"] += 1
            log(f"   warning: unclosed entry at character {at} in {default_source}, rest of file skipped")
            break
        source_matches = source_comment_re.findall(text[gap_start:at])
        source = source_matches[-1] if source_matches else default_source
        pos = end
        gap_start = end
        if entry_type in non_entry_types:
            stats["skipped_" + entry_type] += 1
            continue
        key, fields = parse_entry_body(text[match.end():end - 1])
        entries.append(BibEntry(entry_type, key, fields, text[at:end].strip(), source))
        stats["entries"] += 1
    return entries, stats


def read_bib_file(BIB_FILE, default_source=None):
    return parse_bib_text(read_text_file(BIB_FILE), default_source or BIB_FILE.name)


def write_bib_file(OUTPUT_FILE, entries, header_lines, entry_notes=None):
    # Header lines are BibTeX comments; they must not contain the @ character.
    # entry_notes maps id(entry) to an extra comment line written above that entry
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as handle:
        for line in header_lines:
            handle.write("% " + line.replace("@", "(at)") + "\n")
        handle.write("\n")
        for entry in entries:
            handle.write(f"% source: {entry.source}\n")
            if entry_notes and id(entry) in entry_notes:
                handle.write(f"% {entry_notes[id(entry)]}\n")
            handle.write(entry.raw + "\n\n")


# ================================================== #
# TEXT NORMALISATION
# ================================================== #

latex_symbol_accent_re = re.compile(r"\\[`'\"^~=.]\s*\{?\s*([A-Za-z])\s*\}?")
latex_letter_accent_re = re.compile(r"\\[uvHckrbdt](?:\s+|\s*\{\s*)([A-Za-z])\}?")
latex_special_re = re.compile(r"\\(ss|o|O|ae|AE|oe|OE|aa|AA|l|L)(?![A-Za-z])")
latex_special_map = {"ss": "ss", "o": "o", "O": "O", "ae": "ae", "AE": "AE",
                     "oe": "oe", "OE": "OE", "aa": "a", "AA": "A", "l": "l", "L": "L"}
latex_escaped_re = re.compile(r"\\([&%_$#])")
latex_command_re = re.compile(r"\\[A-Za-z]+\*?")
doi_re = re.compile(r"10\.\d{4,9}/[^\s\"<>{}]+")
year_re = re.compile(r"(?:19|20)\d{2}")


def strip_diacritics(text):
    text = text.replace("ß", "ss")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def clean_latex(text):
    # Turn LaTeX markup and accents into plain ASCII like text
    if not text:
        return ""
    text = latex_symbol_accent_re.sub(r"\1", text)
    text = latex_letter_accent_re.sub(r"\1", text)
    text = latex_special_re.sub(lambda match: latex_special_map[match.group(1)], text)
    text = latex_escaped_re.sub(r"\1", text)
    text = latex_command_re.sub(" ", text)
    text = text.replace("{", "").replace("}", "").replace("$", "")
    text = strip_diacritics(text)
    return " ".join(text.split())


def normalize_for_matching(text):
    # Punctuation becomes a space so "Karlsruhe Inst. Technol." matches "Karlsruhe Inst Technol".
    # Case is kept here; each matcher decides about case sensitivity
    text = clean_latex(text)
    text = re.sub(r"[^\w&/\-]+", " ", text)
    return " ".join(text.split())


def normalize_title(text):
    return re.sub(r"[^a-z0-9]", "", clean_latex(text).lower())


def extract_doi(entry):
    # Take the DOI from the doi field, or from a doi.org link in url
    for name in ("doi", "url"):
        value = entry.fields.get(name, "").replace("\\", "")
        doi = normalize_doi(value)
        if doi:
            return doi
    return ""


def normalize_doi(text):
    # First DOI found in the text, in lower case, without trailing punctuation
    match = doi_re.search(text.replace("\\", ""))
    return match.group(0).lower().rstrip(".,;)]") if match else ""


def extract_year(entry):
    match = year_re.search(entry.fields.get("year", ""))
    return int(match.group(0)) if match else None


def field_text(entry, field_names):
    return " ; ".join(entry.fields[name] for name in field_names if entry.fields.get(name))


# ================================================== #
# EXCLUSION LIST (noAIDA.txt)
# ================================================== #

# A new item starts with an optional number such as "1-", "2)" or "3. ",
# or directly with a DOI; any other line continues the item above it
exclusion_item_start_re = re.compile(r"^\s*(?:\d+\s*(?:[-)]|\.(?=\s))\s*|(?=10\.\d{4,9}/))(.*)$")


@functools.lru_cache(maxsize=None)
def load_exclusions():
    # Read noAIDA.txt once. Lines starting with # are comments, a blank line ends an item
    if not NOAIDA_FILE.exists():
        log(f"   note: {NOAIDA_FILE} not found, no papers are excluded")
        return ()
    raw_items = []
    for line in read_text_file(NOAIDA_FILE).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if not stripped and raw_items and raw_items[-1]:
                raw_items.append("")
            continue
        match = exclusion_item_start_re.match(line)
        if match or not raw_items or not raw_items[-1]:
            raw_items.append((match.group(1) if match else stripped).strip())
        else:
            raw_items[-1] = raw_items[-1] + " " + stripped
    exclusions = []
    for text in raw_items:
        if not text:
            continue
        doi = normalize_doi(text)
        year_match = year_re.search(text)
        exclusions.append({
            "text": text,
            "doi": doi,
            # A citation without DOI is matched by the entry title inside this text
            "text_norm": "" if doi else normalize_title(text),
            "year": int(year_match.group(0)) if year_match and not doi else None,
        })
    return tuple(exclusions)


def exclusion_matches(entry, exclusions):
    # Numbers of the list items that match this entry
    doi = extract_doi(entry)
    title_norm = normalize_title(field_text(entry, bib_title_fields))
    year = extract_year(entry)
    matched = []
    for number, item in enumerate(exclusions, 1):
        if item["doi"]:
            if doi and doi == item["doi"]:
                matched.append(number)
        elif len(title_norm) >= dedup_min_title_length and title_norm in item["text_norm"]:
            if item["year"] and year and abs(item["year"] - year) > dedup_year_tolerance:
                continue
            matched.append(number)
    return matched


def apply_exclusions(entries):
    # Remove listed papers; returns kept entries, removed labels per item, removed count
    exclusions = load_exclusions()
    removed_by_item = {number: [] for number in range(1, len(exclusions) + 1)}
    if not exclusions:
        return list(entries), removed_by_item, 0
    # --- pass 1: entries that match a list item directly
    candidates = []
    removed_titles = defaultdict(list)
    for entry in entries:
        matched = exclusion_matches(entry, exclusions)
        if matched:
            for number in matched:
                removed_by_item[number].append(f"{entry.key}  [{entry.source}]")
            title_norm = normalize_title(field_text(entry, bib_title_fields))
            if len(title_norm) >= dedup_min_title_length:
                removed_titles[title_norm].append((extract_year(entry), extract_doi(entry), matched))
        else:
            candidates.append(entry)

    # --- pass 2: other copies of a removed paper that lack its DOI, found by the
    # same title and year rule as the deduplication (a different DOI is a different paper)
    kept = []
    for entry in candidates:
        title_norm = normalize_title(field_text(entry, bib_title_fields))
        year, doi = extract_year(entry), extract_doi(entry)
        same_paper = None
        for removed_year, removed_doi, matched in removed_titles.get(title_norm, []):
            year_ok = not year or not removed_year or abs(year - removed_year) <= dedup_year_tolerance
            doi_ok = not doi or not removed_doi or doi == removed_doi
            if year_ok and doi_ok:
                same_paper = matched
                break
        if same_paper:
            for number in same_paper:
                removed_by_item[number].append(f"{entry.key}  [{entry.source}]  (copy with the same title)")
        else:
            kept.append(entry)
    return kept, removed_by_item, len(entries) - len(kept)


def exclusion_report_lines(title, removed_by_item, removed_count):
    exclusions = load_exclusions()
    lines = [
        title,
        f"   list file       : {NOAIDA_FILE}",
        f"   items in list   : {len(exclusions)}",
        f"   entries removed : {removed_count}",
    ]
    for number, item in enumerate(exclusions, 1):
        description = f"DOI {item['doi']}" if item["doi"] else f"citation (title match): {item['text'][:100]}"
        lines.append(f"   item {number}: {description}")
        labels = removed_by_item.get(number, [])
        lines += [f"       removed  {label}" for label in labels] or ["       no entry matched"]
    return lines


def log_exclusions(removed_by_item, removed_count):
    unmatched = [str(number) for number, labels in removed_by_item.items() if not labels]
    log(f"   removed by noAIDA.txt: {removed_count}"
        + (f" (items without a match: {', '.join(unmatched)})" if unmatched else ""))


# ================================================== #
# STAGE 1: MERGE
# ================================================== #

def collect_bib_files(source_dirs):
    # All .bib files below each source directory, subfolders included, in a stable order
    BIB_FILES = []
    for SOURCE_DIR in source_dirs:
        if not SOURCE_DIR.is_dir():
            log(f"   warning: source folder not found: {SOURCE_DIR}")
            continue
        BIB_FILES.extend(sorted(
            BIB_FILE for BIB_FILE in SOURCE_DIR.rglob("*")
            if BIB_FILE.is_file() and BIB_FILE.suffix.lower() == ".bib"
        ))
    return BIB_FILES


def run_merge(dry_run):
    log("\n[stage 1] merging BibTeX files")
    BIB_FILES = collect_bib_files(BIB_SOURCE_DIRS)
    if not BIB_FILES:
        sys.exit("error: no .bib files found in " + ", ".join(str(d) for d in BIB_SOURCE_DIRS))

    merged_entries = []
    total_stats = Counter()
    per_file_rows = []
    per_folder_counts = Counter()

    for BIB_FILE in BIB_FILES:
        # The source label is the path relative to the data folder, e.g. openalex/keywords/x.bib
        try:
            source = BIB_FILE.relative_to(AIDA_DATA_DIR).as_posix()
        except ValueError:
            source = BIB_FILE.as_posix()
        entries, stats = read_bib_file(BIB_FILE, source)
        merged_entries.extend(entries)
        total_stats.update(stats)
        per_file_rows.append([source, len(entries)])
        per_folder_counts[str(Path(source).parent.as_posix())] += len(entries)
        log(f"   {len(entries):6d}  {source}", detail=True)

    # --- remove papers listed in noAIDA.txt
    entries_read = len(merged_entries)
    merged_entries, removed_by_item, removed_count = apply_exclusions(merged_entries)

    for index, entry in enumerate(merged_entries):
        entry.index = index

    report_lines = [
        "STAGE 1: MERGE",
        f"   bib files read           : {len(BIB_FILES)}",
        f"   entries read             : {entries_read}",
        f"   removed by noAIDA.txt    : {removed_count}",
        f"   entries merged           : {len(merged_entries)}",
        f"   skipped @comment         : {total_stats['skipped_comment']}",
        f"   skipped @string          : {total_stats['skipped_string']}",
        f"   skipped @preamble        : {total_stats['skipped_preamble']}",
        f"   unclosed (broken) entries: {total_stats['unclosed']}",
        "",
        "   entries per folder",
    ]
    report_lines += format_table(["folder", "entries"],
                                 [[folder, count] for folder, count in sorted(per_folder_counts.items())])
    report_lines += ["", "   entries per file"]
    report_lines += format_table(["file", "entries"], per_file_rows)
    report_lines += [""] + exclusion_report_lines("   EXCLUDED BY noAIDA.txt (stage 1)", removed_by_item, removed_count)

    for line in report_lines[:10]:
        log(line)
    log_exclusions(removed_by_item, removed_count)

    if dry_run:
        log(f"   dry run: {MASTER_BIB_FIRST_STAGE_FILE} not written")
    else:
        write_bib_file(MASTER_BIB_FIRST_STAGE_FILE, merged_entries, [
            "AIDA master bibliography, stage 1: all OpenAlex and Web of Science entries merged",
            f"created {datetime.now():%Y-%m-%d %H:%M} by aida_master_bib.py",
            f"entries: {len(merged_entries)} from {len(BIB_FILES)} files",
        ])
        log(f"   written: {MASTER_BIB_FIRST_STAGE_FILE}")
    return merged_entries, report_lines


# ================================================== #
# STAGE 2: DEDUPLICATION
# ================================================== #

class DuplicateGroups:
    # Union find over entry indices. Every group may hold at most one distinct DOI,
    # so two papers with different DOIs are never merged, not even through a
    # third entry that has no DOI

    def __init__(self, dois):
        self.parent = list(range(len(dois)))
        self.doi_sets = [{doi} if doi else set() for doi in dois]

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i, j):
        root_i, root_j = self.find(i), self.find(j)
        if root_i == root_j:
            return "same"
        merged_dois = self.doi_sets[root_i] | self.doi_sets[root_j]
        if len(merged_dois) > 1:
            return "blocked"
        self.parent[root_j] = root_i
        self.doi_sets[root_i] = merged_dois
        return "merged"


def completeness_key(entry):
    # Higher is better: the most complete copy becomes the base of the merged entry
    return (
        bool(field_text(entry, bib_abstract_fields)),
        bool(field_text(entry, bib_affiliation_fields)),
        bool(extract_doi(entry)),
        bool(field_text(entry, bib_keyword_fields)),
        sum(1 for value in entry.fields.values() if value),
        len(entry.raw),
        -entry.index,
    )


# --- merging duplicate copies

def split_list_value(value):
    # Keyword and affiliation lists are separated by semicolons in both sources
    return [part.strip() for part in value.split(";") if part.strip()]


def merge_entries(copies):
    # copies are sorted from most to least complete; the first one is the base.
    # A field missing in the base is taken from the next copy that has it.
    # Keyword and affiliation fields present in several copies are combined
    # without repeating items. For every other field the base value is kept
    base = copies[0]
    merged_fields = dict(base.fields)
    list_fields = set(bib_keyword_fields) | set(bib_affiliation_fields)
    added_by_copy = []
    for copy in copies[1:]:
        added = []
        for name, value in copy.fields.items():
            if not value:
                continue
            if not merged_fields.get(name):
                merged_fields[name] = value
                added.append(name)
            elif name in list_fields:
                items = split_list_value(merged_fields[name])
                seen = {normalize_for_matching(item).lower() for item in items}
                new_items = []
                for item in split_list_value(value):
                    item_norm = normalize_for_matching(item).lower()
                    if item_norm not in seen:
                        seen.add(item_norm)
                        new_items.append(item)
                if new_items:
                    merged_fields[name] = "; ".join(items + new_items)
                    added.append(f"{name} (+{len(new_items)} items)")
        added_by_copy.append(added)

    sources = []
    for copy in copies:
        for source in copy.source.split("; "):
            if source and source not in sources:
                sources.append(source)
    merged = BibEntry(base.entry_type, base.key, merged_fields, base.raw,
                      "; ".join(sources), min(copy.index for copy in copies))
    return merged, added_by_copy


# --- citation keys: year_firstauthor_journalabbreviation

def split_top_level_and(text):
    # Split an author list on " and " outside braces, so {Smith and Sons} stays one name
    parts = []
    depth = 0
    start = 0
    i = 0
    lower_text = text.lower()
    while i < len(text):
        char = text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0 and char.isspace():
            match = re.match(r"\s+and\s+", lower_text[i:])
            if match:
                parts.append(text[start:i])
                i += match.end()
                start = i
                continue
        i += 1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


def first_author_surname(entry):
    authors = entry.fields.get("author") or entry.fields.get("editor") or ""
    names = split_top_level_and(authors)
    if not names:
        return "anon"
    first = names[0]
    if first.startswith("{") and first.endswith("}"):
        # Corporate author written as {World Meteorological Organization}: first word
        words = clean_latex(first).split()
        surname = words[0] if words else ""
    elif "," in first:
        # "Last, First" form: everything before the comma
        surname = clean_latex(first.split(",")[0])
    else:
        # "First Last" form: the last word
        words = clean_latex(first).split()
        surname = words[-1] if words else ""
    surname = re.sub(r"[^a-z]", "", surname.lower())
    return surname or "anon"


def journal_abbreviation(entry):
    venue = next((entry.fields[name] for name in venue_fields if entry.fields.get(name)), "")
    venue = clean_latex(venue)
    if not venue:
        return re.sub(r"[^a-z0-9]", "", entry.entry_type.lower()) or "misc"

    # A manual abbreviation from config.py wins
    venue_norm = normalize_title(venue)
    for journal_name, abbreviation in journal_abbreviations.items():
        if normalize_title(journal_name) == venue_norm:
            return re.sub(r"[^a-z0-9]", "", str(abbreviation).lower())

    # Otherwise the initials of the significant words:
    # Journal of Aerosol Science gives jas, ATMOSPHERIC CHEMISTRY AND PHYSICS gives acp
    venue = re.sub(r"\([^)]*\)", " ", venue)
    words = [word for word in re.split(r"[^A-Za-z0-9]+", venue)
             if word and word.lower() not in journal_stopwords]
    if not words:
        return "misc"
    if len(words) == 1:
        # One word journals such as Nature or Science keep the whole word
        return words[0].lower()[:12]
    return "".join(word if word.isdigit() else word[0] for word in words).lower()


def generate_key(entry):
    year = extract_year(entry)
    return f"{year or 'noyear'}_{first_author_surname(entry)}_{journal_abbreviation(entry)}"


def assign_keys(entries):
    # Every entry gets a generated key; identical keys get _b, _c ... in file order
    used_keys = set()
    clashes = []
    for entry in entries:
        base_key = generate_key(entry)
        new_key = base_key
        suffix_number = 1
        while new_key in used_keys:
            suffix_number += 1
            letter = chr(ord("a") + suffix_number - 1) if suffix_number <= 26 else str(suffix_number)
            new_key = f"{base_key}_{letter}"
        if new_key != base_key:
            clashes.append((entry.key, new_key))
        entry.key = new_key
        used_keys.add(new_key)
    return clashes


def format_entry(entry):
    # Rebuild the BibTeX text from the fields; every value is written in braces
    items = []
    for name, value in entry.fields.items():
        if not value:
            continue
        if value.count("{") != value.count("}"):
            value = value.replace("{", "").replace("}", "")
        items.append((name, value))
    lines = [f"@{entry.entry_type}{{{entry.key},"]
    for position, (name, value) in enumerate(items):
        comma = "," if position < len(items) - 1 else ""
        lines.append(f"  {name} = {{{value}}}{comma}")
    lines.append("}")
    return "\n".join(lines)


def top_folder(source):
    return source.split("/")[0] if source else ""


def run_dedup(dry_run, entries=None, merge_report_lines=None):
    log("\n[stage 2] merging duplicates and generating keys")
    if entries is None:
        if not MASTER_BIB_FIRST_STAGE_FILE.exists():
            sys.exit(f"error: {MASTER_BIB_FIRST_STAGE_FILE} not found, run stage 1 first")
        entries, _ = read_bib_file(MASTER_BIB_FIRST_STAGE_FILE)
        for index, entry in enumerate(entries):
            entry.index = index

    dois = [extract_doi(entry) for entry in entries]
    years = [extract_year(entry) for entry in entries]
    titles = [normalize_title(field_text(entry, bib_title_fields)) for entry in entries]
    groups = DuplicateGroups(dois)
    union_counts = Counter()

    # --- pass 1: identical DOI
    doi_buckets = defaultdict(list)
    for index, doi in enumerate(dois):
        if doi:
            doi_buckets[doi].append(index)
    for members in doi_buckets.values():
        for other in members[1:]:
            if groups.union(members[0], other) == "merged":
                union_counts["doi"] += 1

    # --- pass 2: identical normalised title, years within the tolerance, no DOI conflict
    title_buckets = defaultdict(list)
    for index, title in enumerate(titles):
        if len(title) >= dedup_min_title_length:
            title_buckets[title].append(index)
    for members in title_buckets.values():
        # Pairs with the closest years are linked first, so an entry without DOI
        # joins the most likely copy when it fits more than one group
        candidate_pairs = []
        for position, first in enumerate(members):
            for second in members[position + 1:]:
                year_first, year_second = years[first], years[second]
                if year_first and year_second:
                    year_gap = abs(year_first - year_second)
                    if year_gap > dedup_year_tolerance:
                        continue
                else:
                    year_gap = dedup_year_tolerance + 1
                candidate_pairs.append((year_gap, first, second))
        for _, first, second in sorted(candidate_pairs):
            result = groups.union(first, second)
            if result == "merged":
                union_counts["title_year"] += 1
            elif result == "blocked":
                union_counts["blocked_by_doi"] += 1

    # --- merge the copies of every group into one entry
    members_by_root = defaultdict(list)
    for index in range(len(entries)):
        members_by_root[groups.find(index)].append(index)

    kept_entries = []
    group_records = []
    group_size_counts = Counter()
    cross_source_groups = 0
    exclusions = load_exclusions()
    removed_by_item = {number: [] for number in range(1, len(exclusions) + 1)}
    excluded_copies = 0
    for members in members_by_root.values():
        copies = sorted((entries[i] for i in members), key=completeness_key, reverse=True)
        # A listed paper is removed with all its duplicate copies, even copies without DOI
        group_matches = sorted({number for copy in copies for number in exclusion_matches(copy, exclusions)})
        if group_matches:
            excluded_copies += len(copies)
            for number in group_matches:
                removed_by_item[number] += [f"{copy.key}  [{copy.source}]" for copy in copies]
            continue
        if len(copies) == 1:
            kept_entries.append(copies[0])
            continue
        merged, added_by_copy = merge_entries(copies)
        kept_entries.append(merged)
        group_size_counts[len(copies)] += 1
        if len({top_folder(copy.source) for copy in copies}) > 1:
            cross_source_groups += 1
        group_records.append((merged, copies, [copy.key for copy in copies], added_by_copy))

    kept_entries.sort(key=lambda entry: entry.index)
    merged_away_count = len(entries) - excluded_copies - len(kept_entries)

    # --- new keys for all entries, then rebuild the BibTeX text
    key_clashes = assign_keys(kept_entries)
    for entry in kept_entries:
        entry.raw = format_entry(entry)

    duplicate_group_lines = []
    for merged, copies, old_keys, added_by_copy in group_records:
        title_preview = clean_latex(field_text(merged, bib_title_fields))[:90]
        duplicate_group_lines.append(f"   [{len(copies)}] {title_preview}")
        duplicate_group_lines.append(
            f"       doi: {extract_doi(merged) or 'none'}   year: {extract_year(merged) or 'none'}")
        duplicate_group_lines.append(f"       new key: {merged.key}")
        duplicate_group_lines.append(f"       base    {old_keys[0]}  [{copies[0].source}]")
        for copy, old_key, added in zip(copies[1:], old_keys[1:], added_by_copy):
            added_text = ", ".join(added) if added else "nothing new"
            duplicate_group_lines.append(f"       merged  {old_key}  [{copy.source}]  added: {added_text}")
        duplicate_group_lines.append("")

    stats_lines = [
        "STAGE 2: DEDUPLICATION",
        f"   entries in                          : {len(entries)}",
        f"   removed by noAIDA.txt (all copies)  : {excluded_copies}",
        f"   entries out (one per paper)         : {len(kept_entries)}",
        f"   duplicate copies merged into others : {merged_away_count}",
        f"   duplicate groups                    : {sum(group_size_counts.values())}",
        f"   groups found in both sources        : {cross_source_groups}",
        f"   links made by DOI                   : {union_counts['doi']}",
        f"   links made by title and year        : {union_counts['title_year']}",
        f"   title matches blocked (other DOI)   : {union_counts['blocked_by_doi']}",
        f"   entries without DOI (after merge)   : {sum(1 for e in kept_entries if not extract_doi(e))}",
        f"   generated keys with a suffix        : {len(key_clashes)}",
        "",
        "   duplicate group sizes",
    ]
    stats_lines += format_table(["copies per paper", "groups"],
                                [[size, count] for size, count in sorted(group_size_counts.items())])
    for line in stats_lines[:12]:
        log(line)
    log_exclusions(removed_by_item, excluded_copies)

    report_lines = [
        "AIDA master bibliography: merge and deduplication report",
        f"created {datetime.now():%Y-%m-%d %H:%M} by aida_master_bib.py",
        f"year tolerance for title matching: {dedup_year_tolerance}, "
        f"minimum normalised title length: {dedup_min_title_length}",
        "key format: year_firstauthor_journalabbreviation",
        "",
    ]
    if merge_report_lines:
        report_lines += merge_report_lines + [""]
    report_lines += stats_lines + [""]
    report_lines += exclusion_report_lines("EXCLUDED BY noAIDA.txt (stage 2)", removed_by_item, excluded_copies) + [""]
    if key_clashes:
        report_lines.append("   generated keys that needed a suffix (original key, new key)")
        report_lines += [f"       {old or '(no key)'}  to  {new}" for old, new in key_clashes]
        report_lines.append("")
    report_lines += ["DUPLICATE GROUPS (base = most complete copy, merged = copies whose fields were added)", ""]
    report_lines += duplicate_group_lines

    if dry_run:
        log(f"   dry run: {MASTER_BIB_NODUP_FILE} not written")
    else:
        write_bib_file(MASTER_BIB_NODUP_FILE, kept_entries, [
            "AIDA master bibliography, stage 2: duplicates merged, one entry per paper",
            f"created {datetime.now():%Y-%m-%d %H:%M} by aida_master_bib.py",
            f"entries: {len(kept_entries)} ({merged_away_count} duplicate copies merged)",
        ])
        MASTER_BIB_DEDUP_REPORT_FILE.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        log(f"   written: {MASTER_BIB_NODUP_FILE}")
        log(f"   written: {MASTER_BIB_DEDUP_REPORT_FILE}")
    return kept_entries


# ================================================== #
# STAGE 3: FILTERING
# ================================================== #

class TermMatcher:
    # Finds whole word occurrences of a list of terms in normalised text.
    # A term may not touch a letter or digit on either side, so AIDA matches
    # "AIDA chamber" and "AIDA-3" but not "AIDAN"

    def __init__(self, terms, ignore_case):
        self.ignore_case = ignore_case
        self.canonical = {}
        pieces = set()
        for term in terms:
            normalized = normalize_for_matching(str(term))
            if not normalized:
                continue
            lookup = normalized.lower() if ignore_case else normalized
            self.canonical.setdefault(lookup, str(term))
            pieces.add(normalized)
        self.regex = None
        if pieces:
            # Longer terms first so "Forschungszentrum Karlsruhe" wins over shorter overlaps
            alternatives = [r"\s+".join(re.escape(word) for word in piece.split())
                            for piece in sorted(pieces, key=len, reverse=True)]
            flags = re.IGNORECASE if ignore_case else 0
            self.regex = re.compile(
                r"(?<![A-Za-z0-9])(?:" + "|".join(alternatives) + r")(?![A-Za-z0-9])", flags)

    def find(self, text):
        found = set()
        if not self.regex or not text:
            return found
        for match in self.regex.finditer(text):
            lookup = " ".join(match.group(0).split())
            if self.ignore_case:
                lookup = lookup.lower()
            found.add(self.canonical.get(lookup, match.group(0)))
        return found


def load_campaign_names():
    # Collect unique names from the campaign column of every campaign CSV
    names = set()
    report_lines = []
    exclude_lower = {str(name).strip().lower() for name in campaign_exclude}
    for CAMPAIGN_FILE in CAMPAIGN_FILES:
        if not CAMPAIGN_FILE.exists():
            log(f"   warning: campaign file not found: {CAMPAIGN_FILE}")
            report_lines.append(f"   {CAMPAIGN_FILE.name}: file not found")
            continue
        with open(CAMPAIGN_FILE, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            column = next((name for name in (reader.fieldnames or [])
                           if name and name.strip().lower() == campaign_column.lower()), None)
            if column is None:
                log(f"   warning: no '{campaign_column}' column in {CAMPAIGN_FILE}")
                report_lines.append(f"   {CAMPAIGN_FILE.name}: column '{campaign_column}' missing")
                continue
            file_names = set()
            for row in reader:
                value = (row.get(column) or "").strip()
                if value and value.lower() not in ("nan", "none", "null"):
                    file_names.add(value)
        names |= file_names
        report_lines.append(f"   {CAMPAIGN_FILE.name}: {len(file_names)} unique campaign names")

    too_short = sorted(name for name in names if len(name) < campaign_min_length)
    excluded = sorted(name for name in names if name.lower() in exclude_lower)
    usable = sorted(name for name in names
                    if len(name) >= campaign_min_length and name.lower() not in exclude_lower)
    report_lines.append(f"   combined unique names: {len(names)}, used for matching: {len(usable)}")
    if too_short:
        report_lines.append(f"   ignored, shorter than {campaign_min_length} characters: {', '.join(too_short)}")
    if excluded:
        report_lines.append(f"   ignored, in campaign_exclude: {', '.join(excluded)}")
    return usable, report_lines


def location_label(locations):
    ordered = [name for name in location_names if name in locations]
    if not ordered:
        return "none"
    if len(ordered) == 1:
        return ordered[0] + " only"
    return " + ".join(ordered)


location_categories = [
    "title only", "abstract only", "keywords only",
    "title + abstract", "title + keywords", "abstract + keywords",
    "title + abstract + keywords", "none",
]


def evaluate_entry(entry, aida_matchers, campaign_matcher, kit_matchers, mode, use_kit_filter):
    texts = {
        "title": normalize_for_matching(field_text(entry, bib_title_fields)),
        "abstract": normalize_for_matching(field_text(entry, bib_abstract_fields)),
        "keywords": normalize_for_matching(field_text(entry, bib_keyword_fields)),
    }
    aida_hits = {}
    campaign_hits = {}
    for location in location_names:
        found = set()
        for matcher in aida_matchers:
            found |= matcher.find(texts[location])
        aida_hits[location] = found
        campaign_hits[location] = campaign_matcher.find(texts[location]) if campaign_matcher else set()

    aida_locations = {loc for loc in location_names if aida_hits[loc]}
    campaign_locations = {loc for loc in location_names if campaign_hits[loc]}
    term_rule = filter_definitions[mode]["terms"]
    if term_rule == "aida":
        filter_locations = aida_locations
    elif term_rule == "campaign":
        filter_locations = campaign_locations
    elif term_rule == "aida_or_campaign":
        filter_locations = aida_locations | campaign_locations
    else:
        # Both an AIDA term and a campaign name are needed; otherwise nothing counts
        filter_locations = aida_locations | campaign_locations if aida_locations and campaign_locations else set()

    affiliation_text = normalize_for_matching(field_text(entry, bib_affiliation_fields))
    kit_terms = set()
    for matcher in kit_matchers:
        kit_terms |= matcher.find(affiliation_text)
    has_affiliation = bool(affiliation_text)

    term_pass = bool(filter_locations)
    if not use_kit_filter or kit_terms:
        kit_pass = True
    else:
        kit_pass = (not has_affiliation) and keep_if_affiliation_missing

    # Removal reason, e.g. "no campaign name" or "no AIDA term; no KIT affiliation"
    reason_parts = []
    if not term_pass:
        if term_rule == "aida_or_campaign":
            reason_parts.append("no AIDA term or campaign name")
        else:
            if term_rule in ("aida", "aida_and_campaign") and not aida_locations:
                reason_parts.append("no AIDA term")
            if term_rule in ("campaign", "aida_and_campaign") and not campaign_locations:
                reason_parts.append("no campaign name")
    if not kit_pass:
        reason_parts.append("no KIT affiliation" if has_affiliation
                            else "no KIT affiliation (affiliation field missing)")
    reason = "; ".join(reason_parts)

    return {
        "entry": entry,
        "texts": texts,
        "aida_locations": aida_locations,
        "aida_terms": set().union(*aida_hits.values()),
        "campaign_locations": campaign_locations,
        "campaigns": set().union(*campaign_hits.values()),
        "filter_locations": filter_locations,
        "has_affiliation": has_affiliation,
        "kit_terms": kit_terms,
        "term_pass": term_pass,
        "kit_pass": kit_pass,
        "kept": term_pass and kit_pass,
        "reason": reason,
    }


def location_breakdown_rows(columns):
    # columns: list of (header, list of location sets); returns the table header and rows
    header = ["found in"] + [name for name, _ in columns]
    counters = [Counter(location_label(locs) for locs in sets) for _, sets in columns]
    totals = [len(sets) for _, sets in columns]
    rows = []
    for category in location_categories:
        rows.append([category] + [f"{c[category]} ({percent(c[category], t)})"
                                  for c, t in zip(counters, totals)])
    rows.append(["", *[""] * len(columns)])
    for field_count, label in ((1, "in one field"), (2, "in two fields"), (3, "in all three fields")):
        values = [sum(1 for locs in sets if len(locs) == field_count) for _, sets in columns]
        rows.append([label] + [f"{v} ({percent(v, t)})" for v, t in zip(values, totals)])
    none_values = [sum(1 for locs in sets if not locs) for _, sets in columns]
    rows.append(["not found"] + [f"{v} ({percent(v, t)})" for v, t in zip(none_values, totals)])
    return header, rows


def filter_rule_text(mode):
    term_rule = filter_definitions[mode]["terms"]
    rule_text = f"{term_rule_texts[term_rule]} in title/abstract/keywords"
    rule_text += " AND KIT affiliation" if filter_definitions[mode]["kit"] else ", no affiliation check"
    return rule_text


def build_filter_summary(results, mode, use_kit_filter, campaign_names, campaign_report_lines, OUTPUT_FILE,
                         exclusion_lines):
    total = len(results)
    kept = [r for r in results if r["kept"]]
    lines = [
        "AIDA master bibliography: filter summary",
        f"created {datetime.now():%Y-%m-%d %H:%M} by aida_master_bib.py",
        f"input : {MASTER_BIB_NODUP_FILE}",
        f"output: {OUTPUT_FILE}",
        "",
        *exclusion_lines,
        "",
        "FILTER SETTINGS",
        f"   filter                      : Filter {mode}",
    ]
    term_rule = filter_definitions[mode]["terms"]
    uses_campaigns = "campaign" in term_rule
    lines.append(f"   rule                        : {filter_rule_text(mode)}")
    lines += [
        f"   AIDA terms (exact case)     : {', '.join(aida_keywords_exact_case) or 'none'}",
        f"   AIDA terms (any case)       : {', '.join(aida_keywords) or 'none'}",
        f"   KIT affiliation check       : {'on' if use_kit_filter else 'off'}",
        f"   KIT terms (any case)        : {', '.join(kit_affiliation) or 'none'}",
        f"   KIT terms (exact case)      : {', '.join(kit_affiliation_exact_case) or 'none'}",
        f"   keep if affiliation missing : {keep_if_affiliation_missing}",
        f"   title fields                : {', '.join(bib_title_fields)}",
        f"   abstract fields             : {', '.join(bib_abstract_fields)}",
        f"   keyword fields              : {', '.join(bib_keyword_fields)}",
        f"   affiliation fields          : {', '.join(bib_affiliation_fields)}",
    ]
    if uses_campaigns:
        lines += [f"   campaign matching           : {'exact case' if campaign_exact_case else 'any case'}"]
        lines += campaign_report_lines

    # --- field availability
    lines += ["", "FIELD AVAILABILITY (all entries)"]
    availability = [
        ("title", sum(1 for r in results if r["texts"]["title"])),
        ("abstract", sum(1 for r in results if r["texts"]["abstract"])),
        ("keywords", sum(1 for r in results if r["texts"]["keywords"])),
        ("affiliation", sum(1 for r in results if r["has_affiliation"])),
    ]
    lines += format_table(["field", "entries with field"],
                          [[name, f"{count} ({percent(count, total)})"] for name, count in availability])

    # --- where the terms were found
    lines += ["", "WHERE THE TERMS WERE FOUND"]
    if term_rule == "aida":
        columns = [
            ("AIDA terms, all entries", [r["aida_locations"] for r in results]),
            ("AIDA terms, kept entries", [r["aida_locations"] for r in kept]),
        ]
    elif term_rule == "campaign":
        columns = [
            ("campaigns, all entries", [r["campaign_locations"] for r in results]),
            ("campaigns, kept entries", [r["campaign_locations"] for r in kept]),
            ("AIDA terms, all entries", [r["aida_locations"] for r in results]),
        ]
    else:
        joiner = "or" if term_rule == "aida_or_campaign" else "and"
        columns = [
            (f"AIDA {joiner} campaign, all", [r["filter_locations"] for r in results]),
            (f"AIDA {joiner} campaign, kept", [r["filter_locations"] for r in kept]),
            ("AIDA terms, all", [r["aida_locations"] for r in results]),
            ("campaigns, all", [r["campaign_locations"] for r in results]),
        ]
    header, rows = location_breakdown_rows(columns)
    lines += format_table(header, rows)

    if uses_campaigns:
        lines += ["", "MATCH TYPE (all entries)"]
        match_types = Counter()
        for r in results:
            has_aida, has_campaign = bool(r["aida_locations"]), bool(r["campaign_locations"])
            if has_aida and has_campaign:
                match_types["AIDA term and campaign"] += 1
            elif has_aida:
                match_types["AIDA term only"] += 1
            elif has_campaign:
                match_types["campaign only"] += 1
            else:
                match_types["no match"] += 1
        lines += format_table(["match type", "entries"], [
            [name, f"{match_types[name]} ({percent(match_types[name], total)})"]
            for name in ("AIDA term only", "campaign only", "AIDA term and campaign", "no match")
        ])
        campaign_all = Counter(name for r in results for name in r["campaigns"])
        campaign_kept = Counter(name for r in kept for name in r["campaigns"])
        lines += ["", "CAMPAIGN NAMES FOUND (entries per campaign)"]
        if campaign_all:
            lines += format_table(["campaign", "all entries", "kept entries"], [
                [name, count, campaign_kept[name]] for name, count in campaign_all.most_common()
            ])
        else:
            lines.append("   no campaign name found in any entry")

    # --- AIDA term frequency
    aida_term_counts = Counter(term for r in results for term in r["aida_terms"])
    lines += ["", "AIDA TERMS FOUND (entries per term)"]
    if aida_term_counts:
        lines += format_table(["term", "entries"], [[t, c] for t, c in aida_term_counts.most_common()])
    else:
        lines.append("   no AIDA term found in any entry")

    # --- affiliation check
    kit_found = sum(1 for r in results if r["kit_terms"])
    affiliation_no_kit = sum(1 for r in results if r["has_affiliation"] and not r["kit_terms"])
    affiliation_missing = sum(1 for r in results if not r["has_affiliation"])
    lines += ["", "KIT AFFILIATION (all entries)"]
    lines += format_table(["status", "entries"], [
        ["KIT affiliation found", f"{kit_found} ({percent(kit_found, total)})"],
        ["affiliation present, no KIT", f"{affiliation_no_kit} ({percent(affiliation_no_kit, total)})"],
        ["affiliation field missing", f"{affiliation_missing} ({percent(affiliation_missing, total)})"],
    ])

    # --- cross table terms versus affiliation
    lines += ["", "TERM MATCH VERSUS KIT AFFILIATION (all entries)"]
    cross = Counter((r["term_pass"], r["kit_pass"]) for r in results)
    lines += format_table(["", "KIT pass", "KIT fail"], [
        ["term match", cross[(True, True)], cross[(True, False)]],
        ["no term match", cross[(False, True)], cross[(False, False)]],
    ])

    # --- result
    reasons = Counter(r["reason"] for r in results if not r["kept"])
    lines += [
        "",
        "RESULT",
        f"   entries in       : {total}",
        f"   entries kept     : {len(kept)} ({percent(len(kept), total)})",
        f"   entries removed  : {total - len(kept)} ({percent(total - len(kept), total)})",
        "",
        "   removal reasons",
    ]
    lines += format_table(["reason", "entries"], [[reason, count] for reason, count in reasons.most_common()])
    return lines


def write_filter_log(results, LOG_FILE):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["key", "entry_type", "year", "doi", "title", "source",
                         "kept", "reason", "filter_locations", "aida_locations", "aida_terms",
                         "campaign_locations", "campaigns", "has_affiliation", "kit_terms"])
        for r in results:
            entry = r["entry"]
            writer.writerow([
                entry.key, entry.entry_type, extract_year(entry) or "", extract_doi(entry),
                clean_latex(field_text(entry, bib_title_fields)), entry.source,
                r["kept"], r["reason"], location_label(r["filter_locations"]),
                location_label(r["aida_locations"]), "; ".join(sorted(r["aida_terms"])),
                location_label(r["campaign_locations"]), "; ".join(sorted(r["campaigns"])),
                r["has_affiliation"], "; ".join(sorted(r["kit_terms"])),
            ])


def filter_output_files(mode, several):
    # One filter writes the standard names. With several filters every name gets
    # _filterN after the master stem, e.g. AIDA_Master_bib_filter4_removed.bib
    FILES = {
        "bib": MASTER_BIB_FILE,
        "summary": MASTER_BIB_FILTER_SUMMARY_FILE,
        "log": MASTER_BIB_FILTER_LOG_FILE,
        "removed": MASTER_BIB_REMOVED_FILE,
    }
    if not several:
        return FILES
    master_stem = MASTER_BIB_FILE.stem
    renamed = {}
    for name, FILE_PATH in FILES.items():
        if FILE_PATH.stem.startswith(master_stem):
            new_stem = f"{master_stem}_filter{mode}{FILE_PATH.stem[len(master_stem):]}"
        else:
            new_stem = f"{FILE_PATH.stem}_filter{mode}"
        # the per filter files are steps on the way, the master folder holds only the final master
        renamed[name] = MASTER_BIB_SUPPLEMENTARY_DIR / (new_stem + FILE_PATH.suffix)
    return renamed


def run_filter(dry_run, mode, no_kit_flag, entries, FILES, exclusion_lines, forced=None):
    log(f"\n[stage 3] filtering with Filter {mode}")
    # The affiliation is checked only for filters defined with kit True
    use_kit_filter = require_kit_affiliation and not no_kit_flag and filter_definitions[mode]["kit"]

    # --- build matchers
    aida_matchers = [TermMatcher(aida_keywords, ignore_case=True),
                     TermMatcher(aida_keywords_exact_case, ignore_case=False)]
    kit_matchers = [TermMatcher(kit_affiliation, ignore_case=True),
                    TermMatcher(kit_affiliation_exact_case, ignore_case=False)]
    campaign_matcher = None
    campaign_names, campaign_report_lines = [], []
    if "campaign" in filter_definitions[mode]["terms"]:
        campaign_names, campaign_report_lines = load_campaign_names()
        for line in campaign_report_lines:
            log(line)
        campaign_matcher = TermMatcher(campaign_names, ignore_case=not campaign_exact_case)

    # --- evaluate every entry
    results = [evaluate_entry(entry, aida_matchers, campaign_matcher, kit_matchers, mode, use_kit_filter)
               for entry in entries]
    # --- a paper confirmed by an author is kept whatever the filter says
    forced = forced or {}
    for r in results:
        if id(r["entry"]) in forced and not r["kept"]:
            r["kept"] = True
            r["reason"] = ""
    confirmed_notes = {key: "confirmed by: " + names for key, names in forced.items()}
    kept_entries = [r["entry"] for r in results if r["kept"]]
    removed_entries = [r["entry"] for r in results if not r["kept"]]
    for r in results:
        if r["kept"]:
            log(f"   keep  {r['entry'].key}  [{location_label(r['filter_locations'])}]", detail=True)

    summary_lines = build_filter_summary(results, mode, use_kit_filter, campaign_names,
                                         campaign_report_lines, FILES["bib"], exclusion_lines)
    log(f"   entries in: {len(entries)}, kept: {len(kept_entries)}, removed: {len(removed_entries)}")

    if dry_run:
        log("\n" + "\n".join(summary_lines))
        log(f"\n   dry run: {FILES['bib']} and reports not written")
        return kept_entries

    write_bib_file(FILES["bib"], kept_entries, [
        f"AIDA master bibliography, stage 3: Filter {mode}",
        f"created {datetime.now():%Y-%m-%d %H:%M} by aida_master_bib.py",
        f"entries: {len(kept_entries)} of {len(entries)}",
    ], confirmed_notes)
    FILES["summary"].parent.mkdir(parents=True, exist_ok=True)
    FILES["summary"].write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    write_filter_log(results, FILES["log"])
    log(f"   written: {FILES['bib']}")
    log(f"   written: {FILES['summary']}")
    log(f"   written: {FILES['log']}")
    if save_removed_entries:
        write_bib_file(FILES["removed"], removed_entries, [
            f"AIDA master bibliography: entries removed by Filter {mode}",
            f"created {datetime.now():%Y-%m-%d %H:%M} by aida_master_bib.py",
            f"entries: {len(removed_entries)}",
        ])
        log(f"   written: {FILES['removed']}")
    return kept_entries


def run_filters(dry_run, modes, no_kit_flag, entries=None):
    # Check all filter numbers before any work is done
    for mode in modes:
        if mode not in filter_definitions:
            sys.exit(f"error: master_bib_filter must be one of {', '.join(filter_definitions)}, got '{mode}'")
    if not modes:
        sys.exit("error: master_bib_filter is empty")
    if entries is None:
        if not MASTER_BIB_NODUP_FILE.exists():
            sys.exit(f"error: {MASTER_BIB_NODUP_FILE} not found, run stage 2 first")
        entries, _ = read_bib_file(MASTER_BIB_NODUP_FILE)

    # --- remove papers listed in noAIDA.txt again, in case the list changed after stage 2
    log("\n[stage 3] checking noAIDA.txt")
    entries, removed_by_item, removed_count = apply_exclusions(entries)
    log_exclusions(removed_by_item, removed_count)
    exclusion_lines = exclusion_report_lines("EXCLUDED BY noAIDA.txt (before filtering)", removed_by_item, removed_count)

    # --- the authors' confirmed lists decide before the filters
    log("\n[stage 3] applying the authors' confirmed lists")
    entries, forced, author_lines, decisions = apply_author_lists(entries)
    if author_lines:
        exclusion_lines = exclusion_lines + [""] + author_lines
        if not dry_run:
            write_author_log(decisions)

    several = len(modes) > 1
    kept_by_mode = {}
    for mode in modes:
        kept_by_mode[mode] = run_filter(dry_run, mode, no_kit_flag, entries,
                                        filter_output_files(mode, several), exclusion_lines, forced)
    if several:
        final_entries = combine_filters(dry_run, modes, entries, kept_by_mode, exclusion_lines, forced)
    else:
        final_entries = kept_by_mode[modes[0]]

    # --- one master per AIDA researcher, taken from the final master
    write_author_masters(dry_run, final_entries, forced)


# --- combining several filters

def combine_filters(dry_run, modes, entries, kept_by_mode, exclusion_lines, forced=None):
    filter_names = ", ".join(f"Filter {mode}" for mode in modes)
    log(f"\n[stage 3] combining {filter_names}")

    # Which filters kept each entry; the combined file keeps the NoDuplicate order
    modes_by_entry = defaultdict(list)
    for mode in modes:
        for entry in kept_by_mode[mode]:
            modes_by_entry[id(entry)].append(mode)
    combined_entries = [entry for entry in entries if id(entry) in modes_by_entry]
    forced = forced or {}
    entry_notes = {id(entry): ("confirmed by: " + forced[id(entry)]) if id(entry) in forced
                   else "kept by: " + ", ".join(f"Filter {mode}" for mode in modes_by_entry[id(entry)])
                   for entry in combined_entries}

    total = len(entries)
    lines = [
        "AIDA master bibliography: combined filter summary",
        f"created {datetime.now():%Y-%m-%d %H:%M} by aida_master_bib.py",
        f"input : {MASTER_BIB_NODUP_FILE}",
        f"output: {MASTER_BIB_FILE}",
        "",
        *exclusion_lines,
        "",
        "SELECTED FILTERS",
    ]
    lines += format_table(["filter", "rule", "kept"], [
        [f"Filter {mode}", filter_rule_text(mode), f"{len(kept_by_mode[mode])} ({percent(len(kept_by_mode[mode]), total)})"]
        for mode in modes
    ], left_columns=2)

    # --- overlap between the filters
    combination_counts = Counter(" + ".join(f"Filter {mode}" for mode in modes_by_entry[id(entry)])
                                 for entry in combined_entries)
    lines += ["", "KEPT BY WHICH FILTERS"]
    lines += format_table(["filters", "entries"], [[name, count] for name, count in combination_counts.most_common()])
    kept_by_all = sum(1 for entry in combined_entries if len(modes_by_entry[id(entry)]) == len(modes))
    lines += [
        "",
        "RESULT",
        f"   entries in                      : {total}",
        f"   entries kept by all filters     : {kept_by_all}",
        f"   entries in the combined file    : {len(combined_entries)} ({percent(len(combined_entries), total)})",
        f"   entries removed by every filter : {total - len(combined_entries)}",
        "",
        "COMBINED ENTRIES",
    ]
    lines += format_table(["key", "kept by", "title"], [
        [entry.key, ", ".join(modes_by_entry[id(entry)]), clean_latex(field_text(entry, bib_title_fields))[:80]]
        for entry in combined_entries
    ], left_columns=3)
    log(f"   combined entries: {len(combined_entries)} of {total}")

    if dry_run:
        log("\n" + "\n".join(lines))
        log(f"\n   dry run: {MASTER_BIB_FILE} not written")
        return combined_entries

    write_bib_file(MASTER_BIB_FILE, combined_entries, [
        f"AIDA master bibliography, stage 3: combined {filter_names}",
        f"created {datetime.now():%Y-%m-%d %H:%M} by aida_master_bib.py",
        f"entries: {len(combined_entries)} of {total}",
    ], entry_notes)
    MASTER_BIB_COMBINED_SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"   written: {MASTER_BIB_FILE}")
    log(f"   written: {MASTER_BIB_COMBINED_SUMMARY_FILE}")
    return combined_entries


# ================================================== #
# AUTHORS' CONFIRMED LISTS
# ================================================== #

# The lists the authors confirmed in step 2 decide before the filters do.
# A paper in a list is kept whatever the filters say; a paper an author removed
# is removed; a paper with an author on it whose list is "complete" but does not
# hold it is removed. Papers without such an author are left to the filters.

def fold_name(text):
    # "Möhler", "Moehler" and "Mohler" all become "mohler"
    text = clean_latex(text or "").lower()
    for long_form, short_form in (("ae", "a"), ("oe", "o"), ("ue", "u")):
        text = text.replace(long_form, short_form)
    return re.sub(r"[^a-z]", "", text)


def name_parts(name):
    # (folded surname, first initial) from "Last, First" or "First Last"
    name = clean_latex(name or "").strip()
    if "," in name:
        surname, _, given = name.partition(",")
    else:
        words = name.split()
        surname, given = (words[-1], " ".join(words[:-1])) if words else ("", "")
    given = given.strip()
    return fold_name(surname), (given[:1].upper() if given else "")


def has_author(entry, surname, initial):
    wanted = (fold_name(surname), initial.upper())
    names = split_top_level_and(entry.fields.get("author", ""))
    return any(name_parts(name) == wanted for name in names)


def read_csv_rows(CSV_PATH):
    # a table saved by Excel on Windows may be cp1252 and use ; as separator
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


def load_author_lists():
    # every configured author whose bib file exists; the others are reported and skipped
    lists = []
    for item in author_confirmed_lists:
        BIB_PATH = Path(item["bib"])
        if not BIB_PATH.exists():
            log(f"   {item['name']}: no confirmed list yet ({BIB_PATH.name}), the filters decide")
            continue
        confirmed, _ = read_bib_file(BIB_PATH, f"author_self_correction/{BIB_PATH.name}")
        removed = []
        if item.get("removed") and Path(item["removed"]).exists():
            for row in read_csv_rows(Path(item["removed"])):
                if (row.get("change") or "").strip() == "removed":
                    year = year_re.search(row.get("year") or "")
                    removed.append({"doi": normalize_doi(row.get("doi") or ""),
                                    "citation": normalize_title(row.get("citation") or ""),
                                    "year": int(year.group(0)) if year else None,
                                    "reason": row.get("reason") or "removed by the author"})
        lists.append(dict(item, confirmed=confirmed, removed_rows=removed))
        log(f"   {item['name']}: {len(confirmed)} confirmed papers, {len(removed)} removed")
    return lists


def same_paper(entry, other):
    # one DOI, or one normalised title with years at most one apart
    doi, other_doi = extract_doi(entry), extract_doi(other)
    if doi and other_doi:
        return doi == other_doi
    title, other_title = normalize_title(field_text(entry, bib_title_fields)), \
        normalize_title(field_text(other, bib_title_fields))
    if len(title) < dedup_min_title_length or title != other_title:
        return False
    year, other_year = extract_year(entry), extract_year(other)
    return year is None or other_year is None or abs(year - other_year) <= dedup_year_tolerance


def removal_matches(entry, row):
    # a removed row of the author's table: by DOI, or the entry's title inside its citation
    doi = extract_doi(entry)
    if row["doi"] and doi:
        return row["doi"] == doi
    title = normalize_title(field_text(entry, bib_title_fields))
    year = extract_year(entry)
    return (len(title) >= dedup_min_title_length and title in row["citation"]
            and (row["year"] is None or year is None or row["year"] == year))


def with_confirmation(entry, names):
    # the entry with the aida_confirmed_by field, its BibTeX text rebuilt
    fields = {name: value for name, value in entry.fields.items() if name not in author_bookkeeping_fields}
    fields["aida_confirmed_by"] = "; ".join(names)
    copy = BibEntry(entry.entry_type, entry.key, fields, "", entry.source, entry.index)
    copy.raw = format_entry(copy)
    return copy


def apply_author_lists(entries):
    # returns (entries for the filters, {id: confirming authors}, report lines, decision rows)
    lists = load_author_lists()
    if not lists:
        return entries, {}, [], []

    decisions = []
    confirmed_by = defaultdict(list)
    matched = set()

    # --- 1, the confirmed papers that are in the master
    for item in lists:
        for confirmed in item["confirmed"]:
            hits = [entry for entry in entries if same_paper(entry, confirmed)]
            for entry in hits:
                confirmed_by[id(entry)].append(item["name"])
            if hits:
                matched.add((item["name"], id(confirmed)))

    # --- 2, the confirmed papers no harvest found, added once even if several authors list them
    added = []
    for item in lists:
        for confirmed in item["confirmed"]:
            if (item["name"], id(confirmed)) in matched:
                continue
            twin = next((entry for entry in added if same_paper(entry, confirmed)), None)
            if twin is not None:
                confirmed_by[id(twin)].append(item["name"])
                continue
            added.append(confirmed)
            confirmed_by[id(confirmed)].append(item["name"])

    # --- 3, removals and rejections, only for papers nobody confirmed
    kept = []
    conflicts = []
    removed_count = Counter()
    for entry in entries:
        removers = []
        for item in lists:
            for row in item["removed_rows"]:
                if removal_matches(entry, row):
                    removers.append((item["name"], "removed: " + row["reason"]))
                    break
            else:
                in_scope = item.get("scope") == "all" or entry.entry_type == "article"
                if (item.get("complete") and in_scope and id(entry) not in confirmed_by
                        and has_author(entry, item["surname"], item["initial"])):
                    removers.append((item["name"], "not in the author's complete list"))
        title = clean_latex(field_text(entry, bib_title_fields))[:90]
        if id(entry) in confirmed_by:
            kept.append(entry)
            if removers:
                conflicts.append(entry)
                decisions.append([entry.key, extract_doi(entry), extract_year(entry) or "", "conflict, kept",
                                  "; ".join(confirmed_by[id(entry)]),
                                  "; ".join(f"{name} {why}" for name, why in removers), title])
            else:
                decisions.append([entry.key, extract_doi(entry), extract_year(entry) or "", "confirmed",
                                  "; ".join(confirmed_by[id(entry)]), "", title])
        elif removers:
            for name, why in removers:
                removed_count[(name, why.split(":")[0])] += 1
            decisions.append([entry.key, extract_doi(entry), extract_year(entry) or "", "removed",
                              "; ".join(name for name, _ in removers),
                              "; ".join(why for _, why in removers), title])
        else:
            kept.append(entry)

    # --- the added papers get master style keys, then all confirmed papers get the field
    used_keys = {entry.key for entry in kept}
    for entry in added:
        base_key = generate_key(entry)
        new_key, number = base_key, 1
        while new_key in used_keys:
            number += 1
            new_key = f"{base_key}_{chr(ord('a') + number - 1)}"
        entry.key = new_key
        used_keys.add(new_key)
        decisions.append([entry.key, extract_doi(entry), extract_year(entry) or "", "added",
                          "; ".join(confirmed_by[id(entry)]), "not found by the harvests",
                          clean_latex(field_text(entry, bib_title_fields))[:90]])

    forced = {}
    final = []
    for entry in kept + added:
        if id(entry) in confirmed_by:
            entry = with_confirmation(entry, list(dict.fromkeys(confirmed_by[id(entry)])))
            forced[id(entry)] = entry.fields["aida_confirmed_by"]
        final.append(entry)

    # --- report
    lines = ["AUTHORS' CONFIRMED LISTS (before filtering)"]
    for item in lists:
        name = item["name"]
        lines.append(f"   {name}: {len(item['confirmed'])} confirmed papers in his list "
                     f"(scope {item.get('scope')}, {'complete' if item.get('complete') else 'reviewed'})")
    lines += [
        f"   confirmed papers kept, filters skipped : {len(forced)}",
        f"     of those added, not found by harvests: {len(added)}",
        f"   removed by an author                   : {sum(c for (n, w), c in removed_count.items() if w == 'removed')}",
        f"   removed, not in a complete list        : {sum(c for (n, w), c in removed_count.items() if w != 'removed')}",
        f"   conflicts between authors, kept        : {len(conflicts)}",
        f"   every decision: {MASTER_BIB_AUTHOR_LOG_FILE.name}",
    ]
    for line in lines:
        log(line)
    return final, forced, lines, decisions


def write_author_log(decisions):
    MASTER_BIB_AUTHOR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MASTER_BIB_AUTHOR_LOG_FILE, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["key", "doi", "year", "decision", "by", "reason", "title"])
        writer.writerows(sorted(decisions, key=lambda row: (row[3], str(row[2]), row[0])))
    log(f"   written: {MASTER_BIB_AUTHOR_LOG_FILE}")


# ================================================== #
# ONE MASTER PER AUTHOR
# ================================================== #

def researcher_names():
    # one name per line, "First Last"; blank lines and lines starting with # are skipped
    candidates = [RESEARCHERS_FILE] + [RESEARCHERS_FILE.parent / name for name in researchers_file_variants]
    FILE_PATH = next((candidate for candidate in candidates if candidate.is_file()), None)
    if FILE_PATH is None:
        return [], RESEARCHERS_FILE
    names = []
    for line in read_text_file(FILE_PATH).splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line not in names:
            names.append(line)
    return names, FILE_PATH


def file_name_for(name):
    # "Ottmar Möhler" becomes "Ottmar_Moehler"; umlauts are written out, as in the authors' lists
    text = name.replace("ß", "ss")
    for source, target in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue")):
        text = text.replace(source, target)
    text = strip_diacritics(text)
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "author"


def write_author_masters(dry_run, final_entries, forced):
    # every researcher of AIDA Scientists.txt gets the papers of the final master he is an author of
    log("\n[stage 3] one master per author")
    names, NAMES_PATH = researcher_names()
    if not names:
        log(f"   {NAMES_PATH.name} not found, no author masters written")
        return
    log(f"   researchers: {len(names)} in {NAMES_PATH.name}")

    rows = []
    found = []
    for name in names:
        surname, initial = name_parts(name)
        if not surname or not initial:
            rows.append([name, 0, "", "name not understood, write it as First Last"])
            continue
        papers = [entry for entry in final_entries if has_author(entry, surname, initial)]
        if not papers:
            rows.append([name, 0, "", "no paper in the master"])
            continue
        confirmed = sum(1 for entry in papers if id(entry) in forced)
        FILE_PATH = MASTER_BIB_AUTHORS_DIR / (file_name_for(name) + ".bib")
        rows.append([name, len(papers), FILE_PATH.name,
                     f"{confirmed} confirmed by an author" if confirmed else ""])
        found.append((name, FILE_PATH, papers))

    lines = [
        "AIDA master bibliography: one master per author",
        f"created {datetime.now():%Y-%m-%d %H:%M} by 3_a_aida_master_bib.py",
        f"researchers: {NAMES_PATH}",
        f"master     : {MASTER_BIB_FILE}",
        f"folder     : {MASTER_BIB_AUTHORS_DIR}",
        "",
        "An author is found by surname and first initial, so R. Wagner and S. Wagner",
        "are told apart; umlauts and their spellings (oe, o) count as one.",
        "",
    ]
    lines += format_table(["researcher", "papers", "file", "note"],
                          sorted(rows, key=lambda row: (-row[1], row[0])), left_columns=1)
    log(f"   files: {len(found)}, researchers without a paper: {len(names) - len(found)}")

    if dry_run:
        log(f"   dry run: {MASTER_BIB_AUTHORS_DIR} not written")
        return

    # --- the folder belongs to this script; files of earlier runs are replaced
    MASTER_BIB_AUTHORS_DIR.mkdir(parents=True, exist_ok=True)
    for OLD_FILE in MASTER_BIB_AUTHORS_DIR.glob("*.bib"):
        OLD_FILE.unlink()
    notes = {key: "confirmed by: " + names_text for key, names_text in forced.items()}
    for name, FILE_PATH, papers in found:
        write_bib_file(FILE_PATH, papers, [
            f"AIDA master bibliography of {name}",
            f"created {datetime.now():%Y-%m-%d %H:%M} by 3_a_aida_master_bib.py",
            f"entries: {len(papers)}, taken from {MASTER_BIB_FILE.name}",
        ], notes)
    MASTER_BIB_AUTHORS_SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MASTER_BIB_AUTHORS_SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"   written: {len(found)} files in {MASTER_BIB_AUTHORS_DIR}")
    log(f"   written: {MASTER_BIB_AUTHORS_SUMMARY_FILE}")


# ================================================== #
# MAIN
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(description="Merge, deduplicate and filter the AIDA BibTeX harvests.")
    parser.add_argument("--stage", choices=["all", "merge", "dedup", "filter"], default="all",
                        help="stage to run; dedup and filter read the previous stage file")
    parser.add_argument("--filter", nargs="+", choices=list(filter_definitions), default=None,
                        help="override master_bib_filter from config.py, e.g. --filter 4 5")
    parser.add_argument("--no-kit-filter", action="store_true",
                        help="do not require a KIT affiliation (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="run everything but write no files")
    parser.add_argument("--verbose", action="store_true", help="print per file and per entry details")
    return parser.parse_args()


def main():
    global verbose_mode
    args = parse_arguments()
    verbose_mode = args.verbose
    modes = parse_filter_list(args.filter) if args.filter else filter_modes

    log("aida_master_bib.py")
    log(f"   config.py    : {'loaded' if config is not None else 'not found, using defaults'}")
    log(f"   data folder  : {AIDA_DATA_DIR}")
    log(f"   output folder: {MASTER_BIB_DIR}")
    log(f"      master       : {MASTER_BIB_MAIN_DIR.name}")
    log(f"      authors      : {MASTER_BIB_AUTHORS_DIR.name}")
    log(f"      supplementary: {MASTER_BIB_SUPPLEMENTARY_DIR.name}")

    if not args.dry_run:
        # Create the output folders if they do not exist yet
        for FOLDER in (MASTER_BIB_MAIN_DIR, MASTER_BIB_AUTHORS_DIR, MASTER_BIB_SUPPLEMENTARY_DIR):
            FOLDER.mkdir(parents=True, exist_ok=True)

    merged_entries, merge_report_lines, nodup_entries = None, None, None
    if args.stage in ("all", "merge"):
        merged_entries, merge_report_lines = run_merge(args.dry_run)
    if args.stage in ("all", "dedup"):
        nodup_entries = run_dedup(args.dry_run, merged_entries, merge_report_lines)
    if args.stage in ("all", "filter"):
        run_filters(args.dry_run, modes, args.no_kit_filter, nodup_entries)
    log("\ndone")


if __name__ == "__main__":
    main()
