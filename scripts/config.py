#!/usr/bin/env python3
r'''
config.py

   Central configuration for the AIDA publication pipeline.

Purpose

   Single importable source of truth for every aida script: project paths,
   filter rules, measurement archive credentials and external API settings.
   No script defines its own paths or rules; everything is read from here.

   The paths work on Linux, macOS and Windows without any editing.
   The project root is found from the location of this file:
      <root>/scripts/config.py   the root is the folder above scripts
      <root>/config.py           the root is the folder holding this file
   So a fresh git clone runs as it is, wherever it is placed.
   To keep the data somewhere else, set the environment variable AIDA_ROOT:
      Linux / macOS   export AIDA_ROOT=/data/AIDA
      Windows cmd     set AIDA_ROOT=D:\AIDA
      PowerShell      $env:AIDA_ROOT = "D:\AIDA"

Usage

   import config
   from config import INFO_DIR, keyword_terms
   python config.py        path and settings sanity check

Output

   no data files are written by this module
   stdout report when the file is executed directly
   ensure_dirs() creates the directories listed in CREATABLE_DIRS
'''

import os
import re
import sys
from pathlib import Path


# ================================================== #
# CONSOLE
# ================================================== #

# the Windows console often uses cp1252, which cannot print every author name
# (for example Möhler is fine, but Greek letters or arrows are not)
# UTF-8 with replacement keeps a print from ever stopping a run
def use_utf8_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


use_utf8_console()


# ================================================== #
# PROJECT PATHS
# ================================================== #

# --- project root
# found from this file, so no path has to be typed on any operating system
# the environment variable AIDA_ROOT overrides it
SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPTS_DIR.parent if SCRIPTS_DIR.name.lower() == "scripts" else SCRIPTS_DIR
AIDA_ROOT = Path(os.environ.get("AIDA_ROOT") or DEFAULT_ROOT).expanduser().resolve()

# older scripts ask for BASE_DIR, it is the same folder
BASE_DIR = AIDA_ROOT

DATA_DIR = AIDA_ROOT / "data"
INFO_DIR = AIDA_ROOT / "info"
OUTPUT_DIR = AIDA_ROOT / "output"

# --- publication pipeline (aida_publications / authors / enrich / plots)
PUBLICATIONS_DIR = INFO_DIR / "publications"
BIBS_DIR = PUBLICATIONS_DIR / "bibs"
AUTHORS_DIR = INFO_DIR / "aut"
FIGURES_DIR = INFO_DIR / "figures"
SEARCH_DIR = INFO_DIR / "search"

PUBLICATIONS_CSV = INFO_DIR / "publications.csv"
PUBLICATIONS_ENRICHED_CSV = INFO_DIR / "publications_enriched.csv"
AFFILIATIONS_CSV = INFO_DIR / "affiliations.csv"
AUTHORS_CSV = AUTHORS_DIR / "authors.csv"
SUMMARY_CSV = INFO_DIR / "summary.csv"
INSTITUTION_COORDS_CSV = INFO_DIR / "institution_coords.csv"
INSTITUTION_COORDS_FILE = INSTITUTION_COORDS_CSV

MPL_STYLE = SCRIPTS_DIR / "LoFaStyle.mplstyle"

# --- Web of Science branch, raw exports
WOS_DIR_RAW = DATA_DIR / "webofscience"
WOS_KEYWORDS_DIR = WOS_DIR_RAW / "keywords"        # topic / instrument exports
WOS_RESEARCHERS_DIR = WOS_DIR_RAW / "researchers"  # author exports

WOS_INPUT_DIRS = [WOS_KEYWORDS_DIR, WOS_RESEARCHERS_DIR]

# --- Web of Science branch, generated files
# the master .bib lives on its own, directly under the project root
# every other generated WoS file goes to processed_data/wos
MASTER_BIBS_DIR = AIDA_ROOT / "master_bibs"
PROCESSED_DATA_DIR = AIDA_ROOT / "processed_data"
WOS_DIR = PROCESSED_DATA_DIR / "wos"

# the surviving records, the end of the four-stage chain
WOS_MASTER_BIB = MASTER_BIBS_DIR / "wos_master.bib"

