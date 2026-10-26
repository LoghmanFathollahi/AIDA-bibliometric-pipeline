'''
2_b_aida_ottmar_check.py

   Completes and checks the DOI of every AIDA publication in Ottmar Möhler's
   own publication list.

Purpose

   2_a_aida_ottmar_list.py keeps the AIDA publications Ottmar marked in
   green. Only about a third of them carry a DOI in the text. Before the
   bib file is made by 2_c_aida_ottmar_bib.py, every entry gets its DOI,
   from the most to the least reliable source:

      1  doi_in_list   the DOI is written in his list
      2  own_harvest   a record in data/openalex or data/webofscience with
                       the same first author, year and title
      3  crossref      the Crossref search, accepted only when first author,
                       year and title all agree with his citation
      4  not_found     nothing reliable, the DOI is left empty

   A match is checked against the whole citation text, so his title does
   not have to be cut out of the citation exactly. A weaker match is kept
   with status "check" and should be looked at by hand.
   Discussion papers (for example ACPD) are used only when no final article
   is found, and are always marked "check".
   Two entries with the same DOI are marked "check", unless their citations
   are identical: then Ottmar listed the paper twice, and both stay "found".

   The result is a table that can be opened in Excel. A DOI found by hand is
   written into the doi column with method "manual"; such rows are kept
   unchanged in every later run. Crossref answers are cached, so a second
   run needs no internet.

Usage

   python 2_b_aida_ottmar_check.py
      complete and check the DOI list

   python 2_b_aida_ottmar_check.py --no-crossref
      no internet needed: his list, the own harvests and earlier Crossref
      answers from the cache are used, nothing new is asked

   python 2_b_aida_ottmar_check.py --refresh
      ask Crossref again, also for entries already in the cache

   All paths come from config.py, the same on Linux and Windows.

Output

   <root>/data/author_self_correction/Ottmar_Moehler_AIDA_dois.csv   one row per publication
   <root>/data/author_self_correction/cache/crossref_ottmar.json     cached Crossref answers
   a summary on the screen: how many DOIs each source gave
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import csv
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
from difflib import SequenceMatcher
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
SELF_CORRECTION_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_DIR",
                                   DATA_DIR / "author_self_correction"))
INPUT_DOCX = Path(getattr(config, "OTTMAR_AIDA_FILE",
                          SELF_CORRECTION_DIR / "Ottmar_Moehler_AIDA_publications.docx"))
DOI_CSV = Path(getattr(config, "OTTMAR_DOI_FILE",
                       SELF_CORRECTION_DIR / "Ottmar_Moehler_AIDA_dois.csv"))
CACHE_DIR = Path(getattr(config, "AUTHOR_SELF_CORRECTION_CACHE_DIR", SELF_CORRECTION_DIR / "cache"))
CROSSREF_CACHE = CACHE_DIR / "crossref_ottmar.json"
HARVEST_DIRS = [Path(HARVEST_DIR) for HARVEST_DIR in getattr(
    config, "BIB_SOURCE_DIRS", [DATA_DIR / "openalex", DATA_DIR / "webofscience"])]

# --- matching thresholds
title_accept = float(getattr(config, "doi_title_accept", 0.90))
title_check = float(getattr(config, "doi_title_check", 0.80))

# --- Crossref
crossref_url = "https://api.crossref.org/works"
crossref_mailto = getattr(config, "crossref_mailto", "loghman.fathollahi@kit.edu")
crossref_rows = 5
request_timeout = int(getattr(config, "request_timeout", 30))
request_pause = float(getattr(config, "request_pause", 0.2))

# --- table layout
csv_columns = ["number", "doi", "method", "status", "score", "year", "first_author",
               "matched_title", "matched_journal", "citation", "note"]

# --- patterns
doi_pattern = re.compile(r"10\.\d{4,9}/[^\s,;\"<>]+", re.IGNORECASE)
# Copernicus discussion papers, three forms:
#   old discussion journals   10.5194/acpd-15-1-2015
#   preprints                 10.5194/acp-2019-123   (a year right after the journal code)
#   EGUsphere                 10.5194/egusphere-2023-123
# the discussion journals are listed by name, because final journals such as
# gmd (Geoscientific Model Development) and esd also end in "d"
discussion_journals = ["acpd", "amtd", "bgd", "cpd", "essdd", "esdd", "gmdd", "hessd", "nhessd",
                       "osd", "sed", "tcd", "npgd", "angeod", "esurfd", "gchrond", "wesd"]
discussion_pattern = re.compile(r"^10\.5194/(?:(?:%s)-\d|egusphere-|[a-z]+-\d{4}-\d+$)"
                                % "|".join(discussion_journals), re.IGNORECASE)
year_pattern = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")


# ================================================== #
# TEXT HELPERS
# ================================================== #

def fold(text):
    # lower case ASCII: Möhler, Moehler and Mohler all become "mohler"
    text = (text or "").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char)).lower()
    return text.replace("ae", "a").replace("oe", "o").replace("ue", "u")


def squash(text):
    # letters and digits only, used to compare titles and citations
    return re.sub(r"[^a-z0-9]", "", fold(text))


def clean_doi(text):
    # bare lower case DOI, resolver prefix and trailing punctuation removed
    match = doi_pattern.search((text or "").replace("\\_", "_"))
    return match.group(0).rstrip(".)]").lower() if match else ""


def is_discussion(doi):
    return bool(discussion_pattern.search(doi or ""))


def surname_of(name):
    # surname from one author name in any common form
    #   "Kamm, S."  "Möhler, Ottmar"  "Eva Sommer"  "Bhattacharyya N"  "C. Xenofontos"  "Kara D. Lamb et al."
    name = re.sub(r"\bet al\.?", "", name or "").strip(" .,;")
    if "," in name:
        return name.split(",")[0].strip()
    words = name.split()
    if not words:
        return ""
    # "Bhattacharyya N": the last word is only initials, so the surname comes first
    if len(words) > 1 and re.fullmatch(r"[A-Z]{1,3}\.?", words[-1]):
        return words[0]
    return words[-1]


def title_cover(title, citation):
    # share of the title that appears, in order, inside the citation text
    # 1.0 means every letter of the title is found in the citation
    title_key, citation_key = squash(title), squash(citation)
    if len(title_key) < 15:
        return 0.0
    if title_key in citation_key:
        return 1.0
    blocks = SequenceMatcher(None, title_key, citation_key, autojunk=False).get_matching_blocks()
    # short scattered pieces are noise, only runs of four or more letters count
    return sum(block.size for block in blocks if block.size >= 4) / len(title_key)


# ================================================== #
# READING OTTMAR'S LIST
# ================================================== #

def read_entries(INPUT_PATH):
    # one entry per numbered paragraph; "79 Wagner, R." without a dot counts too
    entries = []
    for paragraph in Document(str(INPUT_PATH)).paragraphs:
        text = " ".join(paragraph.text.split())
        match = re.match(r"^(\d{1,4})\.?\s+(.*)$", text)
        if not match:
            continue
        citation = match.group(2)
        year_match = re.search(r"\((19\d\d|20\d\d)\)", citation) or year_pattern.search(citation)
        # the first author is everything before the first comma:
        # "Kamm, S., ..." gives Kamm, "Eva Sommer, ..." gives Sommer
        first_author = surname_of(citation.split(",")[0])
        entries.append({
            "number": int(match.group(1)),
            "citation": citation,
            "doi_in_list": clean_doi(citation),
            "year": int(year_match.group(1)) if year_match else None,
            "first_author": first_author,
        })
    return entries


# ================================================== #
# OWN HARVESTS
# ================================================== #

def bib_entries(text):
    # small brace aware BibTeX reader, enough for the harvest files of 1_a to 1_c
    for chunk in re.split(r"\n\s*@", "\n" + text)[1:]:
        fields = {}
        for match in re.finditer(r"([A-Za-z][\w-]*)\s*=\s*\{", chunk):
            depth, start, index = 1, match.end(), match.end()
            while index < len(chunk) and depth:
                depth += {"{": 1, "}": -1}.get(chunk[index], 0)
                index += 1
            fields[match.group(1).lower()] = " ".join(chunk[start:index - 1].split())
        yield fields


def load_harvest():
    # index of every harvested record with a DOI, by folded first author surname
    index = defaultdict(list)
    file_count = 0
    for HARVEST_DIR in HARVEST_DIRS:
        if not HARVEST_DIR.is_dir():
            continue
        for BIB_FILE in HARVEST_DIR.rglob("*.bib"):
            file_count += 1
            text = BIB_FILE.read_text(encoding="utf-8-sig", errors="replace")
            for fields in bib_entries(text):
                doi = clean_doi(fields.get("doi", ""))
                title = fields.get("title", "")
                authors = re.split(r"\s+and\s+", fields.get("author", ""))
                if not (doi and title and authors[0]):
                    continue
                year_match = year_pattern.search(fields.get("year", ""))
                index[fold(surname_of(authors[0]))].append({
                    "doi": doi, "title": title,
                    "year": int(year_match.group(1)) if year_match else None,
                    "journal": fields.get("journal", "") or fields.get("booktitle", ""),
                    "where": BIB_FILE.name,
                })
    return index, file_count


# ================================================== #
# JUDGING A CANDIDATE
# ================================================== #

def judge(entry, title, years, surnames):
    # returns (status, score); status is "found", "check" or ""
    # surnames: the candidate's author surnames, first author first
    score = title_cover(title, entry["citation"])
    folded = [fold(name) for name in surnames if name]
    wanted = fold(entry["first_author"])
    author_first = bool(folded) and folded[0] == wanted
    author_any = wanted in folded
    year_exact = entry["year"] is not None and entry["year"] in years
    year_near = entry["year"] is not None and any(abs(entry["year"] - year) <= 1 for year in years)

    if score >= title_accept and author_first and year_exact:
        return "found", score
    if score >= title_check and (author_first or author_any) and year_near:
        return "check", score
    return "", score


status_rank = {"found": 2, "check": 1}


def best_of(candidates):
    # final articles before discussion papers, then found before check, then the score
    rank = status_rank
    candidates = [item for item in candidates if item["status"]]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (not is_discussion(item["doi"]), rank[item["status"]],
                                      item["score"]), reverse=True)
    best = dict(candidates[0])
    if is_discussion(best["doi"]):
        best["status"] = "check"
        best["note"] = "only a discussion paper was found, look for the final article"
    return best


def match_harvest(entry, index):
    candidates = []
    for record in index.get(fold(entry["first_author"]), []):
        years = [record["year"]] if record["year"] else []
        status, score = judge(entry, record["title"], years, [entry["first_author"]])
        candidates.append({"doi": record["doi"], "status": status, "score": score,
                           "title": record["title"], "journal": record["journal"],
                           "note": "from %s" % record["where"]})
    return best_of(candidates)


# ================================================== #
# CROSSREF
# ================================================== #

def load_cache():
    try:
        return json.loads(CROSSREF_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_PATH = CROSSREF_CACHE.with_suffix(".tmp")
    TEMP_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    TEMP_PATH.replace(CROSSREF_CACHE)


def crossref_search(citation):
    # the whole citation is sent; Crossref is good at reading reference strings
    params = {"query.bibliographic": citation[:900], "rows": crossref_rows,
              "select": "DOI,title,author,issued,published-print,published-online,container-title,type",
              "mailto": crossref_mailto}
    url = crossref_url + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "AIDA-bibliometrics/1.0 (mailto:%s)" % crossref_mailto}
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            time.sleep(request_pause)
            return payload.get("message", {}).get("items", [])
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
    return []


def item_years(item):
    years = set()
    for key in ("issued", "published-print", "published-online"):
        for parts in (item.get(key) or {}).get("date-parts") or []:
            if parts and parts[0]:
                years.add(int(parts[0]))
    return sorted(years)


def match_crossref(entry, items):
    candidates = []
    for item in items:
        # only real publications, not peer review reports, components or datasets
        if item.get("type") not in ("journal-article", "proceedings-article", "book-chapter",
                                    "posted-content", "report", "book"):
            continue
        title = " ".join(item.get("title") or [])
        surnames = [author.get("family", "") for author in item.get("author") or []]
        status, score = judge(entry, title, item_years(item), surnames)
        candidates.append({"doi": (item.get("DOI") or "").lower(), "status": status,
                           "score": score, "title": title,
                           "journal": " ".join(item.get("container-title") or []), "note": ""})
    return best_of(candidates)


# ================================================== #
# THE TABLE
# ================================================== #

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


def manual_rows():
    # rows marked "manual" by hand survive every run unchanged
    kept = {}
    for row in read_csv_rows(DOI_CSV):
        if (row.get("method") or "").strip().lower() == "manual" and (row.get("number") or "").strip():
            row["doi"] = clean_doi(row.get("doi", "")) or (row.get("doi") or "").strip()
            # a DOI written by hand is a decision, so it counts as found
            row["status"] = "found" if row["doi"] else "not_found"
            kept[int(row["number"])] = row
    return kept


def write_table(rows):
    DOI_CSV.parent.mkdir(parents=True, exist_ok=True)
    TEMP_PATH = DOI_CSV.with_suffix(".tmp")
    with open(TEMP_PATH, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    try:
        TEMP_PATH.replace(DOI_CSV)
    except PermissionError:
        sys.exit("Cannot write %s, it is probably open in Excel. Close it and run again." % DOI_CSV)


# ================================================== #
# MAIN
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Complete and check the DOI of every AIDA publication of Ottmar Möhler.")
    parser.add_argument("--input", type=Path, default=INPUT_DOCX,
                        help="output of 2_a_aida_ottmar_list.py (default: %(default)s)")
    parser.add_argument("--no-crossref", action="store_true",
                        help="no internet: list, harvests and cached answers only")
    parser.add_argument("--refresh", action="store_true", help="ask Crossref again, ignore the cache")
    return parser.parse_args()


def build_doi_table(arguments):
    # one DOI per publication in Ottmar's list
    if not arguments.input.is_file():
        sys.exit("Not found: %s\nRun 2_a_aida_ottmar_list.py first." % arguments.input)

    entries = read_entries(arguments.input)
    print("Ottmar's AIDA list: %d publications" % len(entries))
    duplicates = [number for number, count in Counter(e["number"] for e in entries).items() if count > 1]
    if duplicates:
        print("   WARNING: list numbers used twice: %s" % duplicates)

    manual = manual_rows()
    index, harvest_files = load_harvest()
    print("Own harvests: %d bib files, %d authors indexed" % (harvest_files, len(index)))
    # the cache is read also with --no-crossref, only new questions are skipped
    cache = {} if arguments.refresh else load_cache()
    use_crossref = not arguments.no_crossref
    crossref_failed = False

    rows = []
    for entry in entries:
        base = {"number": entry["number"], "year": entry["year"] or "",
                "first_author": entry["first_author"], "citation": entry["citation"]}

        # --- 0, a DOI set by hand is never touched
        if entry["number"] in manual:
            row = manual[entry["number"]]
            row.update({key: base[key] for key in base if not row.get(key)})
            rows.append(row)
            continue

        # --- 1, the DOI written in his list
        if entry["doi_in_list"]:
            rows.append(dict(base, doi=entry["doi_in_list"], method="doi_in_list",
                             status="check" if is_discussion(entry["doi_in_list"]) else "found",
                             score="", note="discussion paper DOI in the list"
                             if is_discussion(entry["doi_in_list"]) else ""))
            continue

        # --- 2, own harvests
        best = match_harvest(entry, index)
        method = "own_harvest"

        # --- 3, Crossref, when the harvest gave nothing certain
        if best is None or best["status"] != "found":
            key = str(entry["number"])
            if key not in cache and use_crossref and not crossref_failed:
                try:
                    cache[key] = crossref_search(entry["citation"])
                except (urllib.error.URLError, TimeoutError, ValueError) as error:
                    print("   Crossref not reachable (%s), continuing without it" % error)
                    crossref_failed = True
            if key in cache:
                found = match_crossref(entry, cache[key])
                if found and (best is None or (status_rank[found["status"]], found["score"])
                              > (status_rank[best["status"]], best["score"])):
                    best, method = found, "crossref"

        # --- 4, nothing reliable
        if best is None:
            rows.append(dict(base, doi="", method="", status="not_found", score="",
                             note="add the DOI by hand and set method to manual"))
            continue
        rows.append(dict(base, doi=best["doi"], method=method, status=best["status"],
                         score="%.2f" % best["score"], matched_title=best["title"],
                         matched_journal=best["journal"], note=best.get("note", "")))

    if use_crossref:
        save_cache(cache)

    # --- a DOI found for two entries is suspicious, both are marked
    # unless the two citations are identical: then Ottmar listed the paper twice,
    # and the bib file simply keeps one entry with both numbers
    rows_by_doi = defaultdict(list)
    for row in rows:
        if row["doi"]:
            rows_by_doi[row["doi"]].append(row)
    for doi, group in rows_by_doi.items():
        if len(group) < 2:
            continue
        numbers = ", ".join("#%s" % row["number"] for row in group)
        identical = len({squash(row["citation"]) for row in group}) == 1
        for row in group:
            if identical:
                row["note"] = "listed twice in his list (%s), one entry in the bib" % numbers
            else:
                row["status"] = "check"
                row["note"] = ("same DOI as %s; " % numbers + (row.get("note") or "")).strip("; ")

    rows.sort(key=lambda row: -int(row["number"]))
    write_table(rows)

    # --- summary
    print("")
    print("DOI by source")
    for method, count in Counter(row["method"] or "none" for row in rows).most_common():
        print("   %-12s %4d" % (method, count))
    print("Status")
    for status, count in Counter(row["status"] for row in rows).most_common():
        print("   %-12s %4d" % (status, count))
    print("")
    print("Written: %s" % DOI_CSV)
    if any(row["status"] in ("check", "not_found") for row in rows):
        print("Open it, look at the rows with status check or not_found, write the right DOI")
        print("into the doi column and set method to manual. Then run this script again.")


def main():
    build_doi_table(parse_arguments())


if __name__ == "__main__":
    main()
