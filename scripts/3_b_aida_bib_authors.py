'''
3_b_aida_bib_authors.py

   Builds interactive HTML pages about the authors and the affiliations of
   the AIDA publications, read from the same master BibTeX file that
   3_a_aida_bib_html.py uses for the publication list.

Purpose

   Read every entry of AIDA_Master_bib.bib and find, for each paper, the
   authors in order, the corresponding authors and the affiliation of each
   author.
   Web of Science entries carry per author addresses and the
   "(Corresponding Author)" marker in their affiliation field, these are
   parsed directly from the bib file.
   OpenAlex is asked for the rest (authorships, is_corresponding flags,
   institutions with ROR ids and coordinates). Every answer is cached, so
   later runs work offline and only new papers are requested.
   For every author count: papers, first author, first and corresponding,
   corresponding but not first, co-author only.
   Count the same for every affiliation and every country, and rank
   authors by affiliation, by journal and by publication type.

   Roles of an author on one paper (mutually exclusive, they sum to papers)
      First + corresponding    first author and corresponding author
      First author only        first author, not corresponding
      Corresponding, not first corresponding author, not first author
      Co-author                neither first nor corresponding

   Authors are identified by surname plus first initial, umlauts and
   accents folded (Möhler, Moehler and Mohler are one person).
   Wrong merges or splits can be fixed in author_aliases.csv.

Usage

   python 3_b_aida_bib_authors.py
   python 3_b_aida_bib_authors.py --no-openalex        no OpenAlex (no API key needed), ROR still places the map
   python 3_b_aida_bib_authors.py --no-geocode         no ROR look ups for the map
   python 3_b_aida_bib_authors.py --no-map-download    map outlines and library from the cache only
   python 3_b_aida_bib_authors.py --refresh-cache      ask the APIs again for cached misses
   python 3_b_aida_bib_authors.py --input some_folder/AIDA_Master_bib.bib
   python 3_b_aida_bib_authors.py --output-dir some_folder/authors
   python 3_b_aida_bib_authors.py --dry-run --verbose  cached data only, write nothing

   Without --input and --output-dir the paths come from config.py and are
   relative to the project root, the same on Linux and Windows.
   The OpenAlex key is read from the environment variable OPENALEX_API_KEY
      Linux / macOS   export OPENALEX_API_KEY=your_key
      Windows cmd     set OPENALEX_API_KEY=your_key
      PowerShell      $env:OPENALEX_API_KEY = "your_key"

Output

   AIDA_Authors.html                interactive authors ranking and author profiles
   AIDA_Affiliations.html           interactive affiliations ranking and profiles
   AIDA_Affiliations_Map.html       world map of all affiliations, no API key, works offline
   AIDA_authors_ranking.csv         one row per author with all role counts
   AIDA_author_affiliation.csv      one row per author and affiliation
   AIDA_author_journal.csv          one row per author and journal
   AIDA_author_type.csv             one row per author, one column per publication type
   AIDA_affiliations_ranking.csv    one row per affiliation with coordinates
   AIDA_countries_ranking.csv       one row per country
   AIDA_paper_authors.csv           one row per paper and author (role, affiliations)
   cache/openalex_works.json        cached OpenAlex authorships
   cache/openalex_institutions.json cached OpenAlex institutions with coordinates
   cache/ror_affiliations.json      cached ROR matches for Web of Science addresses
   cache/world_countries_50m.json   country outlines for the map (Natural Earth, public domain)
   cache/leaflet.js, leaflet.css    map library, stored inside the map page

   Optional input files in the output folder (created by hand)
   author_aliases.csv               variant,canonical   e.g. "Mohler, O.","Möhler, Ottmar"
   corresponding_overrides.csv      doi_or_key,author,corresponding   role corrections from the authors
   affiliation_aliases.csv          pattern,canonical   regex on "organisation, address"
   affiliation_coords_manual.csv    name,lat,lon,country,city   fixes map positions
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import csv
import datetime
import html
import io
import json
import os
import re
import socket
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

# --- project config with standalone fallback
# config.py is used when it exists, otherwise the defaults below apply
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
# the input follows MASTER_BIB_FILE in config.py, like 3_a_aida_bib_html.py
BASE_DIR = Path(getattr(config, "BASE_DIR", FALLBACK_ROOT))
MASTER_DIR = Path(getattr(config, "MASTER_BIB_DIR", BASE_DIR / "AIDA_master"))
INPUT_BIB_PATH = Path(getattr(config, "MASTER_BIB_FILE", MASTER_DIR / "AIDA_Master_bib.bib"))
OUTPUT_DIR = Path(getattr(config, "AUTHORS_HTML_DIR", BASE_DIR / "output" / "authors"))
PUBLICATION_LIST_PATH = Path(getattr(config, "PUBLICATION_LIST_FILE",
                                     BASE_DIR / "output" / "publications" / "AIDA_Publication_List.html"))

# --- coordinate cache written by aida_maps.py, read when it exists
INSTITUTION_COORDS_PATH = Path(getattr(config, "INSTITUTION_COORDS_FILE",
                                       BASE_DIR / "info" / "institution_coords.csv"))

# --- output file names inside the output folder
authors_html_name = "AIDA_Authors.html"
affiliations_html_name = "AIDA_Affiliations.html"
map_html_name = "AIDA_Affiliations_Map.html"

# --- API settings
# OpenAlex needs a free API key since February 2026 (openalex.org/settings/api)
# set it with: export OPENALEX_API_KEY=your_key, or OPENALEX_API_KEY in config.py
# without a key use --no-openalex; the map page itself never needs a key
openalex_url = "https://api.openalex.org"
ror_url = "https://api.ror.org/v2/organizations"
openalex_mailto = os.environ.get("OPENALEX_MAILTO", getattr(config, "OPENALEX_MAILTO",
                                 getattr(config, "openalex_mailto", "")))
openalex_api_key = os.environ.get("OPENALEX_API_KEY", getattr(config, "OPENALEX_API_KEY", ""))
openalex_batch_size = 50
request_timeout = 30
request_pause = 0.12
max_network_failures = 3

# --- map page assets, downloaded once into the cache and stored inside the page
# no map tile service is used, so the map needs no API key (CARTO now asks for one)
world_map_url = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json"
leaflet_js_url = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"
leaflet_css_url = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css"

# --- author identity
# umlaut folding makes Moehler, Möhler and Mohler the same surname
umlaut_folding = True

# --- chart and table settings used in the pages
top_n_default = 15

# --- BibTeX entry type to label, same table as 3_a_aida_bib_html.py
type_labels = {
    "article": "Article",
    "inproceedings": "Conference paper",
    "conference": "Conference paper",
    "proceedings": "Proceedings",
    "incollection": "Book chapter",
    "inbook": "Book chapter",
    "book": "Book",
    "phdthesis": "PhD thesis",
    "mastersthesis": "Master thesis",
    "thesis": "Thesis",
    "techreport": "Report",
    "report": "Report",
    "dataset": "Dataset",
    "data": "Dataset",
    "misc": "Other",
    "unpublished": "Other",
    "online": "Other",
}

# --- entry types that carry no publication
skip_entry_types = {"comment", "preamble", "string"}

# --- affiliation aliases, regex on "organisation, rest of address" (lower case)
# the first matching pattern gives the canonical name
# more can be added in affiliation_aliases.csv without editing this file
affiliation_aliases_default = [
    (r"^(karlsruhe inst(itute)?( of)? technol(ogy)?|kit)\b", "Karlsruhe Institute of Technology"),
    (r"^swiss fed(eral)? inst(itute)?( of)? technol(ogy)?\b.*\bzurich\b", "ETH Zurich"),
    (r"^eth zurich\b", "ETH Zurich"),
    (r"^swiss fed(eral)? inst(itute)?( of)? technol(ogy)?\b.*\blausanne\b", "EPFL"),
    # remove the # below to count Forschungszentrum Karlsruhe (before 2009) as KIT
    # (r"^forschungszentrum karlsruhe\b", "Karlsruhe Institute of Technology"),
]

# --- Web of Science abbreviations expanded before affiliation names are compared
# "Univ Leeds" and "University of Leeds" both become "university leeds"
abbreviation_words = {
    "univ": "university", "universitat": "university", "universite": "university",
    "universidad": "university", "universita": "university", "universiteit": "university",
    "inst": "institute", "institut": "institute", "instituto": "institute",
    "technol": "technology", "res": "research", "natl": "national",
    "ctr": "center", "centre": "center", "lab": "laboratory", "labs": "laboratories",
    "sci": "science", "sch": "school", "dept": "department", "atmospher": "atmospheric",
    "tropospher": "tropospheric", "meteorol": "meteorology", "environm": "environmental",
    "phys": "physics", "chem": "chemistry", "geosci": "geosciences", "engn": "engineering",
    "int": "international", "acad": "academy", "minist": "ministry", "observ": "observatory",
    "fed": "federal", "coll": "college", "fac": "faculty", "oceanog": "oceanography",
    "agcy": "agency", "assoc": "association", "dev": "development", "appl": "applied",
    "biol": "biology", "clim": "climate", "calif": "california", "polytech": "polytechnic",
    "adm": "administration", "grp": "group", "div": "division", "serv": "service",
    "syst": "systems", "cent": "central", "amer": "american", "geophys": "geophysical",
}

# --- words ignored when affiliation names are compared
affiliation_stop_words = {"of", "the", "and", "for", "fur", "de", "du", "des", "la",
                          "gmbh", "ev", "e", "v", "in", "at", "a"}

# --- words that show a text is an institution and not a person
institution_words = {
    "univ", "university", "inst", "institute", "institut", "dept", "department", "ctr",
    "center", "centre", "lab", "laboratory", "sch", "school", "coll", "college", "corp",
    "gmbh", "inc", "ltd", "agcy", "agency", "minist", "ministry", "observ", "observatory",
    "fac", "faculty", "acad", "academy", "natl", "national", "fdn", "foundation", "assoc",
    "association", "helmholtz", "forschungszentrum", "company", "technol", "technology",
    "res", "research", "hosp", "hospital", "grp", "group", "div", "division", "serv",
    "service", "museum", "council", "office", "program", "programme",
}

# --- lower case particles allowed inside a surname
name_particles = {"von", "van", "de", "der", "den", "da", "di", "del", "della", "la", "le",
                  "du", "dos", "das", "ter", "ten", "zu", "af", "al", "el", "bin", "y"}

# --- country names from Web of Science addresses mapped to the OpenAlex form
country_aliases = {
    "usa": "United States", "u s a": "United States", "united states of america": "United States",
    "peoples r china": "China", "pr china": "China", "p r china": "China",
    "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom",
    "north ireland": "United Kingdom", "northern ireland": "United Kingdom", "uk": "United Kingdom",
    "u arab emirates": "United Arab Emirates", "rep of korea": "South Korea",
    "korea": "South Korea", "republic of korea": "South Korea", "korea, republic of": "South Korea",
    "russian federation": "Russia", "czech republic": "Czechia", "deutschland": "Germany",
    "the netherlands": "Netherlands", "turkiye": "Turkey", "viet nam": "Vietnam",
}

# --- corresponding author markers used by Web of Science
corresponding_marker_re = re.compile(r"\(\s*(?:corresponding|reprint)\s+author\s*\)", re.I)


# ================================================== #
# BIBTEX PARSER
# ================================================== #

# --- same small dependency free parser as 3_a_aida_bib_html.py
# handles braced values, quoted values, bare numbers, @string macros
# and the # concatenation operator

def read_braced(text, start):
    # text[start] must be "{" and the matching closing brace is searched
    # returns the inner text and the index after the closing brace
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
        index += 1
    raise ValueError("unbalanced braces starting at character %d" % start)


def read_quoted(text, start):
    # text[start] must be '"', braces inside the quotes may contain quotes
    depth = 0
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == '"' and depth == 0:
            return text[start + 1:index], index + 1
        index += 1
    raise ValueError("unterminated quoted value starting at character %d" % start)


def skip_space(text, index):
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def read_value(text, index, string_macros):
    # reads one field value, including "a" # "b" concatenation
    parts = []
    while True:
        index = skip_space(text, index)
        if index >= len(text):
            break
        char = text[index]
        if char == "{":
            part, index = read_braced(text, index)
        elif char == '"':
            part, index = read_quoted(text, index)
        else:
            match = re.match(r"[^\s,#}\)]+", text[index:])
            if not match:
                break
            token = match.group(0)
            index += len(token)
            part = string_macros.get(token.lower(), token)
        parts.append(part)
        index = skip_space(text, index)
        if index < len(text) and text[index] == "#":
            index += 1
            continue
        break
    return "".join(parts), index


def parse_bibtex(text, verbose=False):
    # returns a list of dicts with keys: entry_type, key, fields
    entries = []
    string_macros = {}
    problem_count = 0
    index = 0
    while True:
        at_pos = text.find("@", index)
        if at_pos < 0:
            break
        match = re.match(r"@\s*([A-Za-z]+)\s*([{(])", text[at_pos:])
        if not match:
            index = at_pos + 1
            continue
        entry_type = match.group(1).lower()
        body_start = at_pos + match.end()
        close_char = "}" if match.group(2) == "{" else ")"

        # --- skip non publication blocks, but remember @string macros
        if entry_type in skip_entry_types:
            try:
                if match.group(2) == "{":
                    block, index = read_braced(text, body_start - 1)
                else:
                    end_pos = text.find(")", body_start)
                    block, index = text[body_start:end_pos], end_pos + 1
            except ValueError:
                index = body_start
                continue
            if entry_type == "string":
                macro = re.match(r"\s*([A-Za-z][\w-]*)\s*=\s*(.*)", block, re.S)
                if macro:
                    value, _ = read_value(macro.group(2), 0, string_macros)
                    string_macros[macro.group(1).lower()] = value
            continue

        # --- entry key
        key_end = text.find(",", body_start)
        if key_end < 0:
            break
        entry_key = text[body_start:key_end].strip()
        index = key_end + 1
        fields = {}

        # --- fields until the closing brace of the entry
        try:
            while True:
                index = skip_space(text, index)
                if index >= len(text):
                    break
                if text[index] == close_char:
                    index += 1
                    break
                if text[index] == ",":
                    index += 1
                    continue
                name_match = re.match(r"([A-Za-z][\w\-:.]*)\s*=", text[index:])
                if not name_match:
                    # malformed field, jump to the next line
                    next_line = text.find("\n", index)
                    index = len(text) if next_line < 0 else next_line + 1
                    if text.find("@", index) == index:
                        break
                    continue
                field_name = name_match.group(1).lower()
                index += name_match.end()
                value, index = read_value(text, index, string_macros)
                fields[field_name] = value
        except ValueError as error:
            problem_count += 1
            if verbose:
                print("  parse problem in %s: %s" % (entry_key, error))
            index = body_start

        entries.append({"entry_type": entry_type, "key": entry_key, "fields": fields})

    if verbose and problem_count:
        print("  entries with parse problems: %d" % problem_count)
    return entries


# ================================================== #
# LATEX CLEANING
# ================================================== #

# --- same cleaning as 3_a_aida_bib_html.py
# private use characters mark sub and sup tags until escaping is done
SUB_OPEN, SUB_CLOSE, SUP_OPEN, SUP_CLOSE = "\ue000", "\ue001", "\ue002", "\ue003"

accent_marks = {
    '"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302",
    "~": "\u0303", "=": "\u0304", ".": "\u0307", "c": "\u0327",
    "v": "\u030C", "u": "\u0306", "H": "\u030B", "k": "\u0328",
    "r": "\u030A", "d": "\u0323", "b": "\u0331",
}

letter_commands = {
    "ss": "ß", "o": "ø", "O": "Ø", "ae": "æ", "AE": "Æ", "oe": "œ",
    "OE": "Œ", "aa": "å", "AA": "Å", "l": "ł", "L": "Ł", "i": "ı",
    "j": "ȷ", "dh": "ð", "DH": "Ð", "th": "þ", "TH": "Þ",
}

symbol_commands = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "degree": "°", "textdegree": "°", "circ": "°", "pm": "±", "times": "×",
    "approx": "≈", "sim": "~", "leq": "≤", "geq": "≥", "le": "≤", "ge": "≥",
    "textregistered": "®", "copyright": "©", "texttrademark": "™",
    "textendash": "-", "textemdash": "-", "textasciitilde": "~",
    "ldots": "...", "dots": "...", "textperiodcentered": "·", "cdot": "·",
    "textmu": "μ", "micro": "μ", "permil": "‰",
    "textless": "<", "textgreater": ">", "rightarrow": "→", "to": "→",
    "textbackslash": "\\",
}

keep_argument_commands = {
    "textit", "textbf", "emph", "textrm", "textsf", "texttt", "textsc",
    "textup", "textmd", "mathrm", "mathit", "mathbf", "mathsf", "text",
    "mbox", "hbox", "textnormal", "ce", "mathregular", "url", "nolinkurl",
    "uppercase", "MakeUppercase", "enquote",
}


def replace_accents(text):
    # {\"o}  \"{o}  \"o  \c{c}  \v c  forms
    def accent_repl(match):
        mark = accent_marks.get(match.group(1))
        letter = match.group(2) or match.group(3) or match.group(4) or ""
        if letter in ("\\i", "i") and mark:
            letter = "i"
        if not mark:
            return letter
        return unicodedata.normalize("NFC", letter + mark)

    pattern = (r"\{?\\([\"'`\^~=.]|[cvuHkrdb](?=[\s{\\]))\s*"
               r"(?:\{\s*(\\?[A-Za-z])\s*\}|(\\i)|([A-Za-z]))\}?")
    return re.sub(pattern, accent_repl, text)


def replace_math(text):
    # $...$ segments: sub and sup to markers, commands to symbols
    def math_repl(match):
        body = match.group(1)
        body = re.sub(r"\\(?:mathrm|text|mathit|mathbf|rm)\s*\{([^{}]*)\}", r"\1", body)
        body = re.sub(r"_\{([^{}]*)\}", SUB_OPEN + r"\1" + SUB_CLOSE, body)
        body = re.sub(r"_(\\?[A-Za-z0-9])", SUB_OPEN + r"\1" + SUB_CLOSE, body)
        body = re.sub(r"\^\{([^{}]*)\}", SUP_OPEN + r"\1" + SUP_CLOSE, body)
        body = re.sub(r"\^(\\?[A-Za-z0-9+\-])", SUP_OPEN + r"\1" + SUP_CLOSE, body)
        return body

    return re.sub(r"(?<!\\)\$([^$]*)(?<!\\)\$", math_repl, text)


def replace_commands(text):
    # \textsubscript{x} and \textsuperscript{x}
    text = re.sub(r"\\textsubscript\s*\{([^{}]*)\}", SUB_OPEN + r"\1" + SUB_CLOSE, text)
    text = re.sub(r"\\textsuperscript\s*\{([^{}]*)\}", SUP_OPEN + r"\1" + SUP_CLOSE, text)

    # formatting commands, repeated so nested commands are resolved
    keep_pattern = r"\\(?:%s)\s*\{([^{}]*)\}" % "|".join(sorted(keep_argument_commands, key=len, reverse=True))
    for _ in range(4):
        new_text = re.sub(keep_pattern, r"\1", text)
        if new_text == text:
            break
        text = new_text

    # symbol and letter commands
    def word_repl(match):
        name = match.group(1)
        if name in symbol_commands:
            return symbol_commands[name]
        if name in letter_commands:
            return letter_commands[name]
        return ""

    text = re.sub(r"\\([A-Za-z]+)\b\s?(?:\{\})?", word_repl, text)

    # escaped special characters
    text = re.sub(r"\\([&%$#_{}])", r"\1", text)
    text = text.replace("\\,", " ").replace("\\ ", " ").replace("\\;", " ").replace("~", " ")
    return text


def replace_html_tags(text):
    # some sources store <sub>2</sub> or <i>x</i> inside the bib fields
    text = re.sub(r"<\s*sub\s*>", SUB_OPEN, text, flags=re.I)
    text = re.sub(r"<\s*/\s*sub\s*>", SUB_CLOSE, text, flags=re.I)
    text = re.sub(r"<\s*sup\s*>", SUP_OPEN, text, flags=re.I)
    text = re.sub(r"<\s*/\s*sup\s*>", SUP_CLOSE, text, flags=re.I)
    text = re.sub(r"</?\s*(?:i|b|em|strong|scp|span|mml:[a-z]+)(?:\s[^>]*)?>", "", text, flags=re.I)
    return text


def clean_latex(text):
    # returns the cleaned text that still contains sub and sup markers
    if not text:
        return ""
    text = html.unescape(text)
    text = replace_html_tags(text)
    text = replace_accents(text)
    text = replace_math(text)
    text = replace_commands(text)

    # --- dashes and quotes become plain ASCII characters
    text = text.replace("---", "-").replace("--", "-")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("``", '"').replace("''", '"')

    # --- a degree sign written as a superscript is shown as a normal degree sign
    text = text.replace(SUP_OPEN + "°" + SUP_CLOSE, "°")
    text = text.replace(SUP_OPEN + " °" + SUP_CLOSE, "°")

    # --- remove leftover grouping braces and collapse whitespace
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\s+", " ", text).strip()
    return unicodedata.normalize("NFC", text)


def to_plain(text):
    # markers removed, used for sorting and searching
    for marker in (SUB_OPEN, SUB_CLOSE, SUP_OPEN, SUP_CLOSE):
        text = text.replace(marker, "")
    return text


def to_html(text):
    # escape first, then turn the markers into real tags
    escaped = html.escape(text, quote=True)
    escaped = escaped.replace(SUB_OPEN, "<sub>").replace(SUB_CLOSE, "</sub>")
    escaped = escaped.replace(SUP_OPEN, "<sup>").replace(SUP_CLOSE, "</sup>")
    return escaped


def plain(text):
    # cleaned text without markers, for names and places
    return to_plain(clean_latex(text))


# ================================================== #
# TEXT KEYS
# ================================================== #

def fold_text(text):
    # accents removed, lower case, special letters written in ASCII
    text = text.replace("ß", "ss").replace("ø", "o").replace("Ø", "O").replace("æ", "ae")
    text = text.replace("Æ", "AE").replace("œ", "oe").replace("ł", "l").replace("Ł", "L")
    text = text.replace("ı", "i").replace("đ", "d").replace("ð", "d").replace("þ", "th")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.lower()


def surname_key(surname):
    # Möhler, Moehler and Mohler all become "mohler"
    key = fold_text(surname)
    if umlaut_folding:
        key = key.replace("ae", "a").replace("oe", "o").replace("ue", "u")
    return re.sub(r"[^a-z]", "", key)


def journal_key(name):
    # case insensitive canonical form, "The" and "&" ignored
    key = fold_text(name).replace("&", " and ")
    key = re.sub(r"^the\s+", "", key)
    return re.sub(r"[^a-z0-9]+", " ", key).strip()


def affiliation_tokens(name):
    # tokens with Web of Science abbreviations expanded
    text = fold_text(name).replace("&", " and ")
    tokens = []
    for token in re.findall(r"[a-z0-9]+", text):
        token = abbreviation_words.get(token, token)
        if token not in affiliation_stop_words:
            tokens.append(token)
    return tokens


def affiliation_key(name):
    return " ".join(affiliation_tokens(name))


def is_abbreviated(name):
    # true when the name still contains Web of Science abbreviations
    words = re.findall(r"[a-z]+", fold_text(name))
    return any(word in abbreviation_words and abbreviation_words[word] != word for word in words)


def normalize_country(text):
    text = re.sub(r"\s+", " ", text.strip().rstrip(".").strip())
    if not text:
        return ""
    if re.search(r"\bUSA$", text):
        return "United States"
    # postcodes before the country name, e.g. "NL-3584 CC Netherlands", are dropped
    text = re.sub(r"^[A-Z]{0,3}-?\d[\w\- ]*\d\s+", "", text)
    alias = country_aliases.get(fold_text(text))
    return alias if alias else text


def clean_city(text):
    # "D-76021 Karlsruhe" becomes "Karlsruhe", "CO 80305" is dropped
    text = re.sub(r"\b[A-Z]{0,3}-?\d[\d\- ]*\b", " ", text)
    text = re.sub(r"\b[A-Z]{2}\b\s*$", " ", text)
    return re.sub(r"\s+", " ", text).strip(" ,.")


def short_openalex_id(value):
    # "https://openalex.org/I123" becomes "I123"
    return (value or "").rstrip("/").rsplit("/", 1)[-1]


# ================================================== #
# AUTHOR NAMES
# ================================================== #

def split_top_level(text, separator):
    # splits on a word separator only outside braces
    parts = []
    depth = 0
    current = []
    tokens = re.split(r"(\{|\}|\s+%s\s+)" % re.escape(separator), text)
    for token in tokens:
        if token is None or token == "":
            continue
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
        if re.fullmatch(r"\s+%s\s+" % re.escape(separator), token) and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(token)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def split_name(raw_name):
    # returns (last_name, given_names) from "Last, First" or "First Last"
    name = plain(raw_name)
    name = corresponding_marker_re.sub("", name).strip().strip(",").strip()
    if not name or name.lower() in ("others", "et al.", "et al", "anonymous"):
        return "", ""
    if "," in name:
        comma_parts = [part.strip() for part in name.split(",")]
        last_name = comma_parts[0]
        # "Last, Jr, First" format
        given_names = comma_parts[-1] if len(comma_parts) > 1 else ""
    else:
        words = name.split()
        if len(words) == 1:
            return words[0], ""
        # lowercase particles such as von, van, de start the last name
        split_at = len(words) - 1
        for position, word in enumerate(words[:-1]):
            if word[0].islower():
                split_at = position
                break
        last_name = " ".join(words[split_at:])
        given_names = " ".join(words[:split_at])
    return last_name, tidy_given(given_names)


def tidy_given(given_names):
    # "OJ" and "O.J." become "O. J.", full names stay unchanged
    given_names = given_names.strip()
    if re.fullmatch(r"[A-Z]{1,4}", given_names):
        return " ".join(char + "." for char in given_names)
    if re.fullmatch(r"(?:[A-Z]\.?-?\s*)+", given_names):
        letters = re.findall(r"[A-Z]", given_names)
        return " ".join(letter + "." for letter in letters)
    return given_names


def author_key(last_name, given_names):
    # surname plus first initial, e.g. "mohler_o"
    surname = surname_key(last_name)
    if not surname:
        return ""
    initials = re.findall(r"[a-z]", fold_text(given_names))
    return "%s_%s" % (surname, initials[0]) if initials else surname


def name_score(variant, count):
    # full given names and accents win, then how often the form was used
    last_name, given_names = variant
    full_given = bool(re.search(r"[a-z]{2,}", given_names))
    has_accents = any(ord(char) > 127 for char in last_name + given_names)
    return (full_given, has_accents, count, len(given_names))


def format_display_name(last_name, given_names):
    return "%s, %s" % (last_name, given_names) if given_names else last_name


# ================================================== #
# WEB OF SCIENCE AFFILIATION FIELD
# ================================================== #

# --- the Web of Science field looks like this, one address per line
#   Wagner, R (Corresponding Author), Karlsruhe Inst Technol, Inst ..., Germany.
#   Wagner, Robert; Moehler, Ottmar, Karlsruhe Inst Technol, Inst ..., Germany.
# OpenAlex style entries only hold "Inst A; Inst B" without authors

def looks_like_name(text):
    # "Last, Given" with a short given part and no institution words
    text = corresponding_marker_re.sub("", text).strip()
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return False
    last_name, given_names = parts
    if re.search(r"\d", text) or len(last_name.split()) > 4 or len(given_names.split()) > 4:
        return False
    for word in last_name.split():
        if word[0].islower() and word.lower() not in name_particles:
            return False
    words = {word.strip(".").lower() for word in re.findall(r"[\w.]+", text)}
    return not (words & institution_words)


def split_affiliation_blocks(raw_value):
    # one block per address; new lines first, then ". Name, X" boundaries
    # a point after a single capital is an initial (M. E), not a block end
    raw_value = raw_value.replace("\r", "")
    blocks = []
    for line_block in re.split(r"(?<=\.)\s*\n\s*", raw_value):
        blocks.extend(re.split(r"(?<=\.)(?<![\s,.;][A-Z]\.)\s*;?\s+(?=[A-Z][^,;.()]{0,40}, [A-Z])", line_block))
    return [block for block in blocks if block.strip()]


def parse_wos_block(block):
    # returns (names, address, other_items)
    items = [item.strip() for item in block.split(";") if item.strip()]
    names = []
    address = ""
    other_items = []
    for item in items:
        if address:
            other_items.append(item)
            continue
        if looks_like_name(item):
            names.append(item)
            continue
        parts = [part.strip() for part in item.split(",")]
        # the last name of a block and the address share one item
        if len(parts) >= 3 and looks_like_name(parts[0] + ", " + parts[1]):
            names.append(parts[0] + ", " + parts[1])
            address = ", ".join(parts[2:])
            continue
        other_items.append(item)
    if not address:
        # names without an address are not trusted, e.g. "Univ Calif, Berkeley"
        other_items = names + other_items
        names = []
    return names, address, other_items


def split_address(address):
    # organisation is the first segment, country the last, city the one before
    segments = [segment.strip() for segment in address.strip().rstrip(".").split(",") if segment.strip()]
    if not segments:
        return "", "", ""
    organisation = segments[0]
    country = normalize_country(segments[-1]) if len(segments) > 1 else ""
    city = clean_city(segments[-2]) if len(segments) > 2 else ""
    return organisation, country, city


def parse_affiliation_field(raw_value):
    # returns (links, flat): links tie a name to an address, flat has no author
    links = []
    flat = []
    for raw_block in split_affiliation_blocks(raw_value):
        block = plain(raw_block).strip().strip(";").strip()
        if not block:
            continue
        names, address, other_items = parse_wos_block(block)
        if names and address:
            organisation, country, city = split_address(address)
            for name in names:
                links.append({
                    "name": corresponding_marker_re.sub("", name).strip(),
                    "corr": bool(corresponding_marker_re.search(name)),
                    "org": organisation, "address": address,
                    "country": country, "city": city,
                })
        for item in other_items:
            item = item.strip().rstrip(".").strip()
            if not item:
                continue
            # an address without authors: organisation is the first segment
            if item.count(",") >= 2:
                organisation, country, city = split_address(item)
                flat.append({"org": organisation, "address": item, "country": country, "city": city})
            else:
                flat.append({"org": item, "address": item, "country": "", "city": ""})
    return links, flat


# ================================================== #
# OTHER FIELDS
# ================================================== #

def extract_year(fields):
    for field_name in ("year", "date", "issued", "urldate"):
        match = re.search(r"(1[89]\d\d|20\d\d)", fields.get(field_name, ""))
        if match:
            return int(match.group(1))
    return None


def extract_venue(fields):
    for field_name in ("journal", "journaltitle", "booktitle", "series",
                       "school", "institution", "organization", "publisher",
                       "howpublished"):
        value = plain(fields.get(field_name, ""))
        if value:
            return value
    return ""


def clean_doi(raw_doi):
    doi = plain(raw_doi).strip()
    doi = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    doi = doi.replace("\\_", "_").replace(" ", "")
    return doi.lower() if doi.startswith("10.") else ""


def doi_to_url(doi):
    if not doi:
        return ""
    return "https://doi.org/" + urllib.parse.quote(doi, safe="/:;()._-<>[]+")


def clean_url(raw_url):
    url = raw_url.strip().replace("\\_", "_").replace("\\%", "%")
    url = url.replace("{", "").replace("}", "")
    return url if re.match(r"https?://", url, flags=re.I) else ""


def extract_openalex_id(fields):
    # OpenAlex harvests keep "OpenAlex: https://openalex.org/W123" in the note
    for field_name in ("note", "openalex", "url", "annote"):
        match = re.search(r"openalex\.org/(W\d+)", fields.get(field_name, ""), flags=re.I)
        if match:
            return match.group(1).upper()
    return ""


def build_base_record(entry):
    fields = entry["fields"]
    title_clean = clean_latex(fields.get("title", ""))
    doi = clean_doi(fields.get("doi", ""))
    authors = []
    for raw_name in split_top_level(fields.get("author", fields.get("editor", "")), "and"):
        last_name, given_names = split_name(raw_name)
        if last_name:
            authors.append((last_name, given_names))
    links, flat = parse_affiliation_field(fields.get("affiliation", ""))
    # the Web of Science "affiliations" field holds organisation names per paper
    for item in plain(fields.get("affiliations", "")).split(";"):
        if item.strip():
            flat.append({"org": item.strip(), "address": item.strip(), "country": "", "city": ""})
    return {
        "key": entry["key"],
        "year": extract_year(fields),
        "title_plain": to_plain(title_clean),
        "title_html": to_html(title_clean),
        "venue": extract_venue(fields),
        "type_label": type_labels.get(entry["entry_type"], "Other"),
        "doi": doi,
        "url": "" if doi else clean_url(fields.get("url", "")),
        "openalex_id": extract_openalex_id(fields),
        "authors": authors,
        "wos_links": links,
        "flat_affiliations": flat,
    }


# ================================================== #
# NETWORK CLIENT
# ================================================== #

class ApiClient:
    # small JSON client with retries; stops asking after repeated network failures

    def __init__(self, enabled=True, verbose=False):
        self.enabled = enabled
        self.verbose = verbose
        self.request_count = 0
        self.failure_count = 0
        self.offline = False
        # True only when the last answer was 404, a real "not found"
        self.last_not_found = False

    def note_failure(self, reason):
        # repeated failures switch the client off for the rest of the run
        self.failure_count += 1
        if self.failure_count >= max_network_failures and not self.offline:
            print("  network not reachable (%s), continuing with cached data only" % reason)
            self.offline = True

    def get_json(self, url):
        self.last_not_found = False
        if not self.enabled or self.offline:
            return None
        headers = {"User-Agent": "AIDA-bibliometrics/1.0 (mailto:%s)" % (openalex_mailto or "none")}
        for attempt in range(4):
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=request_timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.request_count += 1
                self.failure_count = 0
                time.sleep(request_pause)
                return payload
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    self.last_not_found = True
                    return None
                if error.code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                if self.verbose:
                    print("  HTTP %d for %s" % (error.code, url[:140]))
                self.note_failure("HTTP %d" % error.code)
                return None
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
                    json.JSONDecodeError) as error:
                self.note_failure(error)
                if self.offline:
                    return None
                time.sleep(2 ** attempt)
        return None


def openalex_query(path, params):
    params = dict(params)
    if openalex_mailto:
        params["mailto"] = openalex_mailto
    if openalex_api_key:
        params["api_key"] = openalex_api_key
    return openalex_url + path + "?" + urllib.parse.urlencode(params, safe=":|,")


def load_cache(CACHE_PATH):
    if CACHE_PATH.is_file():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print("  cache unreadable, starting empty: %s" % CACHE_PATH)
    return {}


def save_cache(CACHE_PATH, cache, write_enabled):
    if not write_enabled:
        return
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEMP_PATH = CACHE_PATH.with_suffix(".tmp")
    TEMP_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    TEMP_PATH.replace(CACHE_PATH)


# ================================================== #
# MAP ASSETS
# ================================================== #

def load_asset(url, CACHE_PATH, allow_download, check_json=False):
    # text of a static file: from the cache, else downloaded once; "" when not available
    if CACHE_PATH.is_file():
        return CACHE_PATH.read_text(encoding="utf-8")
    if not allow_download:
        return ""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "AIDA-bibliometrics/1.0"})
        with urllib.request.urlopen(request, timeout=request_timeout) as response:
            text = response.read().decode("utf-8")
        if check_json:
            json.loads(text)
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
            UnicodeDecodeError, ValueError) as error:
        print("  could not download %s (%s)" % (url, error))
        return ""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(text, encoding="utf-8")
    print("  downloaded %s" % CACHE_PATH.name)
    return text


def load_map_assets(CACHE_DIR, allow_download):
    # the page works fully offline once these three files are in the cache
    return {
        "world": load_asset(world_map_url, CACHE_DIR / "world_countries_50m.json", allow_download, True),
        "leaflet_js": load_asset(leaflet_js_url, CACHE_DIR / "leaflet.js", allow_download),
        "leaflet_css": load_asset(leaflet_css_url, CACHE_DIR / "leaflet.css", allow_download),
    }


# ================================================== #
# OPENALEX
# ================================================== #

# --- only the fields needed here are requested and stored
work_select = "id,doi,authorships,corresponding_author_ids"
institution_select = "id,display_name,ror,country_code,geo,type"


def compact_work(work):
    corresponding_ids = {short_openalex_id(item) for item in work.get("corresponding_author_ids") or []}
    authorships = []
    for item in work.get("authorships") or []:
        author = item.get("author") or {}
        author_id = short_openalex_id(author.get("id"))
        institutions = []
        for institution in item.get("institutions") or []:
            if institution.get("display_name"):
                institutions.append({
                    "id": short_openalex_id(institution.get("id")),
                    "name": institution.get("display_name") or "",
                    "ror": institution.get("ror") or "",
                    "cc": institution.get("country_code") or "",
                })
        authorships.append({
            "name": author.get("display_name") or item.get("raw_author_name") or "",
            "corr": bool(item.get("is_corresponding")) or (author_id and author_id in corresponding_ids),
            "inst": institutions,
        })
    return {"id": short_openalex_id(work.get("id")), "authorships": authorships}


def fetch_openalex_works(records, cache, client, refresh, CACHE_PATH, write_enabled):
    # cache keys: "doi:<doi>" and "id:<W...>", a cached None means not found
    def needs(cache_key):
        return cache_key not in cache or (refresh and cache[cache_key] is None)

    doi_list = sorted({record["doi"] for record in records if record["doi"] and needs("doi:" + record["doi"])})
    batch_dois = [doi for doi in doi_list if "|" not in doi and "," not in doi]
    single_dois = [doi for doi in doi_list if doi not in batch_dois]
    if doi_list and client.enabled:
        print("  OpenAlex: %d DOIs to request" % len(doi_list))

    # --- DOIs in batches of 50
    for start in range(0, len(batch_dois), openalex_batch_size):
        if client.offline:
            break
        chunk = batch_dois[start:start + openalex_batch_size]
        payload = client.get_json(openalex_query("/works", {
            "filter": "doi:" + "|".join(chunk), "per-page": openalex_batch_size, "select": work_select}))
        if payload is None:
            continue
        found = {}
        for work in payload.get("results") or []:
            found[clean_doi(work.get("doi") or "")] = compact_work(work)
        for doi in chunk:
            cache["doi:" + doi] = found.get(doi)
        if (start // openalex_batch_size) % 10 == 9:
            save_cache(CACHE_PATH, cache, write_enabled)
            print("    %d of %d DOIs" % (min(start + openalex_batch_size, len(batch_dois)), len(batch_dois)))

    # --- DOIs with characters that break the batch filter
    for doi in single_dois:
        if client.offline:
            break
        payload = client.get_json(openalex_query("/works/https://doi.org/" + urllib.parse.quote(doi, safe="/"),
                                                 {"select": work_select}))
        # a failed request is not cached, only a real "not found"
        if payload or client.last_not_found:
            cache["doi:" + doi] = compact_work(payload) if payload else None

    # --- papers without DOI or not found by DOI, by OpenAlex work id
    id_list = []
    for record in records:
        work_id = record["openalex_id"]
        if not work_id or not needs("id:" + work_id):
            continue
        if record["doi"] and cache.get("doi:" + record["doi"]):
            continue
        id_list.append(work_id)
    for work_id in sorted(set(id_list)):
        if client.offline:
            break
        payload = client.get_json(openalex_query("/works/" + work_id, {"select": work_select}))
        if payload or client.last_not_found:
            cache["id:" + work_id] = compact_work(payload) if payload else None

    save_cache(CACHE_PATH, cache, write_enabled)
    return cache


def fetch_openalex_institutions(institution_ids, cache, client, refresh, CACHE_PATH, write_enabled):
    # coordinates, country and city of every OpenAlex institution
    pending = sorted(i for i in institution_ids if i and (i not in cache or (refresh and cache[i] is None)))
    if pending and client.enabled:
        print("  OpenAlex: %d institutions to request" % len(pending))

    def store(item):
        geo = item.get("geo") or {}
        cache[short_openalex_id(item.get("id"))] = {
            "name": item.get("display_name") or "", "ror": item.get("ror") or "",
            "cc": item.get("country_code") or "", "country": geo.get("country") or "",
            "city": geo.get("city") or "", "lat": geo.get("latitude"), "lon": geo.get("longitude"),
            "type": item.get("type") or "",
        }

    for start in range(0, len(pending), openalex_batch_size):
        if client.offline:
            break
        chunk = pending[start:start + openalex_batch_size]
        payload = client.get_json(openalex_query("/institutions", {
            "filter": "openalex:" + "|".join(chunk), "per-page": openalex_batch_size,
            "select": institution_select}))
        if payload is None:
            # the batch filter was refused, ask one by one
            for institution_id in chunk:
                single = client.get_json(openalex_query("/institutions/" + institution_id,
                                                        {"select": institution_select}))
                if single:
                    store(single)
                elif client.last_not_found:
                    cache[institution_id] = None
            continue
        returned = set()
        for item in payload.get("results") or []:
            store(item)
            returned.add(short_openalex_id(item.get("id")))
        for institution_id in chunk:
            if institution_id not in returned:
                cache.setdefault(institution_id, None)
    save_cache(CACHE_PATH, cache, write_enabled)
    return cache


def ror_geocode(query, cache, client, refresh):
    # ROR affiliation matching, only the match ROR marks as "chosen" is used
    if query in cache and not (refresh and cache[query] is None):
        return cache[query]
    if not client.enabled or client.offline:
        return None
    payload = client.get_json(ror_url + "?" + urllib.parse.urlencode({"affiliation": query}))
    if payload is None:
        return None
    result = None
    for item in payload.get("items") or []:
        if not item.get("chosen"):
            continue
        organisation = item.get("organization") or {}
        display_name = ""
        for name in organisation.get("names") or []:
            if "ror_display" in (name.get("types") or []):
                display_name = name.get("value") or ""
        locations = organisation.get("locations") or []
        details = (locations[0].get("geonames_details") or {}) if locations else {}
        result = {"ror": organisation.get("id") or "", "name": display_name,
                  "lat": details.get("lat"), "lon": details.get("lng"),
                  "country": details.get("country_name") or "", "city": details.get("name") or ""}
        break
    cache[query] = result
    return result


# ================================================== #
# ALIAS AND COORDINATE FILES
# ================================================== #

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


def pick_column(row, names):
    # first column whose header matches one of the names, case insensitive
    for name in names:
        for header, value in row.items():
            if header and header.strip().lower() == name and value is not None:
                return value.strip()
    return ""


def load_corresponding_overrides(OVERRIDE_PATH):
    # doi_or_key,author,corresponding  e.g. 10.5194/acp-18-2021-2018,"Wagner, R.",no
    # corrections sent by the authors themselves; they win over OpenAlex and Web of Science
    overrides = {}
    for row in read_csv_rows(OVERRIDE_PATH):
        paper = pick_column(row, ("doi_or_key", "doi", "paper_key", "key")).lower()
        author = pick_column(row, ("author", "name"))
        flag = pick_column(row, ("corresponding", "is_corresponding", "value")).lower()
        if not paper or not author or flag not in ("yes", "no", "true", "false", "1", "0"):
            continue
        paper = paper.replace("https://doi.org/", "").strip()
        key = author_key(*split_name(author))
        if key:
            overrides[(paper, key)] = flag in ("yes", "true", "1")
    return overrides


def load_author_aliases(ALIAS_PATH):
    # variant,canonical  e.g. "Mohler, O.","Möhler, Ottmar"
    alias_keys = {}
    forced_names = {}
    for row in read_csv_rows(ALIAS_PATH):
        variant = pick_column(row, ("variant",))
        canonical = pick_column(row, ("canonical",))
        if not variant or not canonical:
            continue
        canonical_last, canonical_given = split_name(canonical)
        variant_key = author_key(*split_name(variant))
        canonical_key = author_key(canonical_last, canonical_given)
        if variant_key and canonical_key:
            alias_keys[variant_key] = canonical_key
            forced_names[canonical_key] = format_display_name(canonical_last, canonical_given)
    return alias_keys, forced_names


def load_affiliation_aliases(ALIAS_PATH):
    # pattern,canonical  the csv patterns are tried before the defaults
    patterns = []
    for row in read_csv_rows(ALIAS_PATH):
        pattern = pick_column(row, ("pattern",))
        canonical = pick_column(row, ("canonical",))
        if not pattern or not canonical:
            continue
        try:
            patterns.append((re.compile(pattern, re.I), canonical))
        except re.error as error:
            print("  bad pattern in %s: %s (%s)" % (ALIAS_PATH.name, pattern, error))
    for pattern, canonical in affiliation_aliases_default:
        patterns.append((re.compile(pattern, re.I), canonical))
    return patterns


def load_coordinate_table(COORDS_PATH):
    # name -> lat, lon, country, city; column names are detected
    table = {}
    for row in read_csv_rows(COORDS_PATH):
        name = pick_column(row, ("name", "institution", "affiliation", "display_name",
                                 "organisation", "organization"))
        try:
            latitude = float(pick_column(row, ("lat", "latitude")))
            longitude = float(pick_column(row, ("lon", "lng", "longitude")))
        except ValueError:
            continue
        if name:
            table[affiliation_key(name)] = {
                "lat": latitude, "lon": longitude,
                "country": pick_column(row, ("country",)), "city": pick_column(row, ("city",)),
            }
    return table


# ================================================== #
# AUTHOR REGISTRY
# ================================================== #

class AuthorRegistry:
    # one item per author key, with every name form that was seen

    def __init__(self, alias_keys, forced_names):
        self.alias_keys = alias_keys
        self.forced_names = forced_names
        self.items = []
        self.index_by_key = {}

    def key_for(self, last_name, given_names):
        key = author_key(last_name, given_names)
        return self.alias_keys.get(key, key)

    def resolve(self, last_name, given_names):
        key = self.key_for(last_name, given_names)
        if not key:
            return None
        index = self.index_by_key.get(key)
        if index is None:
            index = len(self.items)
            self.items.append({"key": key, "variants": Counter()})
            self.index_by_key[key] = index
        self.items[index]["variants"][(last_name, given_names)] += 1
        return index

    def display_name(self, item):
        if item["key"] in self.forced_names:
            return self.forced_names[item["key"]]
        ranked = sorted(item["variants"].items(), key=lambda pair: name_score(pair[0], pair[1]), reverse=True)
        return format_display_name(*ranked[0][0]) if ranked else item["key"]

    def other_names(self, item):
        shown = self.display_name(item)
        names = [format_display_name(*variant) for variant in item["variants"]]
        return [name for name in dict.fromkeys(names) if name != shown][:6]


# ================================================== #
# AFFILIATION REGISTRY
# ================================================== #

class AffiliationRegistry:
    # one item per institution; OpenAlex id, ROR id and name key all point to it

    def __init__(self, patterns):
        self.patterns = patterns
        self.items = []
        self.by_key = {}
        self.by_external = {}

    def alias_for(self, text):
        folded = fold_text(text)
        for pattern, canonical in self.patterns:
            if pattern.search(folded):
                return canonical
        return ""

    def resolve(self, name, address="", openalex_id="", ror="", country="", city=""):
        name = re.sub(r"\s+", " ", name or "").strip(" .;,")
        if not name:
            return None
        canonical = self.alias_for(address or name) or self.alias_for(name)
        key = affiliation_key(canonical or name)
        if not key:
            return None
        index = None
        for external_id in (openalex_id, ror):
            if external_id and external_id in self.by_external:
                index = self.by_external[external_id]
                break
        if index is None:
            index = self.by_key.get(key)
        if index is None:
            index = len(self.items)
            self.items.append({
                "key": key, "names": Counter(), "forced": "", "openalex_name": "",
                "ror_name": "", "openalex_id": "", "ror": "", "countries": Counter(),
                "cities": Counter(), "queries": Counter(), "lat": None, "lon": None,
                "coord_source": "", "geo_country": "", "geo_city": "", "merged_into": None,
            })
        self.by_key.setdefault(key, index)
        item = self.items[index]
        if canonical:
            item["forced"] = canonical
        if openalex_id:
            item["openalex_id"] = item["openalex_id"] or openalex_id
            item["openalex_name"] = item["openalex_name"] or name
            self.by_external[openalex_id] = index
            # the OpenAlex name key also points here, so Web of Science forms can match it
            self.by_key.setdefault(affiliation_key(name), index)
        if ror:
            item["ror"] = item["ror"] or ror
            self.by_external.setdefault(ror, index)
        item["names"][name] += 1
        if country:
            item["countries"][country] += 1
        if city:
            item["cities"][city] += 1
        if address and not openalex_id:
            item["queries"][address] += 1
        return index

    def display_name(self, item):
        if item["forced"]:
            return item["forced"]
        if item["openalex_name"]:
            return item["openalex_name"]
        if item["ror_name"]:
            return item["ror_name"]
        ranked = sorted(item["names"].items(),
                        key=lambda pair: (not is_abbreviated(pair[0]), not pair[0].isupper(),
                                          pair[1], len(pair[0])),
                        reverse=True)
        return ranked[0][0] if ranked else item["key"]

    def country(self, item):
        if item["geo_country"]:
            return item["geo_country"]
        return item["countries"].most_common(1)[0][0] if item["countries"] else ""

    def city(self, item):
        if item["geo_city"]:
            return item["geo_city"]
        return item["cities"].most_common(1)[0][0] if item["cities"] else ""

    def merge_by_ror(self):
        # items sharing one ROR id become one item, OpenAlex items are kept
        target_by_ror = {}
        remap = {}
        order = sorted(range(len(self.items)), key=lambda i: (not self.items[i]["openalex_id"], i))
        for index in order:
            item = self.items[index]
            ror = item["ror"]
            if not ror:
                continue
            if ror not in target_by_ror:
                target_by_ror[ror] = index
                continue
            target = self.items[target_by_ror[ror]]
            for counter_name in ("names", "countries", "cities", "queries"):
                target[counter_name].update(item[counter_name])
            if target["lat"] is None and item["lat"] is not None:
                for field in ("lat", "lon", "coord_source", "geo_country", "geo_city"):
                    target[field] = item[field]
            target["forced"] = target["forced"] or item["forced"]
            item["merged_into"] = target_by_ror[ror]
            remap[index] = target_by_ror[ror]
        return remap


# ================================================== #
# DATASET
# ================================================== #

def find_work(record, works_cache):
    work = works_cache.get("doi:" + record["doi"]) if record["doi"] else None
    if not work and record["openalex_id"]:
        work = works_cache.get("id:" + record["openalex_id"])
    return work


def add_unique(values, value):
    if value is not None and value not in values:
        values.append(value)


def build_dataset(records, works_cache, institution_cache, author_registry, affiliation_registry,
                  corresponding_overrides=None):
    # returns papers (registry indices) and a Counter with coverage numbers
    stats = Counter()
    journal_index = {}
    journal_names = []
    # raw affiliation texts as found in the source, kept so profiles can show them
    raw_texts = []
    raw_index = {}

    def raw_id(text):
        text = re.sub(r"\s+", " ", text or "").strip()
        if text not in raw_index:
            raw_index[text] = len(raw_texts)
            raw_texts.append(text)
        return raw_index[text]

    # --- OpenAlex institutions first, so Web of Science forms attach to them
    for record in records:
        record["work"] = find_work(record, works_cache)
        for authorship in (record["work"] or {}).get("authorships", []):
            for institution in authorship["inst"]:
                info = institution_cache.get(institution["id"]) or {}
                affiliation_registry.resolve(
                    institution["name"], openalex_id=institution["id"],
                    ror=institution["ror"] or info.get("ror", ""),
                    country=info.get("country", ""), city=info.get("city", ""))

    overrides = corresponding_overrides or {}
    papers = []
    for record in records:
        work = record["work"]
        authorships = work["authorships"] if work else []
        # --- a correction file can name a paper by its DOI or by its citation key
        override_ids = [item for item in ((record["doi"] or "").lower(), (record["key"] or "").lower()) if item]
        stats["papers"] += 1
        stats["papers_openalex"] += 1 if work else 0
        stats["papers_wos_links"] += 1 if record["wos_links"] else 0

        # --- authors in order; the bib author field wins, OpenAlex fills gaps
        names = record["authors"]
        if not names and authorships:
            names = [name for name in (split_name(item["name"]) for item in authorships) if name[0]]
        author_ids = []
        for last_name, given_names in names:
            add_unique(author_ids, author_registry.resolve(last_name, given_names))
        author_keys = [author_registry.items[index]["key"] for index in author_ids]

        # --- OpenAlex authorships and Web of Science links by author key
        openalex_by_key = {}
        for authorship in authorships:
            key = author_registry.key_for(*split_name(authorship["name"]))
            if key:
                openalex_by_key.setdefault(key, authorship)
        use_position = len(authorships) == len(author_ids)
        wos_by_key = {}
        wos_corresponding = set()
        for link in record["wos_links"]:
            key = author_registry.key_for(*split_name(link["name"]))
            if not key:
                continue
            wos_by_key.setdefault(key, []).append(link)
            if link["corr"]:
                wos_corresponding.add(key)

        # --- affiliations and corresponding flag of every author
        author_affiliations = []
        author_sources = []
        author_raws = []
        corresponding = []
        for position, (author_index, key) in enumerate(zip(author_ids, author_keys)):
            authorship = openalex_by_key.get(key)
            if authorship is None and use_position:
                authorship = authorships[position]
            affiliation_ids = []
            # pairs of [affiliation, raw text] show where each affiliation came from
            raw_ids = []
            # --- source codes: "o" OpenAlex, "w" Web of Science address, "" unknown
            source = ""
            if authorship and authorship["inst"]:
                for institution in authorship["inst"]:
                    affiliation_id = affiliation_registry.resolve(
                        institution["name"], openalex_id=institution["id"], ror=institution["ror"])
                    add_unique(affiliation_ids, affiliation_id)
                    if affiliation_id is not None:
                        add_unique(raw_ids, [affiliation_id, raw_id(institution["name"])])
                stats["author_slots_openalex"] += 1
                source = "o"
            elif key in wos_by_key:
                for link in wos_by_key[key]:
                    affiliation_id = affiliation_registry.resolve(
                        link["org"], address=link["address"], country=link["country"], city=link["city"])
                    add_unique(affiliation_ids, affiliation_id)
                    if affiliation_id is not None:
                        add_unique(raw_ids, [affiliation_id, raw_id(link["address"])])
                stats["author_slots_wos"] += 1
                source = "w"
            else:
                stats["author_slots_unknown"] += 1
            author_affiliations.append(affiliation_ids)
            author_sources.append(source)
            author_raws.append(raw_ids)
            is_corresponding = key in wos_corresponding or bool(authorship and authorship["corr"])
            # --- a correction from the authors themselves replaces what the sources say
            for paper_id in override_ids:
                if (paper_id, key) in overrides:
                    is_corresponding = overrides[(paper_id, key)]
                    stats["corresponding_overridden"] += 1
                    break
            if is_corresponding:
                corresponding.append(author_index)
        stats["corresponding_unmatched"] += len(wos_corresponding - set(author_keys))
        corresponding_known = bool(wos_corresponding) or any(item["corr"] for item in authorships)
        corresponding_known = corresponding_known or any(
            (paper_id, key) in overrides for paper_id in override_ids for key in author_keys)
        stats["papers_corresponding_known"] += 1 if corresponding_known else 0

        # --- paper level affiliations; the flat list is used only without author links
        paper_affiliations = []
        for affiliation_ids in author_affiliations:
            for affiliation_id in affiliation_ids:
                add_unique(paper_affiliations, affiliation_id)
        if not paper_affiliations:
            for authorship in authorships:
                for institution in authorship["inst"]:
                    add_unique(paper_affiliations, affiliation_registry.resolve(
                        institution["name"], openalex_id=institution["id"], ror=institution["ror"]))
        if not paper_affiliations:
            for flat in record["flat_affiliations"]:
                add_unique(paper_affiliations, affiliation_registry.resolve(
                    flat["org"], address=flat["address"], country=flat["country"], city=flat["city"]))
            stats["papers_paper_level_only"] += 1 if paper_affiliations else 0
        stats["papers_without_affiliation"] += 0 if paper_affiliations else 1

        # --- journal, case insensitive
        journal_id = -1
        if record["venue"]:
            key = journal_key(record["venue"])
            if key not in journal_index:
                journal_index[key] = len(journal_names)
                journal_names.append(Counter())
            journal_id = journal_index[key]
            journal_names[journal_id][record["venue"]] += 1

        papers.append({
            "k": record["key"], "y": record["year"], "th": record["title_html"],
            "tp": record["title_plain"], "j": journal_id, "ty": record["type_label"],
            "doi": record["doi"], "url": record["url"], "a": author_ids, "c": corresponding,
            "aa": author_affiliations, "pa": paper_affiliations, "ck": corresponding_known,
            "as": author_sources, "ar": author_raws,
        })

    # --- journal display name: most used form, all capitals avoided
    journals = []
    for counter in journal_names:
        ranked = sorted(counter.items(), key=lambda pair: (not pair[0].isupper(), pair[1]), reverse=True)
        journals.append(ranked[0][0])
    return papers, journals, raw_texts, stats


# ================================================== #
# COORDINATES
# ================================================== #

def locate_affiliations(affiliation_registry, institution_cache, manual_table, existing_table,
                        ror_cache, client, refresh, use_ror, verbose):
    # order: manual file, OpenAlex, aida_maps.py cache, ROR matching
    source_counts = Counter()
    for item in affiliation_registry.items:
        names = [affiliation_registry.display_name(item)] + list(item["names"])
        hit = None
        source = ""
        for name in names:
            if affiliation_key(name) in manual_table:
                hit, source = manual_table[affiliation_key(name)], "manual"
                break
        if hit is None and item["openalex_id"]:
            info = institution_cache.get(item["openalex_id"])
            if info and info.get("lat") is not None:
                hit, source = info, "openalex"
        if hit is None:
            for name in names:
                if affiliation_key(name) in existing_table:
                    hit, source = existing_table[affiliation_key(name)], "coords_csv"
                    break
        if hit is None and use_ror and item["queries"]:
            query = item["queries"].most_common(1)[0][0]
            result = ror_geocode(query, ror_cache, client, refresh)
            if result:
                item["ror"] = item["ror"] or result.get("ror", "")
                item["ror_name"] = result.get("name", "")
                if result.get("lat") is not None:
                    hit, source = result, "ror"
        if hit is not None:
            item["lat"] = round(float(hit["lat"]), 5)
            item["lon"] = round(float(hit["lon"]), 5)
            item["geo_country"] = hit.get("country", "") or ""
            item["geo_city"] = hit.get("city", "") or ""
            item["coord_source"] = source
        source_counts[source or "none"] += 1
    return source_counts


# ================================================== #
# DATA FOR THE PAGES
# ================================================== #

def finalize_data(papers, journals, raw_texts, author_registry, affiliation_registry, remap, source_name):
    # merged affiliations are removed and all indices renumbered
    kept = [index for index in range(len(affiliation_registry.items)) if index not in remap]
    new_index = {old: new for new, old in enumerate(kept)}

    def map_affiliation(index):
        return new_index[remap.get(index, index)]

    for paper in papers:
        paper["aa"] = [list(dict.fromkeys(map_affiliation(i) for i in ids)) for ids in paper["aa"]]
        paper["ar"] = [[[map_affiliation(pair[0]), pair[1]] for pair in pairs] for pairs in paper["ar"]]
        paper["pa"] = list(dict.fromkeys(map_affiliation(i) for i in paper["pa"]))

    authors = [{"k": item["key"], "n": author_registry.display_name(item),
                "v": author_registry.other_names(item)} for item in author_registry.items]
    affiliations = []
    used_keys = set()
    for old in kept:
        item = affiliation_registry.items[old]
        key = item["key"]
        while key in used_keys:
            key += "_"
        used_keys.add(key)
        affiliations.append({
            "k": key, "n": affiliation_registry.display_name(item),
            "co": affiliation_registry.country(item), "ci": affiliation_registry.city(item),
            "lat": item["lat"], "lon": item["lon"], "ror": item["ror"],
            "oa": item["openalex_id"], "src": item["coord_source"],
            # other spellings merged into this affiliation, most frequent first
            "v": [name for name, _ in item["names"].most_common(8)
                  if name != affiliation_registry.display_name(item)],
        })
    return {
        "papers": papers, "authors": authors, "affs": affiliations, "journals": journals, "raws": raw_texts,
        "pages": {"authors": authors_html_name, "affs": affiliations_html_name, "map": map_html_name},
        "source": source_name, "built": datetime.date.today().isoformat(),
    }


# ================================================== #
# CSV OUTPUT
# ================================================== #

role_names = ["first_and_corresponding", "first_only", "corresponding_not_first", "co_author"]


def role_index(position, author_index, corresponding):
    first = position == 0
    corr = author_index in corresponding
    if first and corr:
        return 0
    if first:
        return 1
    return 2 if corr else 3


def new_counts():
    return {"papers": 0, "roles": [0, 0, 0, 0], "years": []}


def add_count(counts, role, year):
    counts["papers"] += 1
    counts["roles"][role] += 1
    if year is not None:
        counts["years"].append(year)


def years_range(years):
    return (min(years), max(years)) if years else ("", "")


def write_csv(CSV_PATH, header, rows):
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


# --- where the affiliation of an author on a paper was found
source_names = {"o": "OpenAlex", "w": "Web of Science address"}


def write_csv_files(data, OUT_DIR):
    papers, authors, affs, journals = data["papers"], data["authors"], data["affs"], data["journals"]
    author_totals = {}
    author_affiliation = {}
    author_journal = {}
    author_type = {}
    affiliation_totals = {}
    country_totals = {}
    paper_rows = []

    for paper in papers:
        corresponding = set(paper["c"])
        for position, author_index in enumerate(paper["a"]):
            role = role_index(position, author_index, corresponding)
            add_count(author_totals.setdefault(author_index, new_counts()), role, paper["y"])
            for affiliation_index in paper["aa"][position]:
                add_count(author_affiliation.setdefault((author_index, affiliation_index), new_counts()),
                          role, paper["y"])
            if paper["j"] >= 0:
                add_count(author_journal.setdefault((author_index, paper["j"]), new_counts()), role, paper["y"])
            author_type.setdefault(author_index, Counter())[paper["ty"]] += 1
            paper_rows.append([paper["k"], paper["y"] or "", paper["doi"], position + 1,
                               authors[author_index]["n"], authors[author_index]["k"], role_names[role],
                               "yes" if author_index in corresponding else "",
                               "; ".join(affs[i]["n"] for i in paper["aa"][position]),
                               source_names.get(paper["as"][position], ""),
                               " | ".join(data["raws"][pair[1]] for pair in paper["ar"][position])])

        # --- affiliation roles: first author here, corresponding here, other
        first_affiliations = set(paper["aa"][0]) if paper["aa"] else set()
        corresponding_affiliations = set()
        for position, author_index in enumerate(paper["a"]):
            if author_index in corresponding:
                corresponding_affiliations.update(paper["aa"][position])
        for affiliation_index in paper["pa"]:
            totals = affiliation_totals.setdefault(affiliation_index, {
                "papers": 0, "first": 0, "corr": 0, "authors": set(), "years": []})
            totals["papers"] += 1
            if affiliation_index in first_affiliations:
                totals["first"] += 1
            elif affiliation_index in corresponding_affiliations:
                totals["corr"] += 1
            if paper["y"] is not None:
                totals["years"].append(paper["y"])
            for position, author_index in enumerate(paper["a"]):
                if affiliation_index in paper["aa"][position]:
                    totals["authors"].add(author_index)
        countries = {affs[i]["co"] for i in paper["pa"] if affs[i]["co"]}
        first_countries = {affs[i]["co"] for i in first_affiliations if affs[i]["co"]}
        for country in countries:
            totals = country_totals.setdefault(country, {"papers": 0, "first": 0, "affs": set()})
            totals["papers"] += 1
            totals["first"] += 1 if country in first_countries else 0
            totals["affs"].update(i for i in paper["pa"] if affs[i]["co"] == country)

    # --- authors ranking
    rows = []
    for author_index, counts in author_totals.items():
        affiliation_counter = Counter({a: c["papers"] for (x, a), c in author_affiliation.items() if x == author_index})
        journal_counter = Counter({j: c["papers"] for (x, j), c in author_journal.items() if x == author_index})
        first_year, last_year = years_range(counts["years"])
        rows.append([authors[author_index]["n"], authors[author_index]["k"], counts["papers"],
                     counts["roles"][0] + counts["roles"][1]] + counts["roles"] +
                    [affs[affiliation_counter.most_common(1)[0][0]]["n"] if affiliation_counter else "",
                     len(affiliation_counter),
                     journals[journal_counter.most_common(1)[0][0]] if journal_counter else "",
                     first_year, last_year])
    rows.sort(key=lambda row: (-row[2], -row[3], row[0].lower()))
    write_csv(OUT_DIR / "AIDA_authors_ranking.csv",
              ["rank", "author", "author_key", "papers", "first_author_all"] + role_names +
              ["main_affiliation", "affiliations", "top_journal", "first_year", "last_year"],
              [[rank + 1] + row for rank, row in enumerate(rows)])

    # --- author and affiliation, author and journal
    def pair_rows(pairs, name_of):
        output = []
        for (author_index, other), counts in pairs.items():
            first_year, last_year = years_range(counts["years"])
            output.append([authors[author_index]["n"]] + name_of(other) + [counts["papers"],
                          counts["roles"][0] + counts["roles"][1]] + counts["roles"] + [first_year, last_year])
        output.sort(key=lambda row: (-row[len(row) - 8], row[0].lower()))
        return output

    write_csv(OUT_DIR / "AIDA_author_affiliation.csv",
              ["author", "affiliation", "country", "papers", "first_author_all"] + role_names +
              ["first_year", "last_year"],
              pair_rows(author_affiliation, lambda a: [affs[a]["n"], affs[a]["co"]]))
    write_csv(OUT_DIR / "AIDA_author_journal.csv",
              ["author", "journal", "papers", "first_author_all"] + role_names + ["first_year", "last_year"],
              pair_rows(author_journal, lambda j: [journals[j]]))

    # --- author and publication type
    type_names = sorted({paper["ty"] for paper in papers})
    rows = [[authors[a]["n"], sum(counter.values())] + [counter.get(t, 0) for t in type_names]
            for a, counter in author_type.items()]
    rows.sort(key=lambda row: (-row[1], row[0].lower()))
    write_csv(OUT_DIR / "AIDA_author_type.csv", ["author", "papers"] + type_names, rows)

    # --- affiliations and countries
    rows = []
    for affiliation_index, totals in affiliation_totals.items():
        aff = affs[affiliation_index]
        first_year, last_year = years_range(totals["years"])
        rows.append([aff["n"], aff["co"], aff["ci"], aff["ror"], aff["oa"], aff["lat"] if aff["lat"] is not None else "",
                     aff["lon"] if aff["lon"] is not None else "", aff["src"], totals["papers"], totals["first"],
                     totals["corr"], totals["papers"] - totals["first"] - totals["corr"], len(totals["authors"]),
                     first_year, last_year])
    rows.sort(key=lambda row: (-row[8], row[0].lower()))
    write_csv(OUT_DIR / "AIDA_affiliations_ranking.csv",
              ["rank", "affiliation", "country", "city", "ror", "openalex_id", "lat", "lon", "coord_source",
               "papers", "first_author_papers", "corresponding_not_first_papers", "other_papers", "authors",
               "first_year", "last_year"],
              [[rank + 1] + row for rank, row in enumerate(rows)])
    rows = sorted(([country, t["papers"], t["first"], len(t["affs"])] for country, t in country_totals.items()),
                  key=lambda row: (-row[1], row[0]))
    write_csv(OUT_DIR / "AIDA_countries_ranking.csv",
              ["country", "papers", "first_author_papers", "affiliations"], rows)
    write_csv(OUT_DIR / "AIDA_paper_authors.csv",
              ["paper_key", "year", "doi", "position", "author", "author_key", "role", "corresponding",
               "affiliations", "affiliation_source", "affiliation_as_written"], paper_rows)


# ================================================== #
# HTML SHARED PARTS
# ================================================== #

# --- style shared by the three pages, same palette as the publication list
SHARED_CSS = r'''
  :root {
    --paper: #FBFCFC;
    --ink: #1F2B30;
    --muted: #5B696E;
    --rule: #D3DDDF;
    --green: #009682;
    --green-deep: #00705F;
    --ice: #E3F0F2;
    --slate: #56679A;
    --serif: Charter, "Bitstream Charter", "Sitka Text", Cambria, Georgia, serif;
    --sans: "Source Sans 3", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--paper); color: var(--ink); font-family: var(--sans); font-size: 15px; line-height: 1.45; }
  .page { max-width: 1320px; margin: 0 auto; padding: 28px 28px 64px; }

  /* page links */
  nav.pages { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 22px; }
  nav.pages a { font-size: 0.88rem; padding: 4px 12px; border: 1px solid var(--rule); border-radius: 999px; color: var(--ink); text-decoration: none; background: #fff; }
  nav.pages a:hover { border-color: var(--green); }
  nav.pages a[aria-current="page"] { background: var(--green-deep); border-color: var(--green-deep); color: #fff; }

  h1 { font-family: var(--serif); font-weight: 600; font-size: 2.1rem; line-height: 1.15; margin: 0 0 6px; }
  .summary { color: var(--muted); margin: 0 0 24px; font-size: 1rem; }

  /* top panel: overview or selected profile */
  .panel { border-top: 2px solid var(--ink); padding-top: 16px; margin-bottom: 26px; }
  .panel-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 16px; margin-bottom: 14px; }
  .panel-head h2 { font-family: var(--serif); font-weight: 600; font-size: 1.55rem; line-height: 1.2; margin: 0; }
  .panel-head .sub { color: var(--muted); }
  .panel-head .spacer { flex: 1 1 auto; }
  .also { color: var(--muted); font-size: 0.85rem; margin: -8px 0 14px; }
  .tile-group { color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; margin: 12px 0 6px; }
  .aff-line { color: var(--muted); font-size: 0.8rem; margin-top: 3px; }
  .raw-list { margin: 0; padding-left: 16px; font-size: 0.82rem; }
  .raw-list li { margin: 2px 0; }
  .raw-list a { color: var(--green-deep); }
  table.aff-detail td { vertical-align: top; }
  details summary { cursor: pointer; color: var(--green-deep); font-size: 0.85rem; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); gap: 10px; margin-bottom: 20px; }
  .tile { background: #fff; border: 1px solid var(--rule); border-left: 4px solid var(--tile-color, var(--green)); padding: 7px 11px; }
  .tile b { display: block; font-size: 1.4rem; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1.25; }
  .tile span { color: var(--muted); font-size: 0.82rem; }
  .charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr)); gap: 22px 32px; }
  .chart-box { min-width: 0; }
  .chart-box.wide { grid-column: 1 / -1; }
  .chart-box h3 { font-size: 0.95rem; font-weight: 600; margin: 0 0 6px; }
  .chart-box .note { color: var(--muted); font-size: 0.82rem; margin: 4px 0 0; }
  svg.chart { width: 100%; height: auto; display: block; font-family: var(--sans); overflow: visible; }
  svg.chart .grid { stroke: var(--rule); stroke-width: 1; }
  svg.chart .axis { stroke: var(--ink); stroke-width: 1; }
  svg.chart .tick { fill: var(--muted); font-size: 11px; }
  svg.chart .hit { fill: transparent; }
  svg.chart .clickable { cursor: pointer; }
  svg.chart .clickable:hover .hit { fill: var(--ice); }
  svg.chart.has-sel .ybar:not(.sel) .seg { opacity: 0.28; }
  svg.chart .hlabel { fill: var(--ink); font-size: 12px; }
  svg.chart .clickable .hlabel { fill: var(--green-deep); }
  svg.chart .hvalue { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
  svg.chart .donut-total { fill: var(--ink); font-size: 26px; font-weight: 600; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 0.84rem; color: var(--muted); margin-top: 6px; }
  .legend i, .donut-legend i { display: inline-block; width: 11px; height: 11px; margin-right: 5px; vertical-align: -1px; }
  .donut-wrap { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
  .donut-wrap svg { width: 170px; flex: 0 0 170px; }
  .donut-legend { list-style: none; padding: 0; margin: 0; font-size: 0.9rem; }
  .donut-legend li { margin: 5px 0; }

  /* controls */
  .controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 12px; margin-bottom: 10px; }
  .controls input[type="search"] { flex: 1 1 260px; }
  .controls input, .controls select, .button {
    font: inherit; padding: 7px 10px; border: 1px solid var(--rule); border-radius: 4px; background: #fff; color: var(--ink);
  }
  .controls input[type="number"] { width: 5.5em; }
  .controls label { color: var(--muted); font-size: 0.9rem; display: inline-flex; align-items: center; gap: 6px; }
  .button { cursor: pointer; }
  .button:hover { border-color: var(--green); }
  .controls :focus-visible, .sort-button:focus-visible, a:focus-visible, .name-button:focus-visible, .button:focus-visible {
    outline: 2px solid var(--green); outline-offset: 2px;
  }
  .count { color: var(--muted); margin-left: auto; font-variant-numeric: tabular-nums; }
  .chip { display: inline-flex; align-items: center; gap: 6px; background: var(--ice); border-radius: 999px; padding: 3px 4px 3px 11px; font-size: 0.88rem; }
  .chip button { font: inherit; border: 0; background: transparent; cursor: pointer; padding: 0 6px; color: var(--muted); }
  .definitions { font-size: 0.84rem; color: var(--muted); margin: 0 0 12px; }

  /* tables */
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; min-width: 900px; }
  thead th { position: sticky; top: 0; background: var(--paper); border-bottom: 2px solid var(--ink); text-align: left; padding: 0; font-weight: 600; z-index: 1; vertical-align: bottom; }
  .sort-button { font: inherit; font-weight: 600; color: var(--ink); background: none; border: 0; padding: 9px 8px; width: 100%; text-align: left; cursor: pointer; }
  .sort-button.static { cursor: default; display: block; }
  th.num .sort-button { text-align: right; }
  .sort-button::after { content: ""; display: inline-block; width: 1em; margin-left: 3px; color: var(--green-deep); }
  th[aria-sort="ascending"] .sort-button::after { content: "\25B2"; font-size: 0.7em; }
  th[aria-sort="descending"] .sort-button::after { content: "\25BC"; font-size: 0.7em; }
  tbody td { border-bottom: 1px solid var(--rule); padding: 8px 8px; vertical-align: top; }
  tbody tr:hover td { background: var(--ice); }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.col-rank { color: var(--muted); width: 3em; }
  td.col-muted { color: var(--muted); font-size: 0.92rem; }
  td.col-title { font-family: var(--serif); font-size: 1rem; line-height: 1.45; }
  td.col-doi { font-size: 0.86rem; word-break: break-all; }
  td.col-doi a, a.plain-link { color: var(--green-deep); text-decoration: underline; text-decoration-color: var(--rule); text-underline-offset: 2px; }
  td.col-doi a:hover, a.plain-link:hover { text-decoration-color: var(--green-deep); }
  .name-button { font: inherit; background: none; border: 0; padding: 0; color: var(--green-deep); cursor: pointer; text-align: left; text-decoration: underline; text-decoration-color: var(--rule); text-underline-offset: 2px; }
  .name-button:hover { text-decoration-color: var(--green-deep); }
  .role { display: inline-block; font-size: 0.76rem; padding: 1px 7px; border-radius: 3px; white-space: nowrap; }
  .muted { color: var(--muted); }
  sub, sup { font-size: 0.72em; line-height: 0; }
  .empty-state { padding: 30px 8px; color: var(--muted); }
  footer { margin-top: 34px; color: var(--muted); font-size: 0.85rem; }

  @media (max-width: 720px) {
    .page { padding: 20px 14px 48px; }
    h1 { font-size: 1.6rem; }
    .charts { grid-template-columns: 1fr; }
    .count { margin-left: 0; width: 100%; }
  }
  @media print {
    .controls, nav.pages { display: none; }
    thead th { position: static; }
  }