# --- the stages behind it, all written by aida_wos_merge.py
# stage 1, everything that was read
WOS_ALL_BIB = WOS_DIR / "wos_all.bib"
WOS_ALL_CSV = WOS_DIR / "wos_all.csv"

# stage 2, one copy per publication, and the duplicate audit trail
WOS_UNIQUE_BIB = WOS_DIR / "wos_unique.bib"
WOS_DUPLICATES_CSV = WOS_DIR / "wos_duplicates.csv"
WOS_DUPLICATES_REPORT = WOS_DIR / "wos_duplicates_report.txt"

# stages 3 and 4, what the keyword and the KIT test removed
WOS_NO_KEYWORDS_CSV = WOS_DIR / "wos_no_keywords.csv"
WOS_NO_KIT_CSV = WOS_DIR / "wos_no_kit.csv"

# the surviving records as a table, and the report over all four stages
WOS_MASTER_CSV = WOS_DIR / "wos_master.csv"
WOS_MERGE_SUMMARY = WOS_DIR / "wos_merge_summary.txt"

# --- OpenAlex branch (1_b_aida_openalex.py, 1_c_aida_openalex_merge.py)
OPENALEX_DATA_DIR = DATA_DIR / "openalex"
OPENALEX_KEYWORDS_DIR = OPENALEX_DATA_DIR / "keywords"
OPENALEX_RESEARCHERS_DIR = OPENALEX_DATA_DIR / "researchers"
OPENALEX_INPUT_DIRS = [OPENALEX_KEYWORDS_DIR, OPENALEX_RESEARCHERS_DIR]
OPENALEX_FETCH_LOG = OPENALEX_DATA_DIR / "fetch_log.csv"
OPENALEX_MERGE_DIR = PROCESSED_DATA_DIR / "openalex"

# one researcher name per line, read by 1_b_aida_openalex.py
RESEARCHERS_FILE = DATA_DIR / "AIDA Scientists.txt"

# --- IMK-AAF publication page (1_d_aida_imkaaf_bib.py)
IMKAAF_MASTER_DIR = MASTER_BIBS_DIR
IMKAAF_CACHE_DIR = DATA_DIR / "imkaaf"

# --- HTML pages and author review (3_a to 3_d)
PUBLICATION_LIST_DIR = OUTPUT_DIR / "publications"
PUBLICATION_LIST_FILE = PUBLICATION_LIST_DIR / "AIDA_Publication_List.html"
# one page per researcher, made from AIDA_master/authors, and an index of them
AUTHOR_PUBLICATION_DIR = PUBLICATION_LIST_DIR / "authors"
AUTHOR_PUBLICATION_INDEX_FILE = PUBLICATION_LIST_DIR / "AIDA_Authors_Index.html"
AUTHORS_HTML_DIR = OUTPUT_DIR / "authors"
AUTHORS_SCRIPT_FILE = SCRIPTS_DIR / "3_b_aida_bib_authors.py"

# --- Excel reports (3_b_aida_bib_excel.py): one workbook per researcher and one for all
EXCEL_DIR = OUTPUT_DIR / "excel"
EXCEL_AUTHORS_DIR = EXCEL_DIR / "authors"
EXCEL_MASTER_FILE = EXCEL_DIR / "AIDA_Master.xlsx"

# --- plots and maps as image files (3_b_aida_bib_figures.py): one folder per researcher, one for all
FIGURES_OUTPUT_DIR = OUTPUT_DIR / "figures"
FIGURES_AUTHORS_DIR = FIGURES_OUTPUT_DIR / "authors"
FIGURES_MASTER_DIR = FIGURES_OUTPUT_DIR / "AIDA_Master"
AUTHOR_REVIEW_DIR = AUTHORS_DIR / "review"
AUTHOR_CORRECTIONS_DIR = AUTHORS_DIR / "corrections"

# --- the two shared paper lists
# noAIDA.txt removes papers in every stage, yesAIDA.txt collects proposed additions
NOAIDA_FILE = DATA_DIR / "noAIDA.txt"
NO_AIDA_FILE = NOAIDA_FILE
YES_AIDA_FILE = DATA_DIR / "yesAIDA.txt"

