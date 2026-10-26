'''
3_a_aida_bib_html.py

   Converts the AIDA master BibTeX bibliography into interactive,
   self-contained HTML publication lists: one for all of AIDA and one per
   researcher.

Purpose

   Read every entry of AIDA_Master_bib.bib, clean the LaTeX markup in
   authors, titles and journals, and write a single HTML page with a
   clickable year histogram, free text search, a publication type filter,
   sortable columns and clickable DOI links.
   Papers an author confirmed in his own list (field aida_confirmed_by,
   set by 3_a_aida_master_bib.py) are marked under the title, and a filter
   shows only confirmed or only unconfirmed papers.
   The same page is then made for every researcher file in
   AIDA_master/authors, together with an index page that links them all.
   The pages have no external dependencies and open in any browser.

Usage

   python 3_a_aida_bib_html.py
   python 3_a_aida_bib_html.py --input some_folder/AIDA_Master_bib.bib
   python 3_a_aida_bib_html.py --output some_folder/AIDA_Publication_List.html
   python 3_a_aida_bib_html.py --title "AIDA publications" --max-authors 10
   python 3_a_aida_bib_html.py --dry-run --verbose
   python 3_a_aida_bib_html.py --no-authors      only the main page

   Without --input and --output the paths come from config.py and are
   relative to the project root, the same on Linux and Windows.
   With --input only that one page is made, no author pages.
   The page in AIDA_lists/publications that was sent to the authors is
   never written to, so their corrections keep pointing at what they saw.

Output

   <root>/output/publications/AIDA_Publication_List.html   (interactive HTML page, all of AIDA)
   <root>/output/publications/AIDA_Authors_Index.html      (links to every researcher page)
   <root>/output/publications/authors/<First_Last>.html    (one page per researcher)
   <root>/output/publications/AIDA_Publication_List.json   (optional, parsed records, with --json)
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import datetime
import html
import json
import os
import re
import sys
import unicodedata
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
# the input follows MASTER_BIB_FILE in config.py, the file written by
# 3_a_aida_master_bib.py, so both scripts always use the same master bib
BASE_DIR = Path(getattr(config, "BASE_DIR", FALLBACK_ROOT))
MASTER_DIR = Path(getattr(config, "MASTER_BIB_DIR", BASE_DIR / "AIDA_master"))
PUBLICATIONS_DIR = Path(getattr(config, "PUBLICATION_LIST_DIR", BASE_DIR / "output" / "publications"))
INPUT_BIB_PATH = Path(getattr(config, "MASTER_BIB_FILE", MASTER_DIR / "AIDA_Master_bib.bib"))
OUTPUT_HTML_PATH = Path(getattr(config, "PUBLICATION_LIST_FILE", PUBLICATIONS_DIR / "AIDA_Publication_List.html"))
# --- one page per researcher, from the author masters of 3_a_aida_master_bib.py
AUTHOR_BIB_DIR = Path(getattr(config, "MASTER_BIB_AUTHORS_DIR", MASTER_DIR / "authors"))
AUTHOR_PAGES_DIR = Path(getattr(config, "AUTHOR_PUBLICATION_DIR", PUBLICATIONS_DIR / "authors"))
AUTHOR_INDEX_PATH = Path(getattr(config, "AUTHOR_PUBLICATION_INDEX_FILE", PUBLICATIONS_DIR / "AIDA_Authors_Index.html"))

# --- page settings
page_title_default = "AIDA publications"
max_authors_default = 10

# --- BibTeX entry type to label shown in the Type column
# anything not listed here is shown as "Other"
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


# ================================================== #
# BIBTEX PARSER
# ================================================== #

# --- a small dependency free parser
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

# --- private use characters that mark sub and sup tags until escaping is done
SUB_OPEN, SUB_CLOSE, SUP_OPEN, SUP_CLOSE = "\ue000", "\ue001", "\ue002", "\ue003"

# --- accent commands mapped to unicode combining characters
accent_marks = {
    '"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302",
    "~": "\u0303", "=": "\u0304", ".": "\u0307", "c": "\u0327",
    "v": "\u030C", "u": "\u0306", "H": "\u030B", "k": "\u0328",
    "r": "\u030A", "d": "\u0323", "b": "\u0331",
}

# --- letter commands without argument
letter_commands = {
    "ss": "ß", "o": "ø", "O": "Ø", "ae": "æ", "AE": "Æ", "oe": "œ",
    "OE": "Œ", "aa": "å", "AA": "Å", "l": "ł", "L": "Ł", "i": "ı",
    "j": "ȷ", "dh": "ð", "DH": "Ð", "th": "þ", "TH": "Þ",
}

# --- symbol commands, greek letters and common text commands
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

# --- formatting commands whose argument is kept as plain text
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


# ================================================== #
# FIELD FORMATTING
# ================================================== #

# --- authors

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


def make_initials(given_names):
    # "Jean-Pierre Karl" becomes "J.-P. K."
    initials = []
    for word in given_names.replace(".", ". ").split():
        pieces = [piece for piece in word.split("-") if piece]
        piece_initials = []
        for piece in pieces:
            letters = [char for char in piece if char.isalpha()]
            if letters:
                piece_initials.append(letters[0].upper() + ".")
        if piece_initials:
            initials.append("-".join(piece_initials))
    return " ".join(initials)


def format_author(raw_name):
    # returns "Last, F. M." from "Last, First Middle" or "First Middle Last"
    raw_name = raw_name.strip()

    # a fully braced name is an organisation, keep it unchanged
    if raw_name.startswith("{") and raw_name.endswith("}") and raw_name.count("{") == 1:
        return clean_latex(raw_name)

    name = to_plain(clean_latex(raw_name))
    if not name:
        return ""
    if name.lower() in ("others", "et al.", "et al"):
        return "et al."

    if "," in name:
        comma_parts = [part.strip() for part in name.split(",")]
        last_name = comma_parts[0]
        # "Last, Jr, First" format
        given_names = comma_parts[-1] if len(comma_parts) > 1 else ""
    else:
        words = name.split()
        if len(words) == 1:
            return words[0]
        # lowercase particles such as von, van, de start the last name
        split_at = len(words) - 1
        for position, word in enumerate(words[:-1]):
            if word[0].islower():
                split_at = position
                break
        last_name = " ".join(words[split_at:])
        given_names = " ".join(words[:split_at])

    initials = make_initials(given_names)
    return "%s, %s" % (last_name, initials) if initials else last_name


def format_authors(raw_authors, max_authors):
    names = [format_author(part) for part in split_top_level(raw_authors, "and")]
    names = [name for name in names if name]
    authors_full = "; ".join(names)
    if len(names) > max_authors:
        authors_short = "; ".join(names[:max_authors]) + " et al."
    else:
        authors_short = authors_full
    first_author = names[0] if names else ""
    return authors_short, authors_full, first_author


# --- year

def extract_year(fields):
    for field_name in ("year", "date", "issued", "urldate"):
        match = re.search(r"(1[89]\d\d|20\d\d)", fields.get(field_name, ""))
        if match:
            return int(match.group(1))
    return None


# --- venue

def extract_venue(entry_type, fields):
    for field_name in ("journal", "journaltitle", "booktitle", "series",
                       "school", "institution", "organization", "publisher",
                       "howpublished"):
        value = to_plain(clean_latex(fields.get(field_name, "")))
        if value:
            return value
    return ""


def extract_venue_detail(fields):
    volume = to_plain(clean_latex(fields.get("volume", "")))
    number = to_plain(clean_latex(fields.get("number", fields.get("issue", ""))))
    pages = to_plain(clean_latex(fields.get("pages", "")))
    pages = re.sub(r"\s*-+\s*", "-", pages)

    detail = volume
    if number:
        detail = "%s(%s)" % (detail, number) if detail else "(%s)" % number
    if pages:
        detail = "%s, %s" % (detail, pages) if detail else pages
    return detail


# --- DOI and URL

def clean_doi(raw_doi):
    doi = to_plain(clean_latex(raw_doi)).strip()
    doi = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    doi = doi.replace("\\_", "_").replace(" ", "")
    return doi if doi.startswith("10.") else ""


def doi_to_url(doi):
    if not doi:
        return ""
    safe_chars = "/:;()._-<>[]+"
    from urllib.parse import quote
    return "https://doi.org/" + quote(doi, safe=safe_chars)


def clean_url(raw_url):
    url = raw_url.strip().replace("\\_", "_").replace("\\%", "%")
    url = url.replace("{", "").replace("}", "")
    return url if re.match(r"https?://", url, flags=re.I) else ""


# ================================================== #
# RECORD BUILDING
# ================================================== #

def build_record(entry, max_authors):
    fields = entry["fields"]
    authors_short, authors_full, first_author = format_authors(
        fields.get("author", fields.get("editor", "")), max_authors)

    title_clean = clean_latex(fields.get("title", ""))
    doi = clean_doi(fields.get("doi", ""))
    url = "" if doi else clean_url(fields.get("url", ""))

    return {
        "key": entry["key"],
        "year": extract_year(fields),
        "authors_short": authors_short,
        "authors_full": authors_full,
        "first_author": first_author,
        "title_plain": to_plain(title_clean),
        "title_html": to_html(title_clean),
        "venue": extract_venue(entry["entry_type"], fields),
        "venue_detail": extract_venue_detail(fields),
        "doi": doi,
        "doi_url": doi_to_url(doi),
        "url": url,
        "type_label": type_labels.get(entry["entry_type"], "Other"),
        # the authors who confirmed this paper in their own list, empty when nobody did
        "confirmed_by": to_plain(clean_latex(fields.get("aida_confirmed_by", ""))),
    }


def sort_records(records):
    # newest first, then first author, then title
    return sorted(records, key=lambda r: (-(r["year"] or 0), r["first_author"].lower(),
                                          r["title_plain"].lower()))


# ================================================== #
# HTML WRITER
# ================================================== #

# --- the page template, placeholders are replaced in render_html
HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<style>
  :root {
    --paper: #FBFCFC;
    --ink: #1F2B30;
    --muted: #5B696E;
    --rule: #D3DDDF;
    --green: #009682;
    --green-deep: #00705F;
    --ice: #E3F0F2;
    --serif: Charter, "Bitstream Charter", "Sitka Text", Cambria, Georgia, serif;
    --sans: "Source Sans 3", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    font-family: var(--sans);
    font-size: 15px;
    line-height: 1.45;
  }
  .page { max-width: 1320px; margin: 0 auto; padding: 40px 28px 64px; }

  /* header */
  h1 {
    font-family: var(--serif);
    font-weight: 600;
    font-size: 2.1rem;
    line-height: 1.15;
    margin: 0 0 6px;
  }
  .summary { color: var(--muted); margin: 0 0 28px; font-size: 1rem; }

  /* year histogram, also the year filter */
  .years { margin-bottom: 26px; }
  .years-bars {
    display: flex;
    align-items: flex-end;
    gap: 3px;
    height: 110px;
    border-bottom: 1px solid var(--ink);
  }
  .year-bar {
    flex: 1 1 0;
    min-width: 6px;
    height: 100%;
    padding: 0;
    border: 0;
    background: transparent;
    cursor: pointer;
    display: flex;
    align-items: flex-end;
  }
  .year-bar span {
    display: block;
    width: 100%;
    background: var(--green);
    min-height: 2px;
  }
  .year-bar.empty span { background: var(--rule); }
  .year-bar:hover span { background: var(--green-deep); }
  .years.has-selection .year-bar span { background: var(--ice); }
  .years.has-selection .year-bar[aria-pressed="true"] span { background: var(--green-deep); }
  .year-bar:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
  .years-axis { display: flex; gap: 3px; margin-top: 4px; }
  .years-axis span {
    flex: 1 1 0;
    min-width: 6px;
    font-size: 0.75rem;
    color: var(--muted);
    text-align: center;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    overflow: visible;
  }
  .years-hint { font-size: 0.85rem; color: var(--muted); margin-top: 8px; }

  /* controls */
  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px 16px;
    margin-bottom: 14px;
  }
  .controls input[type="search"] {
    flex: 1 1 320px;
    font: inherit;
    padding: 9px 12px;
    border: 1px solid var(--rule);
    border-radius: 4px;
    background: #fff;
    color: var(--ink);
  }
  .controls select, .controls button {
    font: inherit;
    padding: 8px 12px;
    border: 1px solid var(--rule);
    border-radius: 4px;
    background: #fff;
    color: var(--ink);
  }
  .controls button { cursor: pointer; }
  .controls button:hover { border-color: var(--green); }
  .controls :focus-visible, .sort-button:focus-visible, a:focus-visible {
    outline: 2px solid var(--green);
    outline-offset: 2px;
  }
  .count { color: var(--muted); margin-left: auto; font-variant-numeric: tabular-nums; }

  /* table */
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; min-width: 960px; }
  thead th {
    position: sticky;
    top: 0;
    background: var(--paper);
    border-bottom: 2px solid var(--ink);
    text-align: left;
    padding: 0;
    font-weight: 600;
    z-index: 1;
  }
  .sort-button {
    font: inherit;
    font-weight: 600;
    color: var(--ink);
    background: none;
    border: 0;
    padding: 10px 10px;
    width: 100%;
    text-align: left;
    cursor: pointer;
  }
  .sort-button::after { content: ""; display: inline-block; width: 1em; margin-left: 4px; color: var(--green-deep); }
  th[aria-sort="ascending"] .sort-button::after { content: "\25B2"; font-size: 0.7em; }
  th[aria-sort="descending"] .sort-button::after { content: "\25BC"; font-size: 0.7em; }
  tbody td {
    border-bottom: 1px solid var(--rule);
    padding: 11px 10px;
    vertical-align: top;
  }
  tbody tr:hover td { background: var(--ice); }
  .col-num { width: 3.5em; }
  td.col-num { color: var(--muted); font-variant-numeric: tabular-nums; text-align: right; }
  .col-year { width: 5em; font-weight: 600; font-variant-numeric: tabular-nums; }
  .col-authors { width: 22%; }
  td.col-authors { color: var(--muted); font-size: 0.92rem; }
  td.col-title { font-family: var(--serif); font-size: 1.02rem; line-height: 1.5; }
  .col-venue { width: 17%; }
  .col-venue small { display: block; color: var(--muted); font-variant-numeric: tabular-nums; }
  .col-type { width: 8em; }
  td.col-type { color: var(--muted); font-size: 0.9rem; }
  .col-doi { width: 15em; }
  td.col-doi { font-size: 0.88rem; word-break: break-all; }
  .col-doi a { color: var(--green-deep); text-decoration: underline; text-underline-offset: 2px; }
  .col-doi a:visited { color: #4F5F8A; }
  .missing { color: var(--rule); }
  .confirmed { display: block; margin-top: 4px; font-family: var(--sans); font-size: 0.82rem; color: var(--green-deep); }
  .confirmed::before { content: "\2713  "; }
  .pages { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 0 0 10px; font-size: 0.92rem; }
  .pages a { color: var(--green-deep); text-underline-offset: 2px; }
  .index-list { columns: 3 260px; padding: 0; list-style: none; }
  .index-list li { break-inside: avoid; padding: 5px 0; border-bottom: 1px solid var(--rule); }
  .index-list a { color: var(--ink); }
  .index-list small { color: var(--muted); margin-left: 6px; font-variant-numeric: tabular-nums; }
  sub, sup { font-size: 0.72em; line-height: 0; }

  .empty-state { padding: 40px 10px; color: var(--muted); }
  .empty-state button { margin-top: 10px; }
  footer { margin-top: 36px; color: var(--muted); font-size: 0.85rem; }

  @media (max-width: 720px) {
    .page { padding: 24px 14px 48px; }
    h1 { font-size: 1.6rem; }
    .count { margin-left: 0; width: 100%; }
  }
  @media print {
    .years, .controls { display: none; }
    thead th { position: static; }
    table { min-width: 0; }
  }
</style>
</head>
<body>
<div class="page">
  <header>
    __NAV__
    <h1>__PAGE_TITLE__</h1>
    <p class="summary">__SUMMARY_TEXT__</p>
  </header>

  <section class="years" id="years" aria-label="Publications per year">
    <div class="years-bars" id="years_bars"></div>
    <div class="years-axis" id="years_axis" aria-hidden="true"></div>
    <div class="years-hint" id="years_hint">Select a year bar to show only that year.</div>
  </section>

  <div class="controls">
    <input type="search" id="search_box" placeholder="Search authors, titles, journals, DOIs" aria-label="Search publications">
    <select id="type_select" aria-label="Filter by publication type">
      <option value="">All types</option>
    </select>
    <select id="confirmed_select" aria-label="Filter by author confirmation">
      <option value="">All papers</option>
      <option value="yes">Confirmed by an author</option>
      <option value="no">Not confirmed</option>
    </select>
    <button type="button" id="reset_button">Clear filters</button>
    <span class="count" id="count_text" aria-live="polite"></span>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th class="col-num" scope="col"><span class="sort-button" style="cursor:default">#</span></th>
          <th class="col-year" scope="col" data-key="year"><button class="sort-button" type="button">Year</button></th>
          <th class="col-authors" scope="col" data-key="authors"><button class="sort-button" type="button">Authors</button></th>
          <th class="col-title" scope="col" data-key="title"><button class="sort-button" type="button">Title</button></th>
          <th class="col-venue" scope="col" data-key="venue"><button class="sort-button" type="button">Journal</button></th>
          <th class="col-type" scope="col" data-key="type"><button class="sort-button" type="button">Type</button></th>
          <th class="col-doi" scope="col" data-key="doi"><button class="sort-button" type="button">DOI</button></th>
        </tr>
      </thead>
      <tbody id="table_body"></tbody>
    </table>
    <div class="empty-state" id="empty_state" hidden>
      No publications match these filters.<br>
      <button type="button" id="empty_reset_button">Clear filters</button>
    </div>
  </div>

  <footer>Generated from __SOURCE_NAME__ on __BUILD_DATE__.</footer>
</div>

<script>
const records = __RECORDS_JSON__;

// ---------- state
const state = { sort_key: "year", sort_dir: "desc", query: "", year: null, type: "", confirmed: "" };

// ---------- precomputed search text
records.forEach(function (r) {
  r.search_text = [r.year, r.authors_full, r.title_plain, r.venue, r.venue_detail, r.doi, r.type_label, r.key, r.confirmed_by]
    .join(" ").toLowerCase();
});

const years_known = records.map(r => r.year).filter(y => y !== null);
const year_min = years_known.length ? Math.min.apply(null, years_known) : null;
const year_max = years_known.length ? Math.max.apply(null, years_known) : null;

// ---------- type dropdown
const type_select = document.getElementById("type_select");
Array.from(new Set(records.map(r => r.type_label))).sort().forEach(function (label) {
  const option = document.createElement("option");
  option.value = label;
  option.textContent = label;
  type_select.appendChild(option);
});

// ---------- filtering
function matches_text_and_type(r) {
  if (state.type && r.type_label !== state.type) return false;
  if (state.confirmed === "yes" && !r.confirmed_by) return false;
  if (state.confirmed === "no" && r.confirmed_by) return false;
  if (!state.query) return true;
  return state.query.split(/\s+/).every(term => r.search_text.includes(term));
}

// ---------- sorting
function sort_value(r, key) {
  switch (key) {
    case "year": return r.year;
    case "authors": return r.first_author;
    case "title": return r.title_plain;
    case "venue": return r.venue;
    case "type": return r.type_label;
    case "doi": return r.doi;
  }
  return "";
}

function compare_records(a, b) {
  const va = sort_value(a, state.sort_key);
  const vb = sort_value(b, state.sort_key);
  const a_missing = va === null || va === "";
  const b_missing = vb === null || vb === "";
  // missing values always go to the bottom
  if (a_missing !== b_missing) return a_missing ? 1 : -1;
  let result = 0;
  if (!a_missing) {
    result = state.sort_key === "year"
      ? va - vb
      : String(va).localeCompare(String(vb), undefined, { sensitivity: "base", numeric: true });
    if (state.sort_dir === "desc") result = -result;
  }
  // ties: newest first, then first author, then title
  if (result === 0) result = (b.year || 0) - (a.year || 0);
  if (result === 0) result = a.first_author.localeCompare(b.first_author, undefined, { sensitivity: "base" });
  if (result === 0) result = a.title_plain.localeCompare(b.title_plain, undefined, { sensitivity: "base" });
  return result;
}

// ---------- year histogram
function render_years(pool) {
  const bars = document.getElementById("years_bars");
  const axis = document.getElementById("years_axis");
  const section = document.getElementById("years");
  bars.innerHTML = "";
  axis.innerHTML = "";
  if (year_min === null) { section.hidden = true; return; }

  const counts = {};
  pool.forEach(r => { if (r.year !== null) counts[r.year] = (counts[r.year] || 0) + 1; });
  const peak = Math.max(1, ...Object.values(counts));
  const span = year_max - year_min;
  const label_step = span > 30 ? 10 : 5;

  for (let y = year_min; y <= year_max; y++) {
    const n = counts[y] || 0;
    const bar = document.createElement("button");
    bar.type = "button";
    bar.className = "year-bar" + (n === 0 ? " empty" : "");
    bar.setAttribute("aria-pressed", state.year === y ? "true" : "false");
    bar.setAttribute("aria-label", y + ": " + n + (n === 1 ? " publication" : " publications"));
    bar.title = y + ": " + n;
    const fill = document.createElement("span");
    fill.style.height = (n === 0 ? 0 : Math.max(4, 100 * n / peak)) + "%";
    bar.appendChild(fill);
    bar.addEventListener("click", function () {
      state.year = state.year === y ? null : y;
      render();
    });
    bars.appendChild(bar);

    const tick = document.createElement("span");
    if (y === year_min || y === year_max || (y % label_step === 0 && y - year_min > 2 && year_max - y > 2)) {
      tick.textContent = y;
    }
    axis.appendChild(tick);
  }
  section.classList.toggle("has-selection", state.year !== null);
  document.getElementById("years_hint").textContent = state.year === null
    ? "Select a year bar to show only that year."
    : "Showing " + state.year + " only. Select the bar again to show all years.";
}

// ---------- table
function make_cell(class_name) {
  const td = document.createElement("td");
  td.className = class_name;
  return td;
}

function render_table(rows) {
  const body = document.getElementById("table_body");
  const fragment = document.createDocumentFragment();
  rows.forEach(function (r, index) {
    const tr = document.createElement("tr");

    const td_num = make_cell("col-num");
    td_num.textContent = index + 1;

    const td_year = make_cell("col-year");
    td_year.textContent = r.year === null ? "" : r.year;

    const td_authors = make_cell("col-authors");
    td_authors.textContent = r.authors_short;
    if (r.authors_short !== r.authors_full) td_authors.title = r.authors_full;

    const td_title = make_cell("col-title");
    // title_html is escaped in Python, only <sub> and <sup> tags are added
    td_title.innerHTML = r.title_html;
    if (r.confirmed_by) {
      const mark = document.createElement("small");
      mark.className = "confirmed";
      mark.textContent = "confirmed by " + r.confirmed_by;
      td_title.appendChild(mark);
    }

    const td_venue = make_cell("col-venue");
    td_venue.textContent = r.venue;
    if (r.venue_detail) {
      const small = document.createElement("small");
      small.textContent = r.venue_detail;
      td_venue.appendChild(small);
    }

    const td_type = make_cell("col-type");
    td_type.textContent = r.type_label;

    const td_doi = make_cell("col-doi");
    const link_href = r.doi_url || r.url;
    if (link_href) {
      const a = document.createElement("a");
      a.href = link_href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = r.doi || "Link";
      td_doi.appendChild(a);
    } else {
      const none = document.createElement("span");
      none.className = "missing";
      none.textContent = "none";
      td_doi.appendChild(none);
    }

    [td_num, td_year, td_authors, td_title, td_venue, td_type, td_doi].forEach(td => tr.appendChild(td));
    fragment.appendChild(tr);
  });
  body.replaceChildren(fragment);
  document.getElementById("empty_state").hidden = rows.length > 0;
}

function render_sort_headers() {
  document.querySelectorAll("th[data-key]").forEach(function (th) {
    if (th.dataset.key === state.sort_key) {
      th.setAttribute("aria-sort", state.sort_dir === "asc" ? "ascending" : "descending");
    } else {
      th.removeAttribute("aria-sort");
    }
  });
}

// ---------- main render
function render() {
  const pool = records.filter(matches_text_and_type);
  const rows = pool.filter(r => state.year === null || r.year === state.year).sort(compare_records);
  render_years(pool);
  render_table(rows);
  render_sort_headers();
  document.getElementById("count_text").textContent =
    "Showing " + rows.length + " of " + records.length + (records.length === 1 ? " publication" : " publications");
}

// ---------- events
document.querySelectorAll("th[data-key] .sort-button").forEach(function (button) {
  button.addEventListener("click", function () {
    const key = button.parentElement.dataset.key;
    if (state.sort_key === key) {
      state.sort_dir = state.sort_dir === "asc" ? "desc" : "asc";
    } else {
      state.sort_key = key;
      state.sort_dir = key === "year" ? "desc" : "asc";
    }
    render();
  });
});

let search_timer = null;
document.getElementById("search_box").addEventListener("input", function (event) {
  clearTimeout(search_timer);
  search_timer = setTimeout(function () {
    state.query = event.target.value.trim().toLowerCase();
    render();
  }, 120);
});

type_select.addEventListener("change", function () {
  state.type = type_select.value;
  render();
});

const confirmed_select = document.getElementById("confirmed_select");
// the filter is only offered when at least one paper is confirmed
if (!records.some(r => r.confirmed_by)) confirmed_select.hidden = true;
confirmed_select.addEventListener("change", function () {
  state.confirmed = confirmed_select.value;
  render();
});

function reset_filters() {
  state.query = "";
  state.year = null;
  state.type = "";
  state.confirmed = "";
  document.getElementById("search_box").value = "";
  type_select.value = "";
  confirmed_select.value = "";
  render();
}
document.getElementById("reset_button").addEventListener("click", reset_filters);
document.getElementById("empty_reset_button").addEventListener("click", reset_filters);

render();
</script>
</body>
</html>
'''