'''

# --- JavaScript shared by the three pages: data, aggregation and SVG charts
SHARED_JS = r'''
const D = __DATA_JSON__;
D.papers.forEach(function (p) { p.cs = new Set(p.c); });

// ---------- labels and colours of the roles
const role_labels = ["First + corresponding", "First author only", "Corresponding, not first", "Co-author"];
const role_colors = ["#00705F", "#3DB5A3", "#56679A", "#BDD4D8"];
const aff_role_labels = ["First author from here", "Corresponding from here, not first", "Other authors from here"];
const aff_role_colors = ["#00705F", "#56679A", "#BDD4D8"];

const type_list = Array.from(new Set(D.papers.map(p => p.ty))).sort();
const years_known = D.papers.map(p => p.y).filter(y => y !== null);
const year_min = years_known.length ? Math.min.apply(null, years_known) : null;
const year_max = years_known.length ? Math.max.apply(null, years_known) : null;
const author_by_key = new Map(D.authors.map((a, i) => [a.k, i]));
const aff_by_key = new Map(D.affs.map((f, i) => [f.k, i]));

// ---------- small helpers
function el(id) { return document.getElementById(id); }
function esc(value) {
  return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function sum(values) { return values.reduce((s, v) => s + v, 0); }
function trunc(text, n) { text = String(text); return text.length > n ? text.slice(0, n - 1) + "\u2026" : text; }
function years_text(y0, y1) { if (y0 === null || y0 === undefined) return ""; return y0 === y1 ? String(y0) : y0 + " to " + y1; }
function pct(a, b) { return b ? Math.round(100 * a / b) + "%" : "0%"; }
function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

function tally(map, key, role, n_roles, year) {
  let e = map.get(key);
  if (!e) { e = { n: 0, r: new Array(n_roles).fill(0), y0: null, y1: null }; map.set(key, e); }
  e.n++; e.r[role]++;
  if (year !== null) {
    if (e.y0 === null || year < e.y0) e.y0 = year;
    if (e.y1 === null || year > e.y1) e.y1 = year;
  }
  return e;
}
function sorted_entries(map) {
  return Array.from(map.entries()).sort((a, b) => b[1].n - a[1].n || String(a[0]).localeCompare(String(b[0])));
}
function add_year(s, y) {
  if (y === null) return;
  if (s.y0 === null || y < s.y0) s.y0 = y;
  if (s.y1 === null || y > s.y1) s.y1 = y;
}

// ---------- roles
// author on a paper: 0 first + corresponding, 1 first only, 2 corresponding not first, 3 co-author
function role_of(p, pos, ai) {
  const first = pos === 0, corr = p.cs.has(ai);
  return first && corr ? 0 : first ? 1 : corr ? 2 : 3;
}
// affiliation on a paper: 0 first author from here, 1 corresponding from here, 2 other
function aff_role_of(p, f) {
  if ((p.aa[0] || []).includes(f)) return 0;
  for (const ci of p.c) {
    const pos = p.a.indexOf(ci);
    if (pos >= 0 && (p.aa[pos] || []).includes(f)) return 1;
  }
  return 2;
}

// ---------- filters
function filter_papers(state, ignore_year) {
  return D.papers.filter(function (p) {
    if (state.type && p.ty !== state.type) return false;
    if (!ignore_year) {
      if (state.y0 !== null && (p.y === null || p.y < state.y0)) return false;
      if (state.y1 !== null && (p.y === null || p.y > state.y1)) return false;
    }
    return true;
  });
}
function query_match(text, query) {
  if (!query) return true;
  return query.split(/\s+/).every(term => text.includes(term));
}
function filter_text(state) {
  const parts = [];
  if (state.type) parts.push(state.type);
  if (state.y0 !== null || state.y1 !== null) parts.push(years_text(state.y0 !== null ? state.y0 : year_min, state.y1 !== null ? state.y1 : year_max));
  return parts.length ? "Filtered: " + parts.join(", ") : "All publication types and years";
}

// ---------- aggregation
function aggregate_authors(pool) {
  const stats = new Map();
  pool.forEach(function (p) {
    p.a.forEach(function (ai, pos) {
      let s = stats.get(ai);
      if (!s) { s = { id: ai, n: 0, r: [0, 0, 0, 0], y0: null, y1: null, affs: new Map(), jrs: new Map(), tys: new Map() }; stats.set(ai, s); }
      const role = role_of(p, pos, ai);
      s.n++; s.r[role]++; add_year(s, p.y);
      (p.aa[pos] || []).forEach(f => tally(s.affs, f, role, 4, p.y));
      if (p.j >= 0) tally(s.jrs, p.j, role, 4, p.y);
      tally(s.tys, p.ty, role, 4, p.y);
    });
  });
  return stats;
}

function aggregate_affs(pool) {
  const stats = new Map();
  pool.forEach(function (p) {
    p.pa.forEach(function (f) {
      let s = stats.get(f);
      if (!s) { s = { id: f, n: 0, r: [0, 0, 0], y0: null, y1: null, authors: new Map(), jrs: new Map(), tys: new Map(), partners: new Map() }; stats.set(f, s); }
      const role = aff_role_of(p, f);
      s.n++; s.r[role]++; add_year(s, p.y);
      p.a.forEach(function (ai, pos) { if ((p.aa[pos] || []).includes(f)) tally(s.authors, ai, role_of(p, pos, ai), 4, p.y); });
      if (p.j >= 0) tally(s.jrs, p.j, role, 3, p.y);
      tally(s.tys, p.ty, role, 3, p.y);
      p.pa.forEach(g => { if (g !== f) tally(s.partners, g, role, 3, p.y); });
    });
  });
  return stats;
}

function aggregate_countries(pool) {
  const stats = new Map();
  pool.forEach(function (p) {
    const first_countries = new Set((p.aa[0] || []).map(f => D.affs[f].co).filter(Boolean));
    const countries = new Map();
    p.pa.forEach(function (f) {
      const c = D.affs[f].co;
      if (!c) return;
      if (!countries.has(c)) countries.set(c, new Set());
      countries.get(c).add(f);
    });
    countries.forEach(function (affs, c) {
      let s = stats.get(c);
      if (!s) { s = { id: c, n: 0, first: 0, y0: null, y1: null, affs: new Set(), authors: new Set() }; stats.set(c, s); }
      s.n++; if (first_countries.has(c)) s.first++; add_year(s, p.y);
      affs.forEach(f => s.affs.add(f));
      p.a.forEach(function (ai, pos) { if ((p.aa[pos] || []).some(f => D.affs[f].co === c)) s.authors.add(ai); });
    });
  });
  return stats;
}

// ---------- charts
function nice_step(peak) {
  const raw = peak / 4;
  if (raw <= 1) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 5, 10]) { if (m * mag >= raw) return m * mag; }
  return 10 * mag;
}