# --- author self correction
# raw/ holds the files exactly as the authors sent them, never edited
# the extracted AIDA lists are written to the folder itself
AUTHOR_SELF_CORRECTION_DIR = DATA_DIR / "author_self_correction"
AUTHOR_SELF_CORRECTION_RAW_DIR = AUTHOR_SELF_CORRECTION_DIR / "raw"

# --- Ottmar Möhler's publication list (2_a_aida_ottmar_list.py)
# sent on 2026-10-13, the AIDA publications are highlighted in green
# the file name may be written with ö, oe or _ ; ottmar_file_pattern finds any of them
OTTMAR_LIST_FILE = AUTHOR_SELF_CORRECTION_RAW_DIR / "Ottmar_Möhler_publications.docx"
OTTMAR_AIDA_FILE = AUTHOR_SELF_CORRECTION_DIR / "Ottmar_Moehler_AIDA_publications.docx"
ottmar_file_pattern = "Ottmar*publications*.docx"

# the number of AIDA publications Ottmar reported, a different count gives a warning
ottmar_expected_aida = 142

# Word highlight colours that mark a publication as AIDA work
# the values are the Word names, "green" is the bright green of the toolbar
ottmar_aida_highlight_colors = ["green", "darkGreen"]

# --- DOI list of Ottmar's AIDA publications (2_b_aida_ottmar_check.py)
# one row per publication, can be checked and edited in Excel
OTTMAR_DOI_FILE = AUTHOR_SELF_CORRECTION_DIR / "Ottmar_Moehler_AIDA_dois.csv"
# answers from Crossref are kept, so a second run needs no internet
AUTHOR_SELF_CORRECTION_CACHE_DIR = AUTHOR_SELF_CORRECTION_DIR / "cache"

# a match is accepted when this share of the found title appears in the citation
doi_title_accept = 0.90
# a weaker match is kept with status "check", for a look by hand
doi_title_check = 0.80

# --- bib files built from the authors' own lists, one file per author (2_c_aida_ottmar_bib.py)
# the folder is created when it does not exist
AUTHOR_SELF_CORRECTION_BIB_DIR = AUTHOR_SELF_CORRECTION_DIR / "bib"
OTTMAR_BIB_FILE = AUTHOR_SELF_CORRECTION_BIB_DIR / "Ottmar_Moehler.bib"

# --- Robert Wagner's publication list (2_a_aida_robert_list.py)
# taken from the publication page we sent him, every entry with "Wagner, R." as author
ROBERT_PUBLICATION_PAGE = AIDA_ROOT / "AIDA_lists" / "publications" / "AIDA_Publication_List.html"
ROBERT_AIDA_FILE = AUTHOR_SELF_CORRECTION_DIR / "Robert_Wagner_AIDA_publications.docx"
# his name as it is written in the authors of the page; a regular expression
robert_author_pattern = r"\bWagner, R\."

# --- Robert Wagner's notes on that list (2_b_aida_robert_check.py)
# sent on 2026-10-14 as free text: missing papers, entries to remove, roles, questions
ROBERT_NOTES_FILE = AUTHOR_SELF_CORRECTION_RAW_DIR / "Robert_Wagner_publications.docx"
ROBERT_CORRECTIONS_FILE = AUTHOR_SELF_CORRECTION_DIR / "Robert_Wagner_corrections.csv"
robert_file_pattern = "Robert*Wagner*.docx"
# his corrected list, one row per paper with its DOI, as Ottmar's DOI list
ROBERT_DOI_FILE = AUTHOR_SELF_CORRECTION_DIR / "Robert_Wagner_AIDA_dois.csv"
# his bib file (2_c_aida_robert_bib.py)
ROBERT_BIB_FILE = AUTHOR_SELF_CORRECTION_BIB_DIR / "Robert_Wagner.bib"

# decisions taken on 2026-10-14 for the questions in his notes
# True keeps the proceedings he asked about (two in 2013, one in 2010)
robert_keep_proceedings = True
# True adds the missing papers he marked "uses APC chamber"
robert_keep_apc_papers = True


# ================================================== #
# WEB OF SCIENCE FILTER RULES
# ================================================== #