def make_summary_text(records):
    years = [record["year"] for record in records if record["year"] is not None]
    count = len(records)
    noun = "publication" if count == 1 else "publications"
    if not years:
        return "%d %s." % (count, noun)
    if min(years) == max(years):
        return "%d %s from %d." % (count, noun, min(years))
    return "%d %s from %d to %d." % (count, noun, min(years), max(years))


def render_html(records, page_title, source_name, nav_html=""):
    # json is made safe for embedding inside a script element
    records_json = json.dumps(records, ensure_ascii=False)
    records_json = records_json.replace("</", "<\\/")
    records_json = records_json.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    page = HTML_TEMPLATE
    page = page.replace("__NAV__", nav_html)
    page = page.replace("__PAGE_TITLE__", html.escape(page_title))
    page = page.replace("__SUMMARY_TEXT__", html.escape(make_summary_text(records)))
    page = page.replace("__SOURCE_NAME__", html.escape(source_name))
    page = page.replace("__BUILD_DATE__", datetime.date.today().isoformat())
    # records last, so placeholder names inside the data are never touched
    page = page.replace("__RECORDS_JSON__", records_json)
    return page


# ================================================== #
# ONE PAGE PER AUTHOR
# ================================================== #

# --- the index page uses the style of the publication page, with a plain list instead of the table
INDEX_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<style>__CSS__</style>
</head>
<body>
<div class="page">
  <header>
    __NAV__
    <h1>__PAGE_TITLE__</h1>
    <p class="summary">__SUMMARY_TEXT__</p>
  </header>
  <ul class="index-list">