function legend_html(labels, colors) {
  return '<div class="legend">' + labels.map((label, k) => '<span><i style="background:' + colors[k] + '"></i>' + esc(label) + '</span>').join("") + '</div>';
}

// series: [{name, color, values: {year: count}}], stacked bars for every year
function chart_years(target, series, opts) {
  opts = opts || {};
  if (year_min === null) { target.innerHTML = '<p class="muted">No years in the data.</p>'; return; }
  const W = 1000, H = opts.height || 190, L = 42, R = 10, T = 12, B = 26;
  const n = year_max - year_min + 1, bw = (W - L - R) / n;
  const totals = [];
  for (let y = year_min; y <= year_max; y++) totals.push(series.reduce((s, se) => s + (se.values[y] || 0), 0));
  const peak = Math.max(1, Math.max.apply(null, totals));
  const step = nice_step(peak), top = Math.ceil(peak / step) * step;
  const sy = v => T + (H - T - B) * (1 - v / top);
  const has_sel = opts.y0 !== null && opts.y0 !== undefined;
  let s = '<svg viewBox="0 0 ' + W + ' ' + H + '" class="chart' + (has_sel ? ' has-sel' : '') + '" role="img" aria-label="' + esc(opts.label || "Papers per year") + '">';
  for (let v = 0; v <= top; v += step) {
    s += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + sy(v).toFixed(1) + '" y2="' + sy(v).toFixed(1) + '"/>';
    s += '<text class="tick" x="' + (L - 7) + '" y="' + (sy(v) + 4).toFixed(1) + '" text-anchor="end">' + v + '</text>';
  }
  const label_step = n > 30 ? 10 : 5;
  for (let i = 0; i < n; i++) {
    const y = year_min + i, x = L + i * bw;
    const sel = has_sel && y >= opts.y0 && y <= (opts.y1 === null ? year_max : opts.y1);
    const detail = series.length > 1 ? " (" + series.map(se => se.name + ": " + (se.values[y] || 0)).join(", ") + ")" : "";
    s += '<g class="ybar' + (opts.on_click ? ' clickable' : '') + (sel ? ' sel' : '') + '" data-year="' + y + '"><title>' + y + ": " + totals[i] + esc(detail) + '</title>';
    s += '<rect class="hit" x="' + x.toFixed(2) + '" y="' + T + '" width="' + bw.toFixed(2) + '" height="' + (H - T - B) + '"/>';
    let acc = 0;
    series.forEach(function (se) {
      const v = se.values[y] || 0;
      if (!v) return;
      const y_top = sy(acc + v), y_bottom = sy(acc);
      s += '<rect class="seg" x="' + (x + bw * 0.14).toFixed(2) + '" y="' + y_top.toFixed(2) + '" width="' + (bw * 0.72).toFixed(2) + '" height="' + Math.max(0.6, y_bottom - y_top).toFixed(2) + '" fill="' + se.color + '"/>';
      acc += v;
    });
    s += '</g>';
    if (y === year_min || y === year_max || (y % label_step === 0 && y - year_min > 2 && year_max - y > 2)) {
      s += '<text class="tick" x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle">' + y + '</text>';
    }
  }
  s += '<line class="axis" x1="' + L + '" x2="' + (W - R) + '" y1="' + sy(0) + '" y2="' + sy(0) + '"/></svg>';
  if (series.length > 1) s += legend_html(series.map(se => se.name), series.map(se => se.color));
  target.innerHTML = s;
  if (opts.on_click) target.querySelectorAll(".ybar").forEach(g => g.addEventListener("click", () => opts.on_click(Number(g.dataset.year))));
}