# --- test 1, keywords
# a record passes if at least keyword_min_distinct *different* terms appear
# anywhere in keyword_search_fields
# repeating one term does not help
keyword_terms = ["AIDA", "ice", "nucleation", "chamber", "aerosol"]
keyword_min_distinct = 2
keyword_search_fields = ["Title", "Abstract", "Keywords", "Keywords-Plus"]

# True  -> "aerosol" does NOT match "aerosolised", "ice" does NOT match "price"
# False -> plain substring matching, much looser and not recommended
keyword_whole_word = True

# extra forms counted as the same term, so a plural or a derived form is not
# lost to the whole-word test
# set to {} for a strictly literal reading
keyword_variants = {
    "ice": ["icy"],
    "nucleation": ["nucleating", "nucleate", "nucleated", "nucleus", "nuclei"],
    "chamber": ["chambers"],
    "aerosol": ["aerosols"],
}

# --- test 2, affiliation
# a record passes if ANY pattern matches the affiliation fields, i.e. at
# least one author sits at KIT
# patterns are regular expressions, matched case-insensitively
require_kit_affiliation = True
affiliation_search_fields = ["Affiliation", "Affiliations"]

# note the r"" prefix: \b is a regex word boundary
kit_affiliation_patterns = [
    r"Karlsruhe Institute of Technology",
    r"Karlsruhe Inst\.? Technol",              # WoS abbreviates like this
    r"Forschungszentrum Karlsruhe",
    r"Kernforschungszentrum Karlsruhe",
    r"Research Cent(?:er|re) Karlsruhe",
    r"IMK[ .-]?AAF",
    r"Inst(?:itut|\.)? f(?:ü|u)r Meteorologie und Klimaforschung",
    r"\bKIT\b",
    r"\bFZK\b",
]

# True also accepts any affiliation merely containing "Karlsruhe"
# off by default: it would match Karlsruhe University of Applied Sciences,
# the Hochschule, and KIT spin-offs that are not IMK-AAF
loose_karlsruhe = False

# --- rejection CSV layout
wos_csv_fields = ["Year", "Author", "Title", "Journal", "DOI", "Type",
                  "Times-Cited", "Affiliation"]
wos_csv_max_field_chars = 2000


# ================================================== #
# MEASUREMENT ARCHIVE
# ================================================== #

# MySQL server on the KIT network
# reachable only on the KIT network or through the VPN
# the user and password are read from the environment, never committed here
#     Linux / macOS   export AIDA_DB_USER='...'      export AIDA_DB_PASSWORD='...'
#     Windows cmd     set AIDA_DB_USER=...           set AIDA_DB_PASSWORD=...
#     PowerShell      $env:AIDA_DB_USER = "..."      $env:AIDA_DB_PASSWORD = "..."
db_host = "141.52.172.3"
db_port = 3306
db_name = "aida"
db_user = os.environ.get("AIDA_DB_USER", "")
db_password = os.environ.get("AIDA_DB_PASSWORD", "")

# tables follow {campaign}_aida_{instrument}
db_table_pattern = re.compile(r"^(?P<campaign>.+?)_aida_(?P<instrument>.+)$")


def db_url() -> str:
    '''SQLAlchemy URL for the measurement archive.'''
    if not (db_user and db_password):
        raise RuntimeError(
            "Set AIDA_DB_USER and AIDA_DB_PASSWORD in the environment "
            "before connecting to the measurement archive.")
    return (f"mysql+pymysql://{db_user}:{db_password}"
            f"@{db_host}:{db_port}/{db_name}?charset=utf8mb4")


# ================================================== #
# MASTER BIBLIOGRAPHY (aida_master_bib.py)
# ================================================== #

# --- input folders
AIDA_DATA_DIR = DATA_DIR
AIDA_CAMPAIGNS_DIR = AIDA_DATA_DIR / "AIDA_campaigns"
OPENALEX_BIB_DIR = OPENALEX_DATA_DIR
WOS_BIB_DIR = WOS_DIR_RAW

# Every .bib file below these folders (subfolders included) is merged
BIB_SOURCE_DIRS = [OPENALEX_BIB_DIR, WOS_BIB_DIR]

# Both files must have a column named campaign_column
CAMPAIGN_FILES = [
    AIDA_CAMPAIGNS_DIR / "campaign_instrument_matrix.csv",
    AIDA_CAMPAIGNS_DIR / "campaigns.csv",
]