__ITEMS__
  </ul>
  <footer>Generated from __SOURCE_NAME__ on __BUILD_DATE__.</footer>
</div>
</body>
</html>
"""


def nav_links(links):
    # links: (text, relative href); written as a row above the heading
    if not links:
        return ""
    return '<nav class="pages">%s</nav>' % "".join(
        '<a href="%s">%s</a>' % (html.escape(href, quote=True), html.escape(text)) for text, href in links)


def relative_link(FROM_PAGE, TO_PAGE):
    # a link that works when the folder is copied elsewhere, on Windows too
    return Path(os.path.relpath(TO_PAGE, FROM_PAGE.parent)).as_posix()


def author_name_from_bib(text, fallback):
    # 3_a_aida_master_bib.py writes "% AIDA master bibliography of Ottmar Möhler" on top
    match = re.search(r"^%\s*AIDA master bibliography of (.+?)\s*$", text, re.M)
    return match.group(1) if match else fallback.replace("_", " ")


def write_author_pages(arguments, MAIN_PAGE):
    # one page per bib file in the authors folder, then the index
    if not AUTHOR_BIB_DIR.is_dir():
        print("No author masters in %s, run 3_a_aida_master_bib.py first" % AUTHOR_BIB_DIR)
        return
    BIB_FILES = sorted(AUTHOR_BIB_DIR.glob("*.bib"))
    if not BIB_FILES:
        print("No author masters in %s" % AUTHOR_BIB_DIR)
        return

    AUTHOR_PAGES_DIR.mkdir(parents=True, exist_ok=True)
    for OLD_PAGE in AUTHOR_PAGES_DIR.glob("*.html"):
        OLD_PAGE.unlink()
    items = []
    for BIB_FILE in BIB_FILES:
        bib_text = BIB_FILE.read_text(encoding="utf-8", errors="replace")
        name = author_name_from_bib(bib_text, BIB_FILE.stem)
        records = sort_records([build_record(entry, arguments.max_authors)
                                for entry in parse_bibtex(bib_text)])
        PAGE_PATH = AUTHOR_PAGES_DIR / (BIB_FILE.stem + ".html")
        nav = nav_links([("All AIDA publications", relative_link(PAGE_PATH, MAIN_PAGE)),
                         ("All researchers", relative_link(PAGE_PATH, AUTHOR_INDEX_PATH))])
        PAGE_PATH.write_text(render_html(records, "AIDA publications of %s" % name, BIB_FILE.name, nav),
                             encoding="utf-8")
        confirmed = sum(1 for record in records if record["confirmed_by"])
        items.append((name, PAGE_PATH, len(records), confirmed))

    # --- the index, most papers first
    items.sort(key=lambda item: (-item[2], item[0]))
    rows = []
    for name, PAGE_PATH, count, confirmed in items:
        note = "%d %s" % (count, "paper" if count == 1 else "papers")
        if confirmed:
            note += ", %d confirmed" % confirmed
        rows.append('    <li><a href="%s">%s</a><small>%s</small></li>'
                    % (html.escape(relative_link(AUTHOR_INDEX_PATH, PAGE_PATH), quote=True),
                       html.escape(name), html.escape(note)))
    css = re.search(r"<style>(.*?)</style>", HTML_TEMPLATE, re.S).group(1)
    page = INDEX_TEMPLATE
    page = page.replace("__CSS__", css)
    page = page.replace("__NAV__", nav_links([("All AIDA publications",
                                               relative_link(AUTHOR_INDEX_PATH, MAIN_PAGE))]))
    page = page.replace("__PAGE_TITLE__", "AIDA researchers")
    page = page.replace("__SUMMARY_TEXT__", html.escape(
        "%d researchers, each with a page of their AIDA publications." % len(items)))
    page = page.replace("__SOURCE_NAME__", html.escape(AUTHOR_BIB_DIR.name + " folder"))
    page = page.replace("__BUILD_DATE__", datetime.date.today().isoformat())
    page = page.replace("__ITEMS__", "\n".join(rows))
    AUTHOR_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTHOR_INDEX_PATH.write_text(page, encoding="utf-8")
    print("Author pages  : %d in %s" % (len(items), AUTHOR_PAGES_DIR))
    print("Author index  : %s" % AUTHOR_INDEX_PATH)


# ================================================== #
# REPORTING
# ================================================== #

def print_report(entries, records):
    type_counts = {}
    for record in records:
        type_counts[record["type_label"]] = type_counts.get(record["type_label"], 0) + 1

    missing_year = sum(1 for record in records if record["year"] is None)
    missing_doi = sum(1 for record in records if not record["doi"])
    missing_title = sum(1 for record in records if not record["title_plain"])
    missing_author = sum(1 for record in records if not record["first_author"])
    leftover_latex = [record["key"] for record in records
                      if "\\" in record["title_plain"] or "\\" in record["authors_full"]]

    print("  entries parsed    : %d" % len(entries))
    print("  records written   : %d" % len(records))
    for label in sorted(type_counts):
        print("    %-16s: %d" % (label, type_counts[label]))
    print("  without year      : %d" % missing_year)
    print("  without DOI       : %d" % missing_doi)
    print("  without title     : %d" % missing_title)
    print("  without author    : %d" % missing_author)
    if leftover_latex:
        print("  leftover LaTeX in : %s" % ", ".join(leftover_latex[:10]))


# ================================================== #
# COMMAND LINE
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Create an interactive HTML publication list from a BibTeX file.")
    parser.add_argument("--input", type=Path, default=None,
                        help="BibTeX file to read; makes only this one page (default: %s)" % INPUT_BIB_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_HTML_PATH,
                        help="HTML file to write (default: %(default)s)")
    parser.add_argument("--title", default=page_title_default,
                        help="page title and heading (default: %(default)s)")
    parser.add_argument("--max-authors", type=int, default=max_authors_default,
                        help="authors shown before 'et al.' (default: %(default)s)")
    parser.add_argument("--json", action="store_true",
                        help="also write the parsed records as JSON next to the HTML file")
    parser.add_argument("--no-authors", action="store_true",
                        help="make only the main page, no author pages")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse and report only, write nothing")
    parser.add_argument("--verbose", action="store_true",
                        help="print a parsing report")
    return parser.parse_args()


# ================================================== #
# MAIN
# ================================================== #

def main():
    arguments = parse_arguments()
    # --- a page for one given file only, or the main page followed by the author pages
    single_page = arguments.input is not None
    INPUT_PATH = (arguments.input or INPUT_BIB_PATH).expanduser()
    OUTPUT_PATH = arguments.output.expanduser()

    # --- read
    if not INPUT_PATH.is_file():
        print("Input file not found: %s" % INPUT_PATH)
        return 1
    bib_text = INPUT_PATH.read_text(encoding="utf-8", errors="replace")

    # --- parse and build
    entries = parse_bibtex(bib_text, verbose=arguments.verbose)
    records = sort_records([build_record(entry, arguments.max_authors) for entry in entries])

    print("Read %d entries from %s" % (len(entries), INPUT_PATH))
    if arguments.verbose or arguments.dry_run:
        print_report(entries, records)

    if arguments.dry_run:
        print("Dry run, nothing written.")
        return 0

    # --- write
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with_authors = not single_page and not arguments.no_authors
    nav = nav_links([("All researchers", relative_link(OUTPUT_PATH, AUTHOR_INDEX_PATH))]) if with_authors else ""
    page = render_html(records, arguments.title, INPUT_PATH.name, nav)
    OUTPUT_PATH.write_text(page, encoding="utf-8")
    print("HTML written  : %s" % OUTPUT_PATH)
    if with_authors:
        write_author_pages(arguments, OUTPUT_PATH)

    if arguments.json:
        JSON_PATH = OUTPUT_PATH.with_suffix(".json")
        JSON_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        print("JSON written  : %s" % JSON_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