// items: [{label, values: [n per role], ...}], horizontal stacked bars
function chart_hbar(target, items, opts) {
  opts = opts || {};
  if (!items.length) { target.innerHTML = '<p class="muted">Nothing to show for these filters.</p>'; return; }
  const colors = opts.colors || ["#009682"];
  // the drawing width follows the box, so text keeps its size in wide and narrow boxes
  const W = Math.max(360, Math.round(target.clientWidth || 640)), row_h = 22, T = 2, R = 46;
  const LW = Math.min(opts.label_width || 240, Math.round(W * 0.45));
  const H = T + items.length * row_h + 2;
  const peak = Math.max(1, Math.max.apply(null, items.map(it => sum(it.values))));
  const scale = (W - LW - R) / peak;
  const max_chars = Math.floor(LW / 6.7);
  let s = '<svg viewBox="0 0 ' + W + ' ' + H + '" class="chart">';
  items.forEach(function (it, i) {
    const y = T + i * row_h, total = sum(it.values);
    const detail = opts.names && it.values.length > 1 ? " (" + it.values.map((v, k) => opts.names[k] + ": " + v).join(", ") + ")" : "";
    s += '<g class="hrow' + (opts.on_click ? ' clickable' : '') + '" data-index="' + i + '"><title>' + esc(it.label) + ": " + total + esc(detail) + '</title>';
    s += '<rect class="hit" x="0" y="' + y + '" width="' + W + '" height="' + row_h + '"/>';
    s += '<text class="hlabel" x="' + (LW - 8) + '" y="' + (y + row_h / 2 + 4) + '" text-anchor="end">' + esc(trunc(it.label, max_chars)) + '</text>';
    let acc = 0;
    it.values.forEach(function (v, k) {
      if (!v) return;
      s += '<rect x="' + (LW + acc * scale).toFixed(2) + '" y="' + (y + 4) + '" width="' + Math.max(0.8, v * scale).toFixed(2) + '" height="' + (row_h - 8) + '" fill="' + colors[k % colors.length] + '"/>';
      acc += v;
    });
    s += '<text class="hvalue" x="' + (LW + acc * scale + 5).toFixed(1) + '" y="' + (y + row_h / 2 + 4) + '">' + total + '</text></g>';
  });
  target.innerHTML = s + '</svg>' + (opts.names && opts.legend !== false ? legend_html(opts.names, colors) : "");
  if (opts.on_click) target.querySelectorAll(".hrow").forEach(g => g.addEventListener("click", () => opts.on_click(items[Number(g.dataset.index)])));
}