# --- output folder and files (the folder is created if missing)
# three folders: the master itself, one master per author, and everything else
MASTER_BIB_DIR = AIDA_ROOT / "AIDA_master"
MASTER_BIB_MAIN_DIR = MASTER_BIB_DIR / "master"
MASTER_BIB_AUTHORS_DIR = MASTER_BIB_DIR / "authors"
MASTER_BIB_SUPPLEMENTARY_DIR = MASTER_BIB_DIR / "supplementary"

MASTER_BIB_FILE = MASTER_BIB_MAIN_DIR / "AIDA_Master_bib.bib"
MASTER_BIB_FIRST_STAGE_FILE = MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_first_satge.bib"
MASTER_BIB_NODUP_FILE = MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_NoDuplicate.bib"
MASTER_BIB_DEDUP_REPORT_FILE = MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_dedup_report.txt"
MASTER_BIB_FILTER_SUMMARY_FILE = MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_filter_summary.txt"
MASTER_BIB_FILTER_LOG_FILE = MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_filter_log.csv"
MASTER_BIB_REMOVED_FILE = MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_removed.bib"
MASTER_BIB_COMBINED_SUMMARY_FILE = MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_combined_summary.txt"
MASTER_BIB_AUTHOR_LOG_FILE = MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_author_decisions.csv"
MASTER_BIB_AUTHORS_SUMMARY_FILE = MASTER_BIB_SUPPLEMENTARY_DIR / "AIDA_Master_bib_authors_summary.txt"

# --- filter selection, set to 1, 2, 3, 4, 5 or 6
# terms are searched in title/abstract/keywords
# 1: AIDA term
# 2: campaign name
# 3: AIDA term AND campaign name
# 4: AIDA term AND KIT affiliation
# 5: campaign name AND KIT affiliation
# 6: AIDA term AND campaign name AND KIT affiliation
master_bib_filter = [1, 4, 5]

# --- AIDA terms
# Whole word matching: "AIDA" matches "AIDA chamber" and "AIDA-3", not "AIDAN"
# Exact case list: "AIDA" will not match the first name "Aida"
aida_keywords_exact_case = ["AIDA"]
aida_keywords = [
    "Aerosol Interaction and Dynamics in the Atmosphere",
]

# --- campaign names (filter B only)
campaign_column = "campaign"
# Exact case avoids false hits such as IMPACTS or MOSES matching ordinary words
campaign_exact_case = True
# Names shorter than this are ignored
campaign_min_length = 3
# Names to ignore by hand (compared without case), e.g. ["SOAS", "PASCAL"]
campaign_exclude = []

# --- KIT affiliation
# Any case list; accents and punctuation are ignored, so "fur" also matches "für"
kit_affiliation = [
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
]
# Exact case list for acronyms
kit_affiliation_exact_case = ["KIT", "FZK"]
# False turns the affiliation check off
# own name, so it does not collide with require_kit_affiliation of the WoS filter
master_bib_require_kit_affiliation = True
# True keeps entries that have no affiliation field at all (if the term check passes)
keep_if_affiliation_missing = False
# Also write the removed entries to MASTER_BIB_REMOVED_FILE for manual checking
save_removed_entries = True

# --- the authors' own confirmed lists (step 2), read by 3_a_aida_master_bib.py
# a confirmed list decides for the papers its author is on, the filters follow it:
#   a paper in the list           is always kept, the filters are skipped
#   a paper the author removed    is always removed ("removed" table)
#   a paper with the author on it, missing from a "complete" list, is removed
# authors without a confirmed list are not affected, the filters decide as before
# surname and initial find the author in the bib entries; ö, oe and o count as one
# scope "articles": the list judges only articles, as Ottmar's list holds only
#                   peer reviewed papers; his other entries follow the filters
# scope "all"     : the list judges every entry with the author
# complete True   : the author marked the whole record, an unlisted paper is rejected
# complete False  : the author reviewed the list we sent; new papers follow the filters
author_confirmed_lists = [
    {"name": "Ottmar Möhler", "surname": "Möhler", "initial": "O",
     "bib": OTTMAR_BIB_FILE, "removed": None, "scope": "articles", "complete": True},
    {"name": "Robert Wagner", "surname": "Wagner", "initial": "R",
     "bib": ROBERT_BIB_FILE, "removed": ROBERT_DOI_FILE, "scope": "all", "complete": False},
]