function chart_donut(target, labels, colors, values, unit) {
  const total = sum(values);
  if (!total) { target.innerHTML = '<p class="muted">Nothing to show for these filters.</p>'; return; }
  const r = 58, C = 2 * Math.PI * r;
  let s = '<div class="donut-wrap"><svg viewBox="0 0 160 160" class="chart" role="img" aria-label="Role split">';
  s += '<circle cx="80" cy="80" r="' + r + '" fill="none" stroke="var(--rule)" stroke-width="26"/>';
  let acc = 0;
  values.forEach(function (v, k) {
    if (!v) return;
    const len = C * v / total;
    s += '<circle cx="80" cy="80" r="' + r + '" fill="none" stroke="' + colors[k] + '" stroke-width="26" stroke-dasharray="' + len.toFixed(3) + ' ' + (C - len).toFixed(3) + '" stroke-dashoffset="' + (-acc).toFixed(3) + '" transform="rotate(-90 80 80)"><title>' + esc(labels[k]) + ': ' + v + '</title></circle>';
    acc += len;
  });
  s += '<text x="80" y="86" text-anchor="middle" class="donut-total">' + total + '</text>';
  s += '<text x="80" y="104" text-anchor="middle" class="tick">' + esc(unit || "papers") + '</text></svg><ul class="donut-legend">';
  values.forEach(function (v, k) {
    s += '<li><i style="background:' + colors[k] + '"></i>' + esc(labels[k]) + ': <b>' + v + '</b> <span class="muted">(' + pct(v, total) + ')</span></li>';
  });
  target.innerHTML = s + '</ul></div>';
}

function tiles_html(list) {
  // list: [[value, label, colour]]
  return '<div class="tiles">' + list.map(t => '<div class="tile"' + (t[2] ? ' style="--tile-color:' + t[2] + '"' : '') + '><b>' + esc(t[0]) + '</b><span>' + esc(t[1]) + '</span></div>').join("") + '</div>';
}

// ---------- links between the pages
function author_button(ai) { return '<button type="button" class="name-button" data-author="' + ai + '">' + esc(D.authors[ai].n) + '</button>'; }
function aff_button(f) { return '<button type="button" class="name-button" data-aff="' + f + '">' + esc(D.affs[f].n) + '</button>'; }
function author_page_link(ai) { if (!D.pages.authors) return esc(D.authors[ai].n); return '<a class="plain-link" href="' + D.pages.authors + '#author=' + encodeURIComponent(D.authors[ai].k) + '">' + esc(D.authors[ai].n) + '</a>'; }
function aff_page_link(f) { if (!D.pages.affs) return esc(D.affs[f].n); return '<a class="plain-link" href="' + D.pages.affs + '#aff=' + encodeURIComponent(D.affs[f].k) + '">' + esc(D.affs[f].n) + '</a>'; }
function go_author_page(ai) { if (D.pages.authors) window.location.href = D.pages.authors + "#author=" + encodeURIComponent(D.authors[ai].k); }
function go_aff_page(f) { if (D.pages.affs) window.location.href = D.pages.affs + "#aff=" + encodeURIComponent(D.affs[f].k); }

function read_hash(name) { return new URLSearchParams(location.hash.slice(1)).get(name); }
function write_hash(name, value) {
  const url = value === null ? location.pathname + location.search : "#" + name + "=" + encodeURIComponent(value);
  history.replaceState(null, "", url);
}

// ---------- sortable table; the # column is the rank in the current order
function render_table(table_id, columns, rows, sort_pair, limit, on_sort) {
  const key = sort_pair[0], dir = sort_pair[1];
  const column = columns.find(c => c.key === key) || columns[0];
  rows.sort(function (a, b) {
    const va = column.get(a), vb = column.get(b);
    const am = va === null || va === undefined || va === "", bm = vb === null || vb === undefined || vb === "";
    if (am !== bm) return am ? 1 : -1;
    let r = 0;
    if (!am) r = column.num ? va - vb : String(va).localeCompare(String(vb), undefined, { sensitivity: "base", numeric: true });
    if (dir === "desc") r = -r;
    if (r === 0) r = (b.papers || 0) - (a.papers || 0);
    if (r === 0) r = String(a.sort_name || "").localeCompare(String(b.sort_name || ""));
    return r;
  });
  const table = el(table_id);
  let head = '<tr><th scope="col"><span class="sort-button static">#</span></th>';
  columns.forEach(function (c) {
    const aria = c.key === key ? ' aria-sort="' + (dir === "asc" ? "ascending" : "descending") + '"' : "";
    head += '<th scope="col" data-key="' + c.key + '" class="' + (c.num ? "num" : "") + '"' + aria + '><button class="sort-button" type="button"' + (c.tip ? ' title="' + esc(c.tip) + '"' : "") + '>' + esc(c.label) + '</button></th>';
  });
  const shown = limit ? rows.slice(0, limit) : rows;
  const parts = [];
  shown.forEach(function (r, i) {
    let row = '<tr><td class="col-rank num">' + (i + 1) + '</td>';
    columns.forEach(function (c) { row += '<td class="' + (c.num ? "num " : "") + (c.cls || "") + '">' + (c.html ? c.html(r) : esc(c.get(r))) + '</td>'; });
    parts.push(row + '</tr>');
  });
  table.querySelector("thead").innerHTML = head + '</tr>';
  table.querySelector("tbody").innerHTML = parts.join("");
  table.querySelectorAll("th[data-key] .sort-button").forEach(function (button) {
    button.addEventListener("click", function () {
      const k = button.parentElement.dataset.key;
      const col = columns.find(c => c.key === k);
      on_sort(k === key ? [k, dir === "asc" ? "desc" : "asc"] : [k, col.num ? "desc" : "asc"]);
    });
  });
  return shown.length;
}

// ---------- list of papers inside a profile
function papers_table_html(papers, role_badge, extra_label, extra_html) {
  const rows = papers.slice().sort((a, b) => (b.y || 0) - (a.y || 0) || a.tp.localeCompare(b.tp));
  let s = '<div class="table-wrap"><table class="papers"><thead><tr><th><span class="sort-button static">Year</span></th><th><span class="sort-button static">Role</span></th>';
  if (extra_label) s += '<th><span class="sort-button static">' + esc(extra_label) + '</span></th>';
  s += '<th><span class="sort-button static">Title</span></th><th><span class="sort-button static">Journal</span></th><th><span class="sort-button static">Type</span></th><th><span class="sort-button static">DOI</span></th></tr></thead><tbody>';
  rows.forEach(function (p) {
    const badge = role_badge(p);
    const link = p.doi ? "https://doi.org/" + encodeURI(p.doi) : p.url;
    s += '<tr><td class="num">' + (p.y === null ? "" : p.y) + '</td>';
    s += '<td><span class="role" style="background:' + badge[1] + ';color:' + (badge[2] || "#fff") + '">' + esc(badge[0]) + '</span></td>';
    if (extra_label) s += '<td class="col-muted">' + extra_html(p) + '</td>';
    s += '<td class="col-title">' + p.th + '</td>';
    s += '<td class="col-muted">' + esc(p.j >= 0 ? D.journals[p.j] : "") + '</td>';
    s += '<td class="col-muted">' + esc(p.ty) + '</td>';
    s += '<td class="col-doi">' + (link ? '<a href="' + esc(link) + '" target="_blank" rel="noopener noreferrer">' + esc(p.doi || "Link") + '</a>' : '<span class="muted">none</span>') + '</td></tr>';
  });
  return s + '</tbody></table></div>';
}