# --- BibTeX field names searched (any case; the first existing ones are joined)
bib_title_fields = ["title"]
bib_abstract_fields = ["abstract"]
bib_keyword_fields = ["keywords", "author-keywords", "keywords-plus"]
bib_affiliation_fields = ["affiliation", "affiliations"]

# --- deduplication
# Same normalised title counts as duplicate if the years differ by at most this
# (online first versus print year) and the DOIs do not conflict
dedup_year_tolerance = 1
# Titles shorter than this (letters and digits only) are not used for matching
dedup_min_title_length = 20


# ================================================== #
# EXTERNAL APIS
# ================================================== #

# a contact address puts the requests in the polite pool: faster, higher limits
openalex_mailto = "loghman.fathollahi@kit.edu"
crossref_mailto = openalex_mailto
CONTACT_EMAIL = openalex_mailto
OPENALEX_MAILTO = openalex_mailto

# KIT in the Research Organization Registry, used to pick KIT authors
KIT_ROR = "04t3en479"

# keys are read from the environment, never committed here
# OpenAlex needs a free key since February 2026 (openalex.org/settings/api)
OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY", "")
# Clarivate Starter API, KIT institutional subscription
wos_api_key = os.environ.get("WOS_API_KEY", "")

request_timeout = 30    # seconds
request_pause = 0.2     # seconds between API calls


# ================================================== #
# HELPERS
# ================================================== #

# --- directories the pipeline may create on demand
CREATABLE_DIRS = [
    DATA_DIR, INFO_DIR, OUTPUT_DIR, PUBLICATIONS_DIR, BIBS_DIR, AUTHORS_DIR,
    FIGURES_DIR, SEARCH_DIR, MASTER_BIBS_DIR, PROCESSED_DATA_DIR, WOS_DIR,
    OPENALEX_DATA_DIR, OPENALEX_MERGE_DIR, MASTER_BIB_DIR,
    MASTER_BIB_MAIN_DIR, MASTER_BIB_AUTHORS_DIR, MASTER_BIB_SUPPLEMENTARY_DIR,
]


def ensure_dirs(*dirs: Path) -> None:
    '''Create the given directories, or all CREATABLE_DIRS, if missing.'''
    for directory in (dirs or CREATABLE_DIRS):
        Path(directory).mkdir(parents=True, exist_ok=True)


def _main() -> None:
    '''python config.py, show what everything resolves to here.'''

    # --- paths
    # only uppercase Path objects are project paths, see the style rules
    print(f"system     {sys.platform}, Python {sys.version.split()[0]}")
    print(f"AIDA_ROOT  {AIDA_ROOT}"
          f"{'  (from the AIDA_ROOT variable)' if os.environ.get('AIDA_ROOT') else ''}\n")
    print("paths")
    for name, value in sorted(globals().items()):
        if isinstance(value, Path) and name.isupper():
            mark = "ok     " if value.exists() else "MISSING"
            print(f"  {mark} {name:<34} {value}")

    # --- WoS filter rules
    print("\nWoS filter rules")
    print(f"  terms          {keyword_terms}")
    print(f"  min distinct   {keyword_min_distinct}")
    print(f"  whole word     {keyword_whole_word}")
    print(f"  search fields  {keyword_search_fields}")
    print(f"  KIT required   {require_kit_affiliation} "
          f"({len(kit_affiliation_patterns)} patterns, "
          f"loose={loose_karlsruhe})")

    # --- database and API credentials
    print("\ndatabase")
    print(f"  {db_user or '<AIDA_DB_USER unset>'}@{db_host}:{db_port}/{db_name}"
          f"  password {'set' if db_password else 'NOT set'}")
    print(f"  OPENALEX_API_KEY {'set' if OPENALEX_API_KEY else 'NOT set'}")
    print(f"  WOS_API_KEY      {'set' if wos_api_key else 'NOT set'}")


if __name__ == "__main__":
    _main()