// ---------- filter controls shared by the pages
function setup_year_and_type(state, render) {
  const type_select = el("type_select");
  type_list.forEach(function (label) {
    const option = document.createElement("option");
    option.value = label; option.textContent = label;
    type_select.appendChild(option);
  });
  [["y0_select", "From year"], ["y1_select", "To year"]].forEach(function (pair) {
    const select = el(pair[0]);
    select.innerHTML = '<option value="">' + pair[1] + '</option>';
    if (year_min === null) return;
    for (let y = year_min; y <= year_max; y++) select.innerHTML += '<option value="' + y + '">' + y + '</option>';
  });
  type_select.addEventListener("change", function () { state.type = type_select.value; render(); });
  el("y0_select").addEventListener("change", function () { state.y0 = this.value ? Number(this.value) : null; render(); });
  el("y1_select").addEventListener("change", function () { state.y1 = this.value ? Number(this.value) : null; render(); });
  let timer = null;
  el("search_box").addEventListener("input", function (event) {
    clearTimeout(timer);
    timer = setTimeout(function () { state.query = event.target.value.trim().toLowerCase(); render(); }, 140);
  });
  el("min_input").addEventListener("input", function () { state.min = Math.max(1, Number(this.value) || 1); render(); });
}
function sync_year_and_type(state) {
  el("type_select").value = state.type;
  el("y0_select").value = state.y0 === null ? "" : String(state.y0);
  el("y1_select").value = state.y1 === null ? "" : String(state.y1);
}
function toggle_year(state, y) {
  if (state.y0 === y && state.y1 === y) { state.y0 = null; state.y1 = null; }
  else { state.y0 = y; state.y1 = y; }
}
'''


# ================================================== #
# HTML AUTHORS PAGE
# ================================================== #

AUTHORS_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<style>__SHARED_CSS__</style>
</head>
<body>
<div class="page">
  __NAV__
  <header>
    <h1>__PAGE_TITLE__</h1>
    <p class="summary">__SUMMARY_TEXT__</p>
  </header>

  <section class="panel" id="top_panel" aria-live="polite"></section>

  <div class="controls">
    <select id="view_select" aria-label="Ranking">
      <option value="authors">Rank authors</option>
      <option value="author_aff">Rank author and affiliation</option>
      <option value="author_journal">Rank author and journal</option>
      <option value="author_type">Rank author and publication type</option>
    </select>
    <input type="search" id="search_box" placeholder="Search authors, affiliations, journals" aria-label="Search">
    <select id="type_select" aria-label="Publication type"><option value="">All types</option></select>
    <select id="y0_select" aria-label="From year"></select>
    <select id="y1_select" aria-label="To year"></select>
    <label>Min. papers <input type="number" id="min_input" min="1" value="1"></label>
    <select id="limit_select" aria-label="Rows shown">
      <option value="100">Top 100</option>
      <option value="250" selected>Top 250</option>
      <option value="1000">Top 1000</option>
      <option value="0">All rows</option>
    </select>
    <button type="button" class="button" id="reset_button">Clear filters</button>
    <span class="count" id="count_text" aria-live="polite"></span>
  </div>
  <p class="definitions">Roles per paper, they add up to Papers: <b>First + corr.</b> first and corresponding author; <b>First only</b> first author, not corresponding; <b>Corr., not first</b> corresponding but not first author; <b>Co-author</b> neither. <b>First author</b> = First + corr. and First only. Corresponding authors are known for __CORR_COVERAGE__ of the papers. Select a name to see the author profile, select a column title to rank by it.</p>

  <div class="table-wrap">
    <table id="main_table"><thead></thead><tbody></tbody></table>
    <div class="empty-state" id="empty_state" hidden>No rows match these filters.</div>
  </div>

  <footer>Generated from __SOURCE_NAME__ on __BUILD_DATE__. Authors are matched by surname and first initial.</footer>
</div>

<script>
__SHARED_JS__

// ================= authors page =================
const state = {
  view: "authors", query: "", type: "", y0: null, y1: null, min: 1, limit: 250, sel: null,
  sort: { authors: ["papers", "desc"], author_aff: ["papers", "desc"], author_journal: ["papers", "desc"], author_type: ["papers", "desc"] }
};

// ---------- table columns
const role_columns = [
  { key: "papers", label: "Papers", num: true, get: r => r.papers, tip: "Papers in the current filters" },
  { key: "first_all", label: "First author", num: true, get: r => r.r[0] + r.r[1], tip: "First author, corresponding or not" },
  { key: "first_corr", label: "First + corr.", num: true, get: r => r.r[0], tip: role_labels[0] },
  { key: "first_only", label: "First only", num: true, get: r => r.r[1], tip: role_labels[1] },
  { key: "corr_not_first", label: "Corr., not first", num: true, get: r => r.r[2], tip: role_labels[2] },
  { key: "co", label: "Co-author", num: true, get: r => r.r[3], tip: "Neither first nor corresponding author" },
];
const author_column = { key: "author", label: "Author", get: r => r.name, html: r => author_button(r.ai) };
const years_column = { key: "years", label: "Years", get: r => r.y0, html: r => esc(years_text(r.y0, r.y1)), cls: "col-muted" };

function columns_for(view) {
  if (view === "authors") return [author_column].concat(role_columns, [
    { key: "aff", label: "Main affiliation", get: r => r.aff >= 0 ? D.affs[r.aff].n : "", html: r => r.aff >= 0 ? aff_page_link(r.aff) : "", cls: "col-muted" },
    { key: "journal", label: "Top journal", get: r => r.jr >= 0 ? D.journals[r.jr] : "", cls: "col-muted" },
    years_column]);
  if (view === "author_aff") return [author_column,
    { key: "aff", label: "Affiliation", get: r => D.affs[r.aff].n, html: r => aff_page_link(r.aff) },
    { key: "country", label: "Country", get: r => D.affs[r.aff].co, cls: "col-muted" }].concat(role_columns, [years_column]);
  if (view === "author_journal") return [author_column,
    { key: "journal", label: "Journal", get: r => D.journals[r.jr] }].concat(role_columns, [years_column]);
  return [author_column, { key: "papers", label: "Papers", num: true, get: r => r.papers }].concat(
    type_list.map(t => ({ key: "type_" + t, label: t, num: true, get: r => r.tys.get(t) ? r.tys.get(t).n : 0 })));
}

// ---------- rows for the selected ranking
function build_rows(stats) {
  const rows = [];
  stats.forEach(function (s) {
    const author = D.authors[s.id];
    const base_text = (author.n + " " + author.v.join(" ")).toLowerCase();
    if (state.view === "authors") {
      const top_aff = sorted_entries(s.affs)[0], top_jr = sorted_entries(s.jrs)[0];
      const aff_text = Array.from(s.affs.keys()).map(f => D.affs[f].n + " " + D.affs[f].co).join(" ");
      rows.push({ ai: s.id, name: author.n, sort_name: author.n, papers: s.n, r: s.r, y0: s.y0, y1: s.y1,
        aff: top_aff ? top_aff[0] : -1, jr: top_jr ? top_jr[0] : -1,
        search: base_text + " " + (aff_text + " " + (top_jr ? D.journals[top_jr[0]] : "")).toLowerCase() });
    } else if (state.view === "author_aff") {
      s.affs.forEach(function (e, f) {
        rows.push({ ai: s.id, name: author.n, sort_name: author.n, aff: f, papers: e.n, r: e.r, y0: e.y0, y1: e.y1,
          search: base_text + " " + (D.affs[f].n + " " + D.affs[f].co).toLowerCase() });
      });
    } else if (state.view === "author_journal") {
      s.jrs.forEach(function (e, j) {
        rows.push({ ai: s.id, name: author.n, sort_name: author.n, jr: j, papers: e.n, r: e.r, y0: e.y0, y1: e.y1,
          search: base_text + " " + D.journals[j].toLowerCase() });
      });
    } else {
      rows.push({ ai: s.id, name: author.n, sort_name: author.n, papers: s.n, tys: s.tys, search: base_text });
    }
  });
  return rows.filter(r => r.papers >= state.min && query_match(r.search, state.query));
}

// ---------- overview of all authors
function render_overview(pool, pool_all_years, stats) {
  const panel = el("top_panel");
  const affs = new Set(), journals = new Set();
  pool.forEach(p => { p.pa.forEach(f => affs.add(f)); if (p.j >= 0) journals.add(p.j); });
  const role_totals = [0, 0, 0, 0];
  stats.forEach(s => s.r.forEach((v, k) => role_totals[k] += v));
  panel.innerHTML = '<div class="panel-head"><h2>All authors</h2><span class="sub">' + esc(filter_text(state)) + '</span></div>' +
    tiles_html([[pool.length, "papers"], [stats.size, "different authors"], [affs.size, "different affiliations"], [journals.size, "different journals"],
      [pct(pool.filter(p => p.ck).length, pool.length), "papers with known corresponding author", "#56679A"]]) +
    '<div class="charts">' +
    '<div class="chart-box wide"><h3>Papers per year</h3><div id="c_years"></div><p class="note">Select a bar to show that year only, select it again to show all years.</p></div>' +
    '<div class="chart-box"><h3>Top 15 authors by papers</h3><div id="c_top"></div></div>' +
    '<div class="chart-box"><h3>Top 15 first authors</h3><div id="c_first"></div></div>' +
    '<div class="chart-box"><h3>Top 15 corresponding authors</h3><div id="c_corr"></div></div>' +
    '<div class="chart-box"><h3>All author positions by role</h3><div id="c_roles"></div></div>' +
    '</div>';
  const per_year = {};
  pool_all_years.forEach(p => { if (p.y !== null) per_year[p.y] = (per_year[p.y] || 0) + 1; });
  chart_years(el("c_years"), [{ name: "Papers", color: "#009682", values: per_year }],
    { y0: state.y0, y1: state.y1, on_click: y => { toggle_year(state, y); render(); } });
  const all = Array.from(stats.values());
  const select = it => select_author(it.id);
  const top = all.slice().sort((a, b) => b.n - a.n).slice(0, 15);
  chart_hbar(el("c_top"), top.map(s => ({ label: D.authors[s.id].n, id: s.id, values: s.r })), { colors: role_colors, names: role_labels, on_click: select });
  const top_first = all.filter(s => s.r[0] + s.r[1] > 0).sort((a, b) => (b.r[0] + b.r[1]) - (a.r[0] + a.r[1]) || b.n - a.n).slice(0, 15);
  chart_hbar(el("c_first"), top_first.map(s => ({ label: D.authors[s.id].n, id: s.id, values: [s.r[0], s.r[1]] })), { colors: role_colors.slice(0, 2), names: role_labels.slice(0, 2), on_click: select });
  const top_corr = all.filter(s => s.r[0] + s.r[2] > 0).sort((a, b) => (b.r[0] + b.r[2]) - (a.r[0] + a.r[2]) || b.n - a.n).slice(0, 15);
  chart_hbar(el("c_corr"), top_corr.map(s => ({ label: D.authors[s.id].n, id: s.id, values: [s.r[0], s.r[2]] })), { colors: [role_colors[0], role_colors[2]], names: [role_labels[0], role_labels[2]], on_click: select });
  chart_donut(el("c_roles"), role_labels, role_colors, role_totals, "author positions");
}

// ---------- affiliations of one author, with the source text of each
const source_labels = { o: "OpenAlex", w: "Web of Science address" };

function slot_aff_text(p, pos) {
  const ids = p.aa[pos] || [];
  if (!ids.length) return "Affiliation of this author not known for this paper";
  return "Affiliation here: " + esc(ids.map(f => D.affs[f].n).join("; ")) + (source_labels[p.as[pos]] ? " (" + source_labels[p.as[pos]] + ")" : "");
}

function aff_detail_html(ai, papers) {
  const rows = new Map();
  let unknown = 0;
  papers.forEach(function (p) {
    const pos = p.a.indexOf(ai);
    const ids = p.aa[pos] || [];
    if (!ids.length) { unknown++; return; }
    ids.forEach(function (f) {
      let r = rows.get(f);
      if (!r) { r = { f: f, n: 0, y0: null, y1: null, src: {}, raws: new Map(), papers: [], shared: 0 }; rows.set(f, r); }
      r.n++; add_year(r, p.y);
      r.src[p.as[pos]] = (r.src[p.as[pos]] || 0) + 1;
      if (ids.length > 1) r.shared++;
      (p.ar[pos] || []).forEach(pair => { if (pair[0] === f) r.raws.set(pair[1], (r.raws.get(pair[1]) || 0) + 1); });
      r.papers.push(p);
    });
  });
  const list = Array.from(rows.values()).sort((a, b) => b.n - a.n || (a.y0 || 0) - (b.y0 || 0));
  const multi = papers.filter(p => (p.aa[p.a.indexOf(ai)] || []).length > 1).length;
  let s = '<p class="note">One person can have several affiliation names: an institute can be renamed or merged (Forschungszentrum Karlsruhe became KIT in 2009), a paper can list two affiliations for one author, or the author worked somewhere else for a while. ' +
    'Check each row below: the years, the text as written in the source, and the papers.' +
    (multi ? ' On ' + plural(multi, "paper", "papers") + ' this author has more than one affiliation.' : '') +
    (unknown ? ' On ' + plural(unknown, "paper", "papers") + ' the affiliation of this author is not known.' : '') + '</p>';
  if (!list.length) return s;
  s += '<div class="table-wrap"><table class="papers aff-detail"><thead><tr>' +
    ['Affiliation', 'Country', 'Papers', 'Years', 'Found in', 'As written in the source', 'Papers with this affiliation'].map(h => '<th><span class="sort-button static">' + h + '</span></th>').join("") +
    '</tr></thead><tbody>';
  list.forEach(function (r) {
    const aff = D.affs[r.f];
    const found = Object.keys(r.src).map(k => (source_labels[k] || "other") + ": " + r.src[k]).join("<br>");
    const raws = Array.from(r.raws.entries()).sort((a, b) => b[1] - a[1]).slice(0, 6)
      .map(e => '<li>' + esc(D.raws[e[0]]) + ' <span class="muted">(' + e[1] + ')</span></li>').join("");
    const paper_items = r.papers.slice().sort((a, b) => (a.y || 0) - (b.y || 0)).map(function (p) {
      const link = p.doi ? "https://doi.org/" + encodeURI(p.doi) : p.url;
      return '<li>' + (p.y === null ? "" : p.y + ": ") + (link ? '<a href="' + esc(link) + '" target="_blank" rel="noopener noreferrer">' + p.th + '</a>' : p.th) + '</li>';
    }).join("");
    s += '<tr><td>' + aff_page_link(r.f) + (aff.v && aff.v.length ? '<div class="aff-line">Also written as: ' + esc(aff.v.slice(0, 4).join("; ")) + '</div>' : '') + '</td>' +
      '<td class="col-muted">' + esc([aff.ci, aff.co].filter(Boolean).join(", ")) + '</td>' +
      '<td class="num">' + r.n + (r.shared ? '<div class="aff-line">' + r.shared + ' together with another affiliation</div>' : '') + '</td>' +
      '<td class="col-muted">' + esc(years_text(r.y0, r.y1)) + '</td>' +
      '<td class="col-muted">' + found + '</td>' +
      '<td class="col-muted"><ul class="raw-list">' + raws + '</ul></td>' +
      '<td><details><summary>Show ' + plural(r.n, "paper", "papers") + '</summary><ul class="raw-list">' + paper_items + '</ul></details></td></tr>';
  });
  return s + '</tbody></table></div>';
}

// ---------- profile of one author
function render_profile(ai, pool, pool_all_years, stats) {
  const panel = el("top_panel");
  const author = D.authors[ai];
  const s = stats.get(ai) || { n: 0, r: [0, 0, 0, 0], y0: null, y1: null, affs: new Map(), jrs: new Map(), tys: new Map() };
  const papers = pool.filter(p => p.a.includes(ai));
  const per_role_year = [{}, {}, {}, {}];
  pool_all_years.forEach(function (p) {
    const pos = p.a.indexOf(ai);
    if (pos < 0 || p.y === null) return;
    const role = role_of(p, pos, ai);
    per_role_year[role][p.y] = (per_role_year[role][p.y] || 0) + 1;
  });
  const coauthors = new Map();
  papers.forEach(p => p.a.forEach(bj => { if (bj !== ai) tally(coauthors, bj, 0, 1, p.y); }));
  const top_aff = sorted_entries(s.affs)[0];

  panel.innerHTML = '<div class="panel-head"><h2>' + esc(author.n) + '</h2><span class="sub">' +
    esc([top_aff ? "Main affiliation: " + D.affs[top_aff[0]].n : "", s.y0 !== null ? "AIDA papers " + years_text(s.y0, s.y1) : ""].filter(Boolean).join(". ")) +
    '</span><span class="spacer"></span><button type="button" class="button" id="back_button">Back to all authors</button></div>' +
    (author.v.length ? '<p class="also">Also written as: ' + esc(author.v.join("; ")) + '</p>' : '') +
    '<p class="tile-group">Papers by role: the last four tiles add up to the total; papers as first author = first + corresponding and first author only together</p>' +
    tiles_html([[s.n, "papers in total"], [s.r[0] + s.r[1], "papers as first author", role_colors[1]], [s.r[0], "papers as first + corresponding author", role_colors[0]],
      [s.r[1], "papers as first author only", role_colors[1]], [s.r[2], "papers as corresponding, not first author", role_colors[2]], [s.r[3], "papers as co-author", role_colors[3]]]) +
    '<p class="tile-group">Collaboration</p>' +
    tiles_html([[coauthors.size, "different people who wrote papers with this author"],
      [s.affs.size, "different affiliation names on this author\u2019s papers"], [s.jrs.size, "different journals"]]) +
    '<div class="charts">' +
    '<div class="chart-box wide"><h3>Papers per year by role</h3><div id="c_years"></div><p class="note">Select a bar to show that year only.</p></div>' +
    '<div class="chart-box"><h3>Role split</h3><div id="c_roles"></div></div>' +
    '<div class="chart-box"><h3>Affiliations</h3><div id="c_affs"></div><p class="note">Select a bar to open the affiliation profile.</p></div>' +
    '<div class="chart-box wide"><h3>Affiliations in detail</h3>' + aff_detail_html(ai, papers) + '</div>' +
    '<div class="chart-box"><h3>Journals</h3><div id="c_journals"></div></div>' +
    '<div class="chart-box"><h3>Publication types</h3><div id="c_types"></div></div>' +
    '<div class="chart-box wide"><h3>Most frequent co-authors</h3><div id="c_coauthors"></div><p class="note">Select a bar to open that author.</p></div>' +
    '<div class="chart-box wide"><h3>' + esc(plural(papers.length, "paper", "papers")) + '</h3>' +
    papers_table_html(papers, p => { const role = role_of(p, p.a.indexOf(ai), ai); return [role_labels[role], role_colors[role], role === 3 ? "#1F2B30" : "#fff"]; },
      "Authors and affiliation", p => esc(p.a.slice(0, 5).map(x => D.authors[x].n).join("; ") + (p.a.length > 5 ? " et al." : "")) +
        '<br><span class="aff-line">' + slot_aff_text(p, p.a.indexOf(ai)) + '</span>') + '</div>' +
    '</div>';

  chart_years(el("c_years"), role_labels.map((label, k) => ({ name: label, color: role_colors[k], values: per_role_year[k] })),
    { y0: state.y0, y1: state.y1, on_click: y => { toggle_year(state, y); render(); } });
  chart_donut(el("c_roles"), role_labels, role_colors, s.r);
  const to_items = (map, name_of, n) => sorted_entries(map).slice(0, n).map(e => ({ label: name_of(e[0]), id: e[0], values: e[1].r }));
  chart_hbar(el("c_affs"), to_items(s.affs, f => D.affs[f].n, 12), { colors: role_colors, names: role_labels, legend: false, on_click: it => go_aff_page(it.id) });
  chart_hbar(el("c_journals"), to_items(s.jrs, j => D.journals[j], 12), { colors: role_colors, names: role_labels, legend: false });
  chart_hbar(el("c_types"), to_items(s.tys, t => t, 12), { colors: role_colors, names: role_labels, legend: false });
  chart_hbar(el("c_coauthors"), sorted_entries(coauthors).slice(0, 20).map(e => ({ label: D.authors[e[0]].n, id: e[0], values: [e[1].n] })),
    { colors: ["#009682"], label_width: 260, on_click: it => select_author(it.id) });
  el("back_button").addEventListener("click", () => select_author(null));
}

function select_author(ai) {
  state.sel = ai;
  write_hash("author", ai === null ? null : D.authors[ai].k);
  render();
  el("top_panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------- main render
function render() {
  sync_year_and_type(state);
  const pool = filter_papers(state, false);
  const pool_all_years = filter_papers(state, true);
  const stats = aggregate_authors(pool);
  if (state.sel !== null) render_profile(state.sel, pool, pool_all_years, stats);
  else render_overview(pool, pool_all_years, stats);

  const rows = build_rows(stats);
  const shown = render_table("main_table", columns_for(state.view), rows, state.sort[state.view], state.limit,
    function (pair) { state.sort[state.view] = pair; render(); });
  el("empty_state").hidden = rows.length > 0;
  el("count_text").textContent = "Showing " + shown + " of " + plural(rows.length, "row", "rows");
}

// ---------- events
setup_year_and_type(state, render);
el("view_select").addEventListener("change", function () { state.view = this.value; render(); });
el("limit_select").addEventListener("change", function () { state.limit = Number(this.value); render(); });
el("main_table").addEventListener("click", function (event) {
  const target = event.target.closest("[data-author]");
  if (target) select_author(Number(target.dataset.author));
});
el("reset_button").addEventListener("click", function () {
  Object.assign(state, { query: "", type: "", y0: null, y1: null, min: 1 });
  el("search_box").value = ""; el("min_input").value = 1;
  render();
});
function apply_hash() {
  let key = read_hash("author");
  // a one author review packet carries D.focus and opens on that profile
  if (key === null && D.focus && author_by_key.has(D.focus)) key = D.focus;
  state.sel = key !== null && author_by_key.has(key) ? author_by_key.get(key) : null;
  render();
}
window.addEventListener("hashchange", apply_hash);
apply_hash();
</script>
</body>
</html>
'''


# ================================================== #
# HTML AFFILIATIONS PAGE
# ================================================== #

AFFILIATIONS_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<style>__SHARED_CSS__</style>
</head>
<body>
<div class="page">
  __NAV__
  <header>
    <h1>__PAGE_TITLE__</h1>
    <p class="summary">__SUMMARY_TEXT__</p>
  </header>

  <section class="panel" id="top_panel" aria-live="polite"></section>

  <div class="controls">
    <select id="view_select" aria-label="Ranking">
      <option value="affs">Rank affiliations</option>
      <option value="aff_author">Rank affiliation and author</option>
      <option value="aff_journal">Rank affiliation and journal</option>
      <option value="aff_type">Rank affiliation and publication type</option>
      <option value="countries">Rank countries</option>
    </select>
    <input type="search" id="search_box" placeholder="Search affiliations, countries, authors, journals" aria-label="Search">
    <select id="type_select" aria-label="Publication type"><option value="">All types</option></select>
    <select id="y0_select" aria-label="From year"></select>
    <select id="y1_select" aria-label="To year"></select>
    <label>Min. papers <input type="number" id="min_input" min="1" value="1"></label>
    <select id="limit_select" aria-label="Rows shown">
      <option value="100">Top 100</option>
      <option value="250" selected>Top 250</option>
      <option value="1000">Top 1000</option>
      <option value="0">All rows</option>
    </select>
    <button type="button" class="button" id="reset_button">Clear filters</button>
    <span id="country_chip"></span>
    <span class="count" id="count_text" aria-live="polite"></span>
  </div>
  <p class="definitions">A paper counts once for every affiliation of its authors. <b>First author</b> papers have their first author at this affiliation; <b>Corr., not first</b> papers have a corresponding author here but the first author elsewhere; <b>Other</b> papers have co-authors here only. Author level affiliations are known for __AFF_COVERAGE__ of the author positions. Select a name to open its profile, a country to list its affiliations.</p>

  <div class="table-wrap">
    <table id="main_table"><thead></thead><tbody></tbody></table>
    <div class="empty-state" id="empty_state" hidden>No rows match these filters.</div>
  </div>

  <footer>Generated from __SOURCE_NAME__ on __BUILD_DATE__.</footer>
</div>

<script>
__SHARED_JS__

// ================= affiliations page =================
const state = {
  view: "affs", query: "", type: "", y0: null, y1: null, min: 1, limit: 250, sel: null, country: null,
  sort: { affs: ["papers", "desc"], aff_author: ["papers", "desc"], aff_journal: ["papers", "desc"], aff_type: ["papers", "desc"], countries: ["papers", "desc"] }
};

// ---------- table columns
const aff_role_columns = [
  { key: "papers", label: "Papers", num: true, get: r => r.papers, tip: "Papers in the current filters" },
  { key: "first", label: "First author", num: true, get: r => r.r[0], tip: aff_role_labels[0] },
  { key: "corr", label: "Corr., not first", num: true, get: r => r.r[1], tip: aff_role_labels[1] },
  { key: "other", label: "Other", num: true, get: r => r.r[2], tip: aff_role_labels[2] },
];
const aff_column = { key: "aff", label: "Affiliation", get: r => D.affs[r.f].n, html: r => aff_button(r.f) };
const country_column = { key: "country", label: "Country", get: r => D.affs[r.f].co, html: r => D.affs[r.f].co ? '<button type="button" class="name-button" data-country="' + esc(D.affs[r.f].co) + '">' + esc(D.affs[r.f].co) + '</button>' : "", cls: "col-muted" };
const years_column = { key: "years", label: "Years", get: r => r.y0, html: r => esc(years_text(r.y0, r.y1)), cls: "col-muted" };

function columns_for(view) {
  if (view === "affs") return [aff_column, country_column].concat(aff_role_columns, [
    { key: "authors", label: "Authors", num: true, get: r => r.authors, tip: "Different authors from this affiliation" },
    { key: "top_author", label: "Most active author", get: r => r.top_author >= 0 ? D.authors[r.top_author].n : "", html: r => r.top_author >= 0 ? author_page_link(r.top_author) : "", cls: "col-muted" },
    years_column]);
  if (view === "aff_author") return [aff_column,
    { key: "author", label: "Author", get: r => D.authors[r.ai].n, html: r => author_page_link(r.ai) },
    { key: "papers", label: "Papers", num: true, get: r => r.papers },
    { key: "first_all", label: "First author", num: true, get: r => r.r[0] + r.r[1], tip: "First author, corresponding or not" },
    { key: "first_corr", label: "First + corr.", num: true, get: r => r.r[0], tip: role_labels[0] },
    { key: "corr_not_first", label: "Corr., not first", num: true, get: r => r.r[2], tip: role_labels[2] },
    { key: "co", label: "Co-author", num: true, get: r => r.r[3], tip: role_labels[3] },
    years_column];
  if (view === "aff_journal") return [aff_column,
    { key: "journal", label: "Journal", get: r => D.journals[r.jr] }].concat(aff_role_columns, [years_column]);
  if (view === "aff_type") return [aff_column, { key: "papers", label: "Papers", num: true, get: r => r.papers }].concat(
    type_list.map(t => ({ key: "type_" + t, label: t, num: true, get: r => r.tys.get(t) ? r.tys.get(t).n : 0 })));
  return [
    { key: "country", label: "Country", get: r => r.c, html: r => '<button type="button" class="name-button" data-country="' + esc(r.c) + '">' + esc(r.c) + '</button>' },
    { key: "papers", label: "Papers", num: true, get: r => r.papers },
    { key: "first", label: "First author", num: true, get: r => r.first, tip: "Papers with the first author in this country" },
    { key: "affs", label: "Affiliations", num: true, get: r => r.affs },
    { key: "authors", label: "Authors", num: true, get: r => r.authors },
    years_column];
}

// ---------- rows for the selected ranking
function build_rows(stats, pool) {
  const rows = [];
  if (state.view === "countries") {
    aggregate_countries(pool).forEach(function (s, c) {
      rows.push({ c: c, sort_name: c, papers: s.n, first: s.first, affs: s.affs.size, authors: s.authors.size, y0: s.y0, y1: s.y1, search: c.toLowerCase() });
    });
    return rows.filter(r => r.papers >= state.min && query_match(r.search, state.query));
  }
  stats.forEach(function (s) {
    const aff = D.affs[s.id];
    if (state.country && aff.co !== state.country) return;
    const base_text = (aff.n + " " + aff.co + " " + aff.ci).toLowerCase();
    if (state.view === "affs") {
      const top = sorted_entries(s.authors)[0];
      rows.push({ f: s.id, sort_name: aff.n, papers: s.n, r: s.r, y0: s.y0, y1: s.y1, authors: s.authors.size,
        top_author: top ? top[0] : -1,
        search: base_text + " " + Array.from(s.authors.keys()).map(ai => D.authors[ai].n).join(" ").toLowerCase() });
    } else if (state.view === "aff_author") {
      s.authors.forEach(function (e, ai) {
        rows.push({ f: s.id, ai: ai, sort_name: aff.n, papers: e.n, r: e.r, y0: e.y0, y1: e.y1,
          search: base_text + " " + D.authors[ai].n.toLowerCase() });
      });
    } else if (state.view === "aff_journal") {
      s.jrs.forEach(function (e, j) {
        rows.push({ f: s.id, jr: j, sort_name: aff.n, papers: e.n, r: e.r, y0: e.y0, y1: e.y1,
          search: base_text + " " + D.journals[j].toLowerCase() });
      });
    } else {
      rows.push({ f: s.id, sort_name: aff.n, papers: s.n, tys: s.tys, search: base_text });
    }
  });
  return rows.filter(r => r.papers >= state.min && query_match(r.search, state.query));
}

// ---------- overview of all affiliations
function render_overview(pool, pool_all_years, stats) {
  const panel = el("top_panel");
  const countries = aggregate_countries(pool);
  const located = Array.from(stats.keys()).filter(f => D.affs[f].lat !== null).length;
  panel.innerHTML = '<div class="panel-head"><h2>All affiliations</h2><span class="sub">' + esc(filter_text(state)) + '</span><span class="spacer"></span><a class="button" style="text-decoration:none" href="' + D.pages.map + '">Open the map</a></div>' +
    tiles_html([[pool.length, "papers"], [stats.size, "affiliations"], [countries.size, "countries"], [located, "affiliations on the map"],
      [pool.filter(p => !p.pa.length).length, "papers without affiliation", "#BDD4D8"]]) +
    '<div class="charts">' +
    '<div class="chart-box wide"><h3>Papers per year</h3><div id="c_years"></div><p class="note">Select a bar to show that year only, select it again to show all years.</p></div>' +
    '<div class="chart-box"><h3>Top 15 affiliations by papers</h3><div id="c_top"></div></div>' +
    '<div class="chart-box"><h3>Top 15 affiliations by first-author papers</h3><div id="c_first"></div></div>' +
    '<div class="chart-box"><h3>Top 15 countries</h3><div id="c_countries"></div></div>' +
    '<div class="chart-box"><h3>Papers by number of affiliations</h3><div id="c_spread"></div></div>' +
    '</div>';
  const per_year = {};
  pool_all_years.forEach(p => { if (p.y !== null) per_year[p.y] = (per_year[p.y] || 0) + 1; });
  chart_years(el("c_years"), [{ name: "Papers", color: "#009682", values: per_year }],
    { y0: state.y0, y1: state.y1, on_click: y => { toggle_year(state, y); render(); } });
  const all = Array.from(stats.values());
  const select = it => select_aff(it.id);
  chart_hbar(el("c_top"), all.slice().sort((a, b) => b.n - a.n).slice(0, 15).map(s => ({ label: D.affs[s.id].n, id: s.id, values: s.r })),
    { colors: aff_role_colors, names: aff_role_labels, on_click: select });
  chart_hbar(el("c_first"), all.filter(s => s.r[0] > 0).sort((a, b) => b.r[0] - a.r[0] || b.n - a.n).slice(0, 15).map(s => ({ label: D.affs[s.id].n, id: s.id, values: [s.r[0]] })),
    { colors: [aff_role_colors[0]], on_click: select });
  chart_hbar(el("c_countries"), Array.from(countries.values()).sort((a, b) => b.n - a.n).slice(0, 15).map(s => ({ label: s.id, id: s.id, values: [s.first, s.n - s.first] })),
    { colors: [aff_role_colors[0], aff_role_colors[2]], names: ["First author in this country", "Co-authors only"], on_click: it => set_country(it.id) });
  const spread = {};
  pool.forEach(p => { const k = Math.min(p.pa.length, 10); spread[k] = (spread[k] || 0) + 1; });
  chart_hbar(el("c_spread"), Object.keys(spread).map(Number).sort((a, b) => a - b).map(k => ({ label: k === 10 ? "10 or more" : plural(k, "affiliation", "affiliations"), values: [spread[k]] })),
    { colors: ["#56679A"], label_width: 160 });
}

// ---------- profile of one affiliation
function render_profile(f, pool, pool_all_years, stats) {
  const panel = el("top_panel");
  const aff = D.affs[f];
  const s = stats.get(f) || { n: 0, r: [0, 0, 0], y0: null, y1: null, authors: new Map(), jrs: new Map(), tys: new Map(), partners: new Map() };
  const papers = pool.filter(p => p.pa.includes(f));
  const per_role_year = [{}, {}, {}];
  pool_all_years.forEach(function (p) {
    if (!p.pa.includes(f) || p.y === null) return;
    const role = aff_role_of(p, f);
    per_role_year[role][p.y] = (per_role_year[role][p.y] || 0) + 1;
  });
  const place = [aff.ci, aff.co].filter(Boolean).join(", ");
  const ids = [aff.ror ? '<a class="plain-link" href="' + esc(aff.ror) + '" target="_blank" rel="noopener noreferrer">ROR</a>' : "",
    aff.oa ? '<a class="plain-link" href="https://openalex.org/' + esc(aff.oa) + '" target="_blank" rel="noopener noreferrer">OpenAlex</a>' : "",
    aff.lat !== null ? '<a class="plain-link" href="' + D.pages.map + '#aff=' + encodeURIComponent(aff.k) + '">Show on the map</a>' : ""].filter(Boolean).join(" \u00b7 ");

  panel.innerHTML = '<div class="panel-head"><h2>' + esc(aff.n) + '</h2><span class="sub">' + esc([place, years_text(s.y0, s.y1)].filter(Boolean).join(", ")) +
    '</span><span class="spacer"></span><button type="button" class="button" id="back_button">Back to all affiliations</button></div>' +
    (ids ? '<p class="also">' + ids + '</p>' : '') +
    '<p class="tile-group">Papers by role of this affiliation: the last three tiles add up to the total</p>' +
    tiles_html([[s.n, "papers in total"], [s.r[0], "papers with the first author from here", aff_role_colors[0]], [s.r[1], "papers with a corresponding author from here, first author elsewhere", aff_role_colors[1]],
      [s.r[2], "papers with co-authors from here only", aff_role_colors[2]]]) +
    '<p class="tile-group">Collaboration</p>' +
    tiles_html([[s.authors.size, "different people from here on these papers"], [s.partners.size, "different partner institutions on these papers"], [s.jrs.size, "different journals"]]) +
    '<div class="charts">' +
    '<div class="chart-box wide"><h3>Papers per year by role of this affiliation</h3><div id="c_years"></div><p class="note">Select a bar to show that year only.</p></div>' +
    '<div class="chart-box"><h3>Role split</h3><div id="c_roles"></div></div>' +
    '<div class="chart-box"><h3>Authors from here</h3><div id="c_authors"></div><p class="note">Bars show the role of each author. Select one to open the author profile.</p></div>' +
    '<div class="chart-box"><h3>Journals</h3><div id="c_journals"></div></div>' +
    '<div class="chart-box"><h3>Publication types</h3><div id="c_types"></div></div>' +
    '<div class="chart-box wide"><h3>Partner institutions</h3><div id="c_partners"></div><p class="note">Institutions on the same papers. Select one to open it.</p></div>' +
    '<div class="chart-box wide"><h3>' + esc(plural(papers.length, "paper", "papers")) + '</h3>' +
    papers_table_html(papers, p => { const role = aff_role_of(p, f); return [["First author", "Corresponding", "Other"][role], aff_role_colors[role], role === 2 ? "#1F2B30" : "#fff"]; },
      "Authors from here", p => p.a.filter((ai, pos) => (p.aa[pos] || []).includes(f)).map(ai => author_page_link(ai)).join("; ") || '<span class="muted">not known per author</span>') + '</div>' +
    '</div>';

  chart_years(el("c_years"), aff_role_labels.map((label, k) => ({ name: label, color: aff_role_colors[k], values: per_role_year[k] })),
    { y0: state.y0, y1: state.y1, on_click: y => { toggle_year(state, y); render(); } });
  chart_donut(el("c_roles"), aff_role_labels, aff_role_colors, s.r);
  chart_hbar(el("c_authors"), sorted_entries(s.authors).slice(0, 15).map(e => ({ label: D.authors[e[0]].n, id: e[0], values: e[1].r })),
    { colors: role_colors, names: role_labels, on_click: it => go_author_page(it.id) });
  const to_items = (map, name_of, n) => sorted_entries(map).slice(0, n).map(e => ({ label: name_of(e[0]), id: e[0], values: e[1].r }));
  chart_hbar(el("c_journals"), to_items(s.jrs, j => D.journals[j], 12), { colors: aff_role_colors, names: aff_role_labels, legend: false });
  chart_hbar(el("c_types"), to_items(s.tys, t => t, 12), { colors: aff_role_colors, names: aff_role_labels, legend: false });
  chart_hbar(el("c_partners"), to_items(s.partners, g => D.affs[g].n, 20), { colors: aff_role_colors, names: aff_role_labels, label_width: 300, on_click: it => select_aff(it.id) });
  el("back_button").addEventListener("click", () => select_aff(null));
}

function select_aff(f) {
  state.sel = f;
  write_hash("aff", f === null ? null : D.affs[f].k);
  render();
  el("top_panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function set_country(country) {
  state.country = country;
  if (country && state.view === "countries") { state.view = "affs"; el("view_select").value = "affs"; }
  render();
}

// ---------- main render
function render() {
  sync_year_and_type(state);
  const pool = filter_papers(state, false);
  const pool_all_years = filter_papers(state, true);
  const stats = aggregate_affs(pool);
  if (state.sel !== null) render_profile(state.sel, pool, pool_all_years, stats);
  else render_overview(pool, pool_all_years, stats);

  el("country_chip").innerHTML = state.country && state.view !== "countries"
    ? '<span class="chip">Country: ' + esc(state.country) + '<button type="button" id="chip_clear" aria-label="Show all countries">\u00d7</button></span>' : "";
  if (el("chip_clear")) el("chip_clear").addEventListener("click", () => set_country(null));

  const rows = build_rows(stats, pool);
  const shown = render_table("main_table", columns_for(state.view), rows, state.sort[state.view], state.limit,
    function (pair) { state.sort[state.view] = pair; render(); });
  el("empty_state").hidden = rows.length > 0;
  el("count_text").textContent = "Showing " + shown + " of " + plural(rows.length, "row", "rows");
}

// ---------- events
setup_year_and_type(state, render);
el("view_select").addEventListener("change", function () { state.view = this.value; render(); });
el("limit_select").addEventListener("change", function () { state.limit = Number(this.value); render(); });
el("main_table").addEventListener("click", function (event) {
  const aff_target = event.target.closest("[data-aff]");
  if (aff_target) { select_aff(Number(aff_target.dataset.aff)); return; }
  const country_target = event.target.closest("[data-country]");
  if (country_target) set_country(country_target.dataset.country);
});
el("reset_button").addEventListener("click", function () {
  Object.assign(state, { query: "", type: "", y0: null, y1: null, min: 1, country: null });
  el("search_box").value = ""; el("min_input").value = 1;
  render();
});
function apply_hash() {
  const key = read_hash("aff");
  state.sel = key !== null && aff_by_key.has(key) ? aff_by_key.get(key) : null;
  render();
}
window.addEventListener("hashchange", apply_hash);
apply_hash();
</script>
</body>
</html>
'''


# ================================================== #
# HTML MAP PAGE
# ================================================== #

# --- the base map is drawn from country outlines stored inside the page
# no tile server and no API key is used; Leaflet is inlined when it was downloaded
# without Leaflet a plain longitude and latitude plot with the outlines is drawn
MAP_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
__LEAFLET_HEAD__
<style>__SHARED_CSS__
  .map-layout { display: grid; grid-template-columns: 360px 1fr; gap: 20px; align-items: start; }
  .map-side { display: flex; flex-direction: column; gap: 10px; }
  .map-side .controls { margin: 0; }
  .map-side .controls input[type="search"] { flex: 1 1 100%; }
  #map { height: calc(100vh - 190px); min-height: 520px; border: 1px solid var(--rule); background: #DCEBEE; }
  .map-list { list-style: none; margin: 0; padding: 0; max-height: 46vh; overflow-y: auto; border-top: 2px solid var(--ink); }
  .map-list li { display: flex; gap: 8px; align-items: baseline; border-bottom: 1px solid var(--rule); padding: 6px 2px; }
  .map-list li .name-button { flex: 1 1 auto; }
  .map-list li .n { font-variant-numeric: tabular-nums; font-weight: 600; }
  .map-list li .co { color: var(--muted); font-size: 0.82rem; }
  .map-list li.nogeo .name-button { color: var(--muted); }
  .map-side h3 { font-size: 0.95rem; margin: 8px 0 4px; }
  .leaflet-popup-content { font-family: var(--sans); font-size: 13px; line-height: 1.4; }
  .leaflet-popup-content h4 { font-family: var(--serif); font-size: 1.05rem; margin: 0 0 4px; }
  .leaflet-popup-content ol { margin: 4px 0 6px; padding-left: 18px; }
  .map-wrap { position: relative; }
  .map-legend { position: absolute; left: 10px; bottom: 26px; z-index: 1000; background: rgba(255,255,255,0.94); border: 1px solid var(--rule); border-radius: 4px; padding: 8px 10px; font-size: 0.8rem; line-height: 1.35; max-width: 270px; box-shadow: 0 1px 3px rgba(31,43,48,0.12); }
  .map-legend h4 { font-size: 0.82rem; font-weight: 600; margin: 0 0 5px; }
  .map-legend .row { display: flex; align-items: center; gap: 7px; margin: 3px 0; }
  .map-legend .sizes { display: flex; align-items: flex-end; gap: 10px; margin: 4px 0 2px; }
  .map-legend .sizes span { display: flex; flex-direction: column; align-items: center; gap: 2px; color: var(--muted); }
  .map-legend svg { display: block; overflow: visible; }
  .map-legend .muted { margin-top: 5px; }
  #fallback svg { width: 100%; height: auto; display: block; }
  #fallback .dot { fill: #009682; fill-opacity: 0.55; stroke: #00705F; cursor: pointer; }
  #fallback .dot.sel { fill: #56679A; }
  @media (max-width: 900px) { .map-layout { grid-template-columns: 1fr; } #map { height: 60vh; } }
</style>
</head>
<body>
<div class="page">
  __NAV__
  <header>
    <h1>__PAGE_TITLE__</h1>
    <p class="summary">__SUMMARY_TEXT__</p>
  </header>

  <div class="map-layout">
    <aside class="map-side">
      <div class="controls">
        <input type="search" id="search_box" placeholder="Search affiliations, cities, countries" aria-label="Search">
        <select id="type_select" aria-label="Publication type"><option value="">All types</option></select>
        <select id="y0_select" aria-label="From year"></select>
        <select id="y1_select" aria-label="To year"></select>
        <label>Min. papers <input type="number" id="min_input" min="1" value="1"></label>
        <button type="button" class="button" id="reset_button">Clear filters</button>
      </div>
      <div class="muted" id="count_text" aria-live="polite"></div>
      <ol class="map-list" id="map_list"></ol>
      <h3>Countries</h3>
      <div id="c_countries"></div>
    </aside>
    <div class="map-wrap">
      <div id="map" role="region" aria-label="Map of affiliations"></div>
      <div class="map-legend" id="map_legend" aria-label="Map legend"></div>
    </div>
  </div>

  <footer>Generated from __SOURCE_NAME__ on __BUILD_DATE__. Circle area grows with the number of papers. Country outlines: Natural Earth (public domain), via world-atlas. No map service or API key is used.</footer>
</div>

<script>
__SHARED_JS__

// ================= map page =================
const state = { query: "", type: "", y0: null, y1: null, min: 1, sel: null };
const markers = new Map();
let map = null, layer = null;

// ---------- country outlines stored in the page (TopoJSON, Natural Earth)
const WORLD = __WORLD_JSON__;

function world_polygons(topo) {
  // returns a list of countries, each a list of polygons, each a list of rings of [lat, lon]
  if (!topo || !topo.objects || !topo.objects.countries) return [];
  const tf = topo.transform;
  const arcs = topo.arcs.map(function (arc) {
    let x = 0, y = 0;
    return arc.map(function (p) {
      if (!tf) return [p[1], p[0]];
      x += p[0]; y += p[1];
      return [y * tf.scale[1] + tf.translate[1], x * tf.scale[0] + tf.translate[0]];
    });
  });
  function ring(ids) {
    const points = [];
    ids.forEach(function (i, k) {
      const arc = i < 0 ? arcs[~i].slice().reverse() : arcs[i];
      arc.forEach(function (p, j) { if (k === 0 || j > 0) points.push(p); });
    });
    return points;
  }
  const countries = [];
  (topo.objects.countries.geometries || []).forEach(function (g) {
    let polygons = [];
    if (g.type === "Polygon") polygons = [g.arcs.map(ring)];
    else if (g.type === "MultiPolygon") polygons = g.arcs.map(p => p.map(ring));
    if (polygons.length) countries.push({ name: (g.properties || {}).name || "", polygons: polygons });
  });
  return countries;
}
const world_countries = world_polygons(WORLD);

if (window.L) {
  map = L.map("map", { worldCopyJump: false, minZoom: 2, maxZoom: 12, preferCanvas: true,
    maxBounds: [[-85, -200], [85, 200]], maxBoundsViscosity: 0.8 }).setView([35, 10], 2);
  map.attributionControl.setPrefix("");
  map.attributionControl.addAttribution("Outlines: Natural Earth");
  // land in white, borders in the light rule colour; the sea is the map background
  const base = L.layerGroup().addTo(map);
  world_countries.forEach(function (c) {
    L.polygon(c.polygons, { color: "#B8C7CB", weight: 0.7, fillColor: "#FFFFFF", fillOpacity: 1, interactive: false }).addTo(base);
  });
  if (!world_countries.length) {
    for (let lon = -180; lon <= 180; lon += 30) L.polyline([[-85, lon], [85, lon]], { color: "#C9D6D9", weight: 1, interactive: false }).addTo(base);
    for (let lat = -60; lat <= 60; lat += 30) L.polyline([[lat, -180], [lat, 180]], { color: "#C9D6D9", weight: 1, interactive: false }).addTo(base);
  }
  layer = L.layerGroup().addTo(map);
} else {
  el("map").innerHTML = '<div id="fallback"><p class="muted" style="padding:8px 10px;margin:0">The map library could not be loaded, a plain longitude and latitude plot is shown instead.</p><div id="fallback_plot"></div></div>';
}

function popup_html(s) {
  const f = D.affs[s.id];
  const top = sorted_entries(s.authors).slice(0, 5);
  let h = '<h4>' + esc(f.n) + '</h4><div class="muted">' + esc([f.ci, f.co].filter(Boolean).join(", ")) + '</div>';
  h += '<div><b>' + plural(s.n, "paper", "papers") + '</b>, ' + years_text(s.y0, s.y1) + '<br>First author from here: ' + s.r[0] + ', corresponding (not first): ' + s.r[1] + '<br>Authors: ' + s.authors.size + '</div>';
  if (top.length) h += '<ol>' + top.map(e => '<li>' + author_page_link(e[0]) + ' (' + e[1].n + ')</li>').join("") + '</ol>';
  return h + '<a class="plain-link" href="' + D.pages.affs + '#aff=' + encodeURIComponent(f.k) + '">Open affiliation profile</a>';
}

function draw_fallback(located, peak) {
  const W = 1000, H = 500;
  const px = lon => (lon + 180) / 360 * W, py = lat => (90 - lat) / 180 * H;
  let s = '<svg viewBox="0 0 ' + W + ' ' + H + '"><rect width="' + W + '" height="' + H + '" fill="#DCEBEE"/>';
  // country outlines, when they are stored in the page
  world_countries.forEach(function (c) {
    const d = c.polygons.map(poly => poly.map(r => "M" + r.map(p => px(p[1]).toFixed(1) + "," + py(p[0]).toFixed(1)).join("L") + "Z").join("")).join("");
    s += '<path d="' + d + '" fill="#fff" stroke="#B8C7CB" stroke-width="0.5" fill-rule="evenodd"/>';
  });
  for (let lon = -180; lon <= 180; lon += 30) s += '<line x1="' + px(lon) + '" x2="' + px(lon) + '" y1="0" y2="' + H + '" stroke="#D3DDDF"/>';
  for (let lat = -90; lat <= 90; lat += 30) s += '<line y1="' + py(lat) + '" y2="' + py(lat) + '" x1="0" x2="' + W + '" stroke="#D3DDDF"/>';
  located.forEach(function (st) {
    const f = D.affs[st.id];
    s += '<circle class="dot" data-aff="' + st.id + '" cx="' + px(f.lon).toFixed(1) + '" cy="' + py(f.lat).toFixed(1) + '" r="' + marker_radius(st.n, peak).toFixed(1) + '" style="fill:' + (st.r[0] > 0 ? "#009682" : "#56679A") + '"><title>' + esc(f.n) + ': ' + st.n + '</title></circle>';
  });
  el("fallback_plot").innerHTML = s + '</svg>';
}

// ---------- legend: colours and circle sizes, sizes follow the current filters
function marker_radius(n, peak) { return 3 + 17 * Math.sqrt(n / peak); }

function legend_circle(r, fill) {
  const size = Math.ceil(2 * r + 2);
  return '<svg width="' + size + '" height="' + size + '"><circle cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r.toFixed(1) +
    '" fill="' + fill + '" fill-opacity="0.5" stroke="#00705F" stroke-width="1"/></svg>';
}

function draw_legend(peak, located_count) {
  const samples = Array.from(new Set([1, Math.round(peak / 4), peak])).filter(n => n >= 1).sort((a, b) => a - b);
  el("map_legend").innerHTML = '<h4>Legend</h4>' +
    '<div class="row">' + legend_circle(6, "#009682") + '<span>First author from this affiliation on at least one paper</span></div>' +
    '<div class="row">' + legend_circle(6, "#56679A") + '<span>Co-authors or corresponding authors from here, but never the first author</span></div>' +
    '<h4 style="margin-top:7px">Circle size = papers with authors from here</h4>' +
    '<div class="sizes">' + samples.map(n => '<span>' + legend_circle(marker_radius(n, peak), "#BDD4D8") + n + '</span>').join("") + '</div>' +
    '<div class="muted">Click a circle for details. ' + plural(located_count, "affiliation", "affiliations") + ' shown for the current filters.</div>';
}

function render() {
  sync_year_and_type(state);
  const pool = filter_papers(state, false);
  const stats = aggregate_affs(pool);
  let rows = Array.from(stats.values()).filter(s => s.n >= state.min);
  rows = rows.filter(s => query_match((D.affs[s.id].n + " " + D.affs[s.id].co + " " + D.affs[s.id].ci).toLowerCase(), state.query));
  rows.sort((a, b) => b.n - a.n || D.affs[a.id].n.localeCompare(D.affs[b.id].n));
  const located = rows.filter(s => D.affs[s.id].lat !== null);
  const peak = Math.max(1, ...located.map(s => s.n));

  markers.clear();
  if (map) {
    layer.clearLayers();
    // small circles are drawn last so they stay clickable on top of large ones
    located.forEach(function (s) {
      const f = D.affs[s.id];
      const marker = L.circleMarker([f.lat, f.lon], {
        radius: marker_radius(s.n, peak), color: "#00705F", weight: 1,
        fillColor: s.r[0] > 0 ? "#009682" : "#56679A", fillOpacity: 0.5
      }).bindPopup(popup_html(s), { maxWidth: 320 }).bindTooltip(esc(f.n) + ": " + s.n);
      marker.addTo(layer);
      markers.set(s.id, marker);
    });
  } else {
    draw_fallback(located, peak);
  }
  draw_legend(peak, located.length);

  el("count_text").textContent = plural(rows.length, "affiliation", "affiliations") + ", " + located.length + " on the map, " + (rows.length - located.length) + " without coordinates.";
  el("map_list").innerHTML = rows.slice(0, 400).map(s => {
    const f = D.affs[s.id];
    return '<li class="' + (f.lat === null ? "nogeo" : "") + '"><button type="button" class="name-button" data-aff="' + s.id + '"' + (f.lat === null ? ' title="No coordinates"' : '') + '>' + esc(f.n) + '</button><span class="co">' + esc(f.co) + '</span><span class="n">' + s.n + '</span></li>';
  }).join("");
  chart_hbar(el("c_countries"), Array.from(aggregate_countries(pool).values()).sort((a, b) => b.n - a.n).slice(0, 12).map(s => ({ label: s.id, values: [s.first, s.n - s.first] })),
    { colors: ["#00705F", "#BDD4D8"], names: ["First author in this country", "Co-authors only"], label_width: 190 });
}

function focus_aff(f) {
  state.sel = f;
  write_hash("aff", D.affs[f].k);
  const marker = markers.get(f);
  if (map && marker) { map.flyTo(marker.getLatLng(), Math.max(map.getZoom(), 6), { duration: 0.8 }); marker.openPopup(); }
  else if (!map) render();
  else window.location.href = D.pages.affs + "#aff=" + encodeURIComponent(D.affs[f].k);
}

setup_year_and_type(state, render);
el("map_list").addEventListener("click", function (event) {
  const target = event.target.closest("[data-aff]");
  if (target) focus_aff(Number(target.dataset.aff));
});
el("map").addEventListener("click", function (event) {
  const target = event.target.closest("circle[data-aff]");
  if (target) window.location.href = D.pages.affs + "#aff=" + encodeURIComponent(D.affs[Number(target.dataset.aff)].k);
});
el("reset_button").addEventListener("click", function () {
  Object.assign(state, { query: "", type: "", y0: null, y1: null, min: 1 });
  el("search_box").value = ""; el("min_input").value = 1;
  render();
});
render();
const start_key = read_hash("aff");
if (start_key !== null && aff_by_key.has(start_key)) focus_aff(aff_by_key.get(start_key));
</script>
</body>
</html>
'''


# ================================================== #
# PAGE ASSEMBLY
# ================================================== #

def build_nav(current_name):
    # links between the three pages and the publication list
    links = [(authors_html_name, "Authors"), (affiliations_html_name, "Affiliations"),
             (map_html_name, "Affiliations map")]
    parts = []
    for file_name, label in links:
        current = ' aria-current="page"' if file_name == current_name else ""
        parts.append('<a href="%s"%s>%s</a>' % (file_name, current, label))
    parts.append('<a href="../publications/%s">Publication list</a>' % PUBLICATION_LIST_PATH.name)
    return '<nav class="pages">' + "".join(parts) + "</nav>"


def percent_text(part, whole):
    if not whole:
        return "0%"
    return "%d%%" % round(100.0 * part / whole)


def summary_numbers(data, stats):
    # numbers shown under the page titles
    years = [paper["y"] for paper in data["papers"] if paper.get("y")]
    located = sum(1 for item in data["affs"] if item["lat"] is not None)
    countries = {item["co"] for item in data["affs"] if item["co"]}
    slots = stats["author_slots_openalex"] + stats["author_slots_wos"] + stats["author_slots_unknown"]
    return {
        "papers": len(data["papers"]), "authors": len(data["authors"]), "affs": len(data["affs"]),
        "countries": len(countries), "located": located,
        "year_text": "%s to %s" % (min(years), max(years)) if years else "no years",
        "corr_coverage": percent_text(stats["papers_corresponding_known"], stats["papers"]),
        "aff_coverage": percent_text(stats["author_slots_openalex"] + stats["author_slots_wos"], slots),
    }


def data_json(data):
    # "</" is escaped so no text in the data can close the script tag
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def fill_template(template, replacements):
    # the shared JavaScript goes in first, because it carries __DATA_JSON__ itself
    page = template.replace("__SHARED_JS__", SHARED_JS)
    page = page.replace("__SHARED_CSS__", SHARED_CSS)
    for placeholder, value in replacements.items():
        page = page.replace(placeholder, value)
    return page


def leaflet_head(assets):
    # inlined Leaflet when it is cached, otherwise the CDN (only the library, no map tiles)
    if assets.get("leaflet_js") and assets.get("leaflet_css"):
        script = assets["leaflet_js"].replace("</script", "<\\/script")
        return "<style>" + assets["leaflet_css"] + "</style>\n<script>" + script + "</script>"
    return ('<link rel="stylesheet" href="%s">\n<script src="%s"></script>' % (leaflet_css_url, leaflet_js_url))


def world_json(assets):
    # the TopoJSON text, compacted; "null" draws the map without outlines
    try:
        topo = json.loads(assets.get("world") or "null")
    except ValueError:
        return "null"
    if not isinstance(topo, dict) or "countries" not in (topo.get("objects") or {}):
        return "null"
    # the land object is not used, dropping it keeps the page smaller
    topo["objects"] = {"countries": topo["objects"]["countries"]}
    return json.dumps(topo, separators=(",", ":")).replace("</", "<\\/")


def render_pages(data, stats, assets):
    # returns {file name: html text}
    numbers = summary_numbers(data, stats)
    shared = {
        "__SOURCE_NAME__": html.escape(data["source"]),
        "__BUILD_DATE__": data["built"],
        "__CORR_COVERAGE__": numbers["corr_coverage"],
        "__AFF_COVERAGE__": numbers["aff_coverage"],
        "__DATA_JSON__": data_json(data),
    }
    pages = {}

    # --- authors page
    replacements = dict(shared)
    replacements["__PAGE_TITLE__"] = "AIDA Authors"
    replacements["__NAV__"] = build_nav(authors_html_name)
    replacements["__SUMMARY_TEXT__"] = (
        "%d authors on %d AIDA publications, %s. Select an author to see the full profile."
        % (numbers["authors"], numbers["papers"], numbers["year_text"]))
    pages[authors_html_name] = fill_template(AUTHORS_TEMPLATE, replacements)

    # --- affiliations page
    replacements = dict(shared)
    replacements["__PAGE_TITLE__"] = "AIDA Affiliations"
    replacements["__NAV__"] = build_nav(affiliations_html_name)
    replacements["__SUMMARY_TEXT__"] = (
        "%d affiliations in %d countries on %d AIDA publications, %s. Select an affiliation to see its profile."
        % (numbers["affs"], numbers["countries"], numbers["papers"], numbers["year_text"]))
    pages[affiliations_html_name] = fill_template(AFFILIATIONS_TEMPLATE, replacements)

    # --- map page
    replacements = dict(shared)
    replacements["__PAGE_TITLE__"] = "AIDA Affiliations Map"
    replacements["__NAV__"] = build_nav(map_html_name)
    replacements["__SUMMARY_TEXT__"] = (
        "%d of %d affiliations placed on the map, %d countries, %d AIDA publications, %s."
        % (numbers["located"], numbers["affs"], numbers["countries"], numbers["papers"], numbers["year_text"]))
    page = fill_template(MAP_TEMPLATE, replacements)
    # the large assets go in last, so no placeholder search runs through them
    page = page.replace("__WORLD_JSON__", world_json(assets), 1)
    pages[map_html_name] = page.replace("__LEAFLET_HEAD__", leaflet_head(assets), 1)
    return pages


# ================================================== #
# REPORT
# ================================================== #

def print_report(data, stats, coord_sources, verbose):
    numbers = summary_numbers(data, stats)
    print("")
    print("  papers                      %d" % numbers["papers"])
    print("  papers matched in OpenAlex  %d" % stats["papers_openalex"])
    print("  papers with WoS addresses   %d" % stats["papers_wos_links"])
    print("  corresponding author known  %d (%s)" % (stats["papers_corresponding_known"],
                                                   numbers["corr_coverage"]))
    print("  author positions with aff.  %s" % numbers["aff_coverage"])
    print("  papers without affiliation  %d" % stats["papers_without_affiliation"])
    print("  authors                     %d" % numbers["authors"])
    print("  affiliations                %d (%d on the map)" % (numbers["affs"], numbers["located"]))
    print("  countries                   %d" % numbers["countries"])
    if verbose:
        print("  coordinate sources          %s" % ", ".join(
            "%s %d" % pair for pair in sorted(coord_sources.items())))
        print("  corresponding not matched   %d" % stats["corresponding_unmatched"])
    if stats["corresponding_overridden"]:
        print("  corresponding corrected by hand  %d" % stats["corresponding_overridden"])

    # --- top ten authors by papers
    totals = Counter()
    firsts = Counter()
    for paper in data["papers"]:
        for position, author_index in enumerate(paper["a"]):
            totals[author_index] += 1
            firsts[author_index] += 1 if position == 0 else 0
    print("")
    print("  top authors (papers, first author)")
    for author_index, count in totals.most_common(10):
        print("    %-32s %4d %4d" % (data["authors"][author_index]["n"][:32], count, firsts[author_index]))


# ================================================== #
# ARGUMENTS
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(description="AIDA authors, affiliations and map pages from the master bib file.")
    parser.add_argument("--input", default=str(INPUT_BIB_PATH), help="master BibTeX file")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="folder for html, csv and cache")
    parser.add_argument("--no-openalex", action="store_true", help="no API calls, bib file and cache only")
    parser.add_argument("--no-geocode", action="store_true", help="no ROR look ups for map positions")
    parser.add_argument("--refresh-cache", action="store_true", help="ask again for cached misses")
    parser.add_argument("--no-map-download", action="store_true",
                        help="do not download the map outlines and library, use the cache only")
    parser.add_argument("--dry-run", action="store_true", help="run everything, write nothing")
    parser.add_argument("--verbose", action="store_true", help="more details while running")
    return parser.parse_args()


# ================================================== #
# MAIN
# ================================================== #

def main():
    arguments = parse_arguments()
    INPUT_PATH = Path(arguments.input)
    OUT_DIR = Path(arguments.output_dir)
    CACHE_DIR = OUT_DIR / "cache"
    WORKS_CACHE_PATH = CACHE_DIR / "openalex_works.json"
    INSTITUTIONS_CACHE_PATH = CACHE_DIR / "openalex_institutions.json"
    ROR_CACHE_PATH = CACHE_DIR / "ror_affiliations.json"
    AUTHOR_ALIASES_PATH = OUT_DIR / "author_aliases.csv"
    CORRESPONDING_OVERRIDES_PATH = OUT_DIR / "corresponding_overrides.csv"
    AFFILIATION_ALIASES_PATH = OUT_DIR / "affiliation_aliases.csv"
    MANUAL_COORDS_PATH = OUT_DIR / "affiliation_coords_manual.csv"
    write_enabled = not arguments.dry_run

    if not INPUT_PATH.is_file():
        print("Input file not found: %s" % INPUT_PATH)
        sys.exit(1)

    # --- read the bib file
    print("Reading %s" % INPUT_PATH)
    entries = parse_bibtex(INPUT_PATH.read_text(encoding="utf-8", errors="replace"), arguments.verbose)
    records = [build_base_record(entry) for entry in entries]
    print("  %d entries" % len(records))
    if not records:
        print("No entries found, nothing to do.")
        sys.exit(1)

    # --- OpenAlex authorships and institutions
    # a dry run still reads the caches but asks no API
    use_network = not arguments.no_openalex and not arguments.dry_run
    client = ApiClient(enabled=use_network, verbose=arguments.verbose)
    works_cache = load_cache(WORKS_CACHE_PATH)
    institution_cache = load_cache(INSTITUTIONS_CACHE_PATH)
    ror_cache = load_cache(ROR_CACHE_PATH)
    if use_network:
        print("Asking OpenAlex")
        fetch_openalex_works(records, works_cache, client, arguments.refresh_cache,
                             WORKS_CACHE_PATH, write_enabled)
    institution_ids = set()
    for record in records:
        for authorship in (find_work(record, works_cache) or {}).get("authorships", []):
            for institution in authorship["inst"]:
                institution_ids.add(institution["id"])
    if use_network:
        fetch_openalex_institutions(institution_ids, institution_cache, client, arguments.refresh_cache,
                                    INSTITUTIONS_CACHE_PATH, write_enabled)

    if use_network and client.failure_count and not client.request_count:
        print("  OpenAlex did not answer, using the bib file and the cache only")

    # --- authors and affiliations
    print("Building authors and affiliations")
    alias_keys, forced_names = load_author_aliases(AUTHOR_ALIASES_PATH)
    author_registry = AuthorRegistry(alias_keys, forced_names)
    affiliation_registry = AffiliationRegistry(load_affiliation_aliases(AFFILIATION_ALIASES_PATH))
    corresponding_overrides = load_corresponding_overrides(CORRESPONDING_OVERRIDES_PATH)
    papers, journals, raw_texts, stats = build_dataset(records, works_cache, institution_cache,
                                            author_registry, affiliation_registry,
                                            corresponding_overrides)

    # --- coordinates for the map
    print("Locating affiliations")
    # ROR needs no API key, so it also runs with --no-openalex
    use_ror = not arguments.dry_run and not arguments.no_geocode
    ror_client = ApiClient(enabled=use_ror, verbose=arguments.verbose)
    coord_sources = locate_affiliations(affiliation_registry, institution_cache,
                                        load_coordinate_table(MANUAL_COORDS_PATH),
                                        load_coordinate_table(INSTITUTION_COORDS_PATH),
                                        ror_cache, ror_client, arguments.refresh_cache, use_ror, arguments.verbose)
    save_cache(ROR_CACHE_PATH, ror_cache, write_enabled and use_ror)
    remap = affiliation_registry.merge_by_ror()
    data = finalize_data(papers, journals, raw_texts, author_registry, affiliation_registry, remap, INPUT_PATH.name)
    print_report(data, stats, coord_sources, arguments.verbose)

    # --- pages and tables
    # --- map outlines and library; no API key, downloaded once
    assets = load_map_assets(CACHE_DIR, allow_download=write_enabled and not arguments.no_map_download)
    if not assets["world"]:
        print("  no country outlines for the map, the map shows a plain grid")
    pages = render_pages(data, stats, assets)
    if not write_enabled:
        print("")
        print("Dry run, nothing written. Pages would go to %s" % OUT_DIR)
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for file_name, page in pages.items():
        (OUT_DIR / file_name).write_text(page, encoding="utf-8")
    write_csv_files(data, OUT_DIR)
    print("")
    print("Written to %s" % OUT_DIR)
    for file_name in pages:
        print("  %s" % file_name)
    print("  AIDA_*.csv tables")
    if client.request_count or ror_client.request_count:
        print("  API requests: OpenAlex %d, ROR %d" % (client.request_count, ror_client.request_count))


if __name__ == "__main__":
    main()
