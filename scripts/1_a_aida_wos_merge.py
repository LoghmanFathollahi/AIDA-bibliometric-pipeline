#!/usr/bin/env python3
'''
1_a_aida_wos_merge.py

Merge the Web of Science BibTeX exports and take them through the whole
selection chain in four stages, reporting what every stage removes.

Purpose
    WoS is queried in two ways for the AIDA record, by topic / instrument
    keyword and by researcher name, so the same paper usually appears in
    more than one export.  This script runs the complete chain:

        stage 1  read every .bib and merge into one master list
                 nothing is removed, duplicates included
        stage 2  find duplicates, keep one copy, drop the rest
        stage 3  apply the keyword test from config.py
        stage 4  apply the KIT affiliation test from config.py

    Every record keeps the file it was read from, so each stage can be
    reported per input file and per year.

Usage
    python 1_a_aida_wos_merge.py
        run the four stages on the two default export folders
            <root>/data/webofscience/keywords/*.bib      topic searches
            <root>/data/webofscience/researchers/*.bib   author searches
        <root> is the project root found by config.py, the same on Linux
        and Windows, so no path has to be given

    python 1_a_aida_wos_merge.py Wagner_R_.bib "AIDA_aerosol.bib"
        run on named files instead

    options
        --input-dirs DIR [DIR ...]   folders to scan for .bib files
        --bib-dir DIR                folder for wos_master.bib
        --out-dir DIR                folder for every other output
        --prefix NAME                basename of the master files
        --no-kit-requirement         report stage 4 but do not remove
        --min-distinct N             override the keyword threshold

Output
    the master list, --bib-dir, default <root>/master_bibs/
        wos_master.bib              the SURVIVING records only, what comes
                                    out of stage 4, records verbatim,
                                    citation keys made LaTeX-safe

    everything else, --out-dir, default <root>/processed_data/wos/
        wos_master.csv              one row per surviving record
        wos_all.bib                 stage 1, every record from every file,
                                    duplicates included
        wos_all.csv                 one row per record read, each tagged
                                    with the stage that removed it
        wos_unique.bib              stage 2, one copy per publication
        wos_duplicates.csv          one row per COPY of every duplicated
                                    record, flagged KEPT / DROPPED
        wos_duplicates_report.txt   the same audit trail, human readable
        wos_no_keywords.csv         records removed by stage 3
        wos_no_kit.csv              records removed by stage 4
        wos_merge_summary.txt       totals, one table per input folder
                                    showing what each stage removes for
                                    each file, publications per year, and
                                    the overlap between the files

    both folders are created if they do not exist
    a progress and statistics report is printed to stdout
'''

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


# ================================================== #
# PROJECT PATHS
# ================================================== #

# config.py is the single source of truth for the project layout
# the folder of this script is put on the import path, so config.py next to it
# is found from any working directory, on Linux and on Windows alike
# the fallback keeps the script usable on its own, outside the pipeline
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
try:
    import config
except ImportError:
    config = None

if config is not None:
    DEFAULT_INPUT_DIRS = config.WOS_INPUT_DIRS
    DEFAULT_BIB_DIR = config.MASTER_BIBS_DIR
    DEFAULT_OUT_DIR = config.WOS_DIR
else:
    # the same rule as in config.py: the folder above scripts, or AIDA_ROOT
    AIDA_ROOT = Path(os.environ.get("AIDA_ROOT") or (
        SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == "scripts" else SCRIPT_DIR))
    DEFAULT_INPUT_DIRS = [
        AIDA_ROOT / "data" / "webofscience" / "keywords",
        AIDA_ROOT / "data" / "webofscience" / "researchers",
    ]
    DEFAULT_BIB_DIR = AIDA_ROOT / "master_bibs"
    DEFAULT_OUT_DIR = AIDA_ROOT / "processed_data" / "wos"


# ================================================== #
# SELECTION RULES
# ================================================== #

# the keyword and affiliation rules live in config.py, so this script and
# any later script read exactly the same definitions

if config is not None:
    keyword_terms = config.keyword_terms
    keyword_min_distinct = config.keyword_min_distinct
    keyword_search_fields = config.keyword_search_fields
    keyword_whole_word = config.keyword_whole_word
    keyword_variants = config.keyword_variants
    require_kit_affiliation = config.require_kit_affiliation
    affiliation_search_fields = config.affiliation_search_fields
    kit_affiliation_patterns = config.kit_affiliation_patterns
    loose_karlsruhe = config.loose_karlsruhe
    csv_max_field_chars = config.wos_csv_max_field_chars
else:
    keyword_terms = ["AIDA", "ice", "nucleation", "chamber", "aerosol"]
    keyword_min_distinct = 2
    keyword_search_fields = ["Title", "Abstract", "Keywords", "Keywords-Plus"]
    keyword_whole_word = True
    keyword_variants = {
        "ice": ["icy"],
        "nucleation": ["nucleating", "nucleate", "nucleated",
                       "nucleus", "nuclei"],
        "chamber": ["chambers"],
        "aerosol": ["aerosols"],
    }
    require_kit_affiliation = True
    affiliation_search_fields = ["Affiliation", "Affiliations"]
    kit_affiliation_patterns = [
        r"Karlsruhe Institute of Technology",
        r"Karlsruhe Inst\.? Technol",
        r"Forschungszentrum Karlsruhe",
        r"Kernforschungszentrum Karlsruhe",
        r"Research Cent(?:er|re) Karlsruhe",
        r"IMK[ .-]?AAF",
        r"Inst(?:itut|\.)? f(?:ü|u)r Meteorologie und Klimaforschung",
        r"\bKIT\b",
        r"\bFZK\b",
    ]
    loose_karlsruhe = False
    csv_max_field_chars = 2000

# --- the four stages, in order
# the label a record carries once it leaves the chain
stage_labels = ["duplicate", "no_keywords", "no_kit", "final"]


# ================================================== #
# PARSING
# ================================================== #

# WoS writes one entry per record, opened by
#     @article{ WOS:000267082200011,
# and closed by a line holding nothing but a single brace
entry_start = re.compile(r"^@(\w+)\s*\{\s*(.*?)\s*,?\s*$")
field_start = re.compile(r"^\s*([A-Za-z][A-Za-z0-9\-]*)\s*=\s*(.*)$")

# fields lifted into the CSVs
# everything else is still preserved in the .bib files
csv_fields = [
    "Author", "Title", "Journal", "Year", "Volume", "Number", "Pages",
    "DOI", "ISSN", "Type", "Times-Cited", "Affiliations", "Keywords",
]


def _clean(value: str) -> str:
    '''Collapse a multi-line BibTeX value into a single tidy string.'''

    value = value.strip()

    # --- drop the closing brace and comma left over from the raw text
    value = re.sub(r"[}\s,]+$", "", value)
    value = re.sub(r"^\{", "", value)

    # --- fold the continuation lines into one line
    value = re.sub(r"\s*\n\s*", " ", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip()


def parse_bib(path: Path) -> list[dict]:
    '''Parse a WoS BibTeX export into a list of record dicts.

    Each record keeps its verbatim source text under "_raw", so every .bib
    this script writes avoids a lossy round trip through a parser.
    '''

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()

    records: list[dict] = []
    line_no = 0
    n_lines = len(lines)

    while line_no < n_lines:
        match_entry = entry_start.match(lines[line_no])
        if not match_entry:
            line_no += 1
            continue

        entry_type, cite_key = match_entry.group(1), match_entry.group(2)
        raw_lines = [lines[line_no]]
        line_no += 1

        fields: dict[str, str] = {}
        field_name: str | None = None
        field_value: list[str] = []
        depth = 0

        def flush() -> None:
            '''Store the field collected so far.'''
            if field_name is not None:
                fields[field_name] = _clean("\n".join(field_value))

        while line_no < n_lines:
            line = lines[line_no]

            # --- a lone brace at depth zero closes the record
            if depth == 0 and line.strip() == "}":
                raw_lines.append(line)
                line_no += 1
                break
            raw_lines.append(line)

            # --- a new field starts only outside a braced value
            match_field = field_start.match(line) if depth == 0 else None
            if match_field:
                flush()
                field_name = match_field.group(1)
                first_part = match_field.group(2)
                field_value = [first_part]
                depth = first_part.count("{") - first_part.count("}")
            else:
                field_value.append(line)
                depth += line.count("{") - line.count("}")
            depth = max(depth, 0)
            line_no += 1

        flush()

        # the label carries the parent folder, so files of the same name in
        # keywords/ and researchers/ stay distinguishable in the report
        records.append({
            "entry_type": entry_type,
            "cite_key": cite_key,
            "fields": fields,
            "_raw": "\n".join(raw_lines),
            "source_file": f"{path.parent.name}/{path.name}",
            "source_dir": path.parent.name,
        })

    return records


# ================================================== #
# RECORD IDENTIFIERS
# ================================================== #

# three keys, applied in this order of trust
#   1. ut     WoS accession number (WOS:000...), exact and authoritative
#   2. doi    lowercased DOI, for records exported under different UTs
#   3. title  normalised title and year, last-resort net for records with
#             neither a usable UT nor a DOI


def ut_of(record: dict) -> str:
    '''WoS accession number, from the citation key or the UT field.'''

    for candidate in (record["cite_key"],
                      record["fields"].get("Unique-ID", ""),
                      record["fields"].get("UT", "")):
        match_ut = re.search(r"WOS:\s*([A-Za-z0-9]+)", candidate)
        if match_ut:
            return "WOS:" + match_ut.group(1)
    return ""


def doi_of(record: dict) -> str:
    '''Bare lowercased DOI, resolver prefix and trailing punctuation removed.'''

    doi = record["fields"].get("DOI", "")
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = doi.split()[0] if doi.split() else ""
    return doi.strip().strip(".,;").lower()


def norm_title(record: dict) -> str:
    '''Title reduced to lowercase ASCII letters and digits only.'''

    title = record["fields"].get("Title", "")
    title = unicodedata.normalize("NFKD", title)
    title = title.encode("ascii", "ignore").decode("ascii").lower()
    title = re.sub(r"[^a-z0-9]+", "", title)
    return title


def year_of(record: dict) -> int | None:
    '''Publication year as an integer, None if absent or unreadable.'''

    match_year = re.search(r"(1[89]\d{2}|20\d{2})",
                           record["fields"].get("Year", ""))
    return int(match_year.group(1)) if match_year else None


def dedup_key(record: dict) -> tuple[str, str]:
    '''Return (key_type, key) from the most trustworthy identifier present.'''

    ut = ut_of(record)
    if ut:
        return "ut", ut

    doi = doi_of(record)
    if doi:
        return "doi", doi

    title = norm_title(record)
    if title:
        return "title", f"{title}|{record['fields'].get('Year', '')}"

    return "unkeyed", f"{record['source_file']}|{record['cite_key']}"


def richness(record: dict) -> int:
    '''How complete a record is, used to decide which copy to keep.'''

    return sum(1 for value in record["fields"].values() if value)


# ================================================== #
# SELECTION TESTS
# ================================================== #

def _build_term_patterns() -> dict[str, re.Pattern]:
    '''One compiled pattern per keyword term, its variants folded in.'''

    patterns: dict[str, re.Pattern] = {}
    for term in keyword_terms:
        forms = [term] + list(keyword_variants.get(term, []))
        body = "|".join(re.escape(f) for f in forms)
        if keyword_whole_word:
            patterns[term] = re.compile(rf"\b(?:{body})\b", re.I)
        else:
            patterns[term] = re.compile(rf"(?:{body})", re.I)
    return patterns


term_patterns = _build_term_patterns()
kit_patterns = [re.compile(p, re.I) for p in kit_affiliation_patterns]


def _joined_fields(record: dict, field_names: list[str]) -> str:
    '''All the named fields of a record as one searchable string.'''

    return " ".join(record["fields"].get(name, "") for name in field_names)


def terms_found(record: dict) -> list[str]:
    '''Which of the keyword terms appear in the searched fields.'''

    text = _joined_fields(record, keyword_search_fields)
    return [term for term, pattern in term_patterns.items()
            if pattern.search(text)]


def has_kit_affiliation(record: dict) -> bool:
    '''True if any author of the record sits at KIT.'''

    text = _joined_fields(record, affiliation_search_fields)
    if not text.strip():
        return False
    if loose_karlsruhe and re.search(r"Karlsruhe", text, re.I):
        return True
    return any(pattern.search(text) for pattern in kit_patterns)


# ================================================== #
# STAGE 1 -- MERGE
# ================================================== #

def stage_merge(files: list[Path]) -> tuple[list[dict], dict[str, dict]]:
    '''Read every export into one list, nothing removed.

    Returns (records, file_stats), file_stats holding what only the reading
    step knows: how many records each file had and how many lacked an
    identifier.
    '''

    records: list[dict] = []
    file_stats: dict[str, dict] = {}

    print(f"stage 1  reading {len(files)} file(s)")
    last_dir = None
    for path in files:
        if path.parent != last_dir:
            print(f"  [{path.parent}]")
            last_dir = path.parent

        file_records = parse_bib(path)
        n_no_ut = sum(1 for r in file_records if not ut_of(r))
        n_no_doi = sum(1 for r in file_records if not doi_of(r))
        n_no_year = sum(1 for r in file_records if year_of(r) is None)

        file_stats[f"{path.parent.name}/{path.name}"] = {
            "n_records": len(file_records),
            "n_no_ut": n_no_ut,
            "n_no_doi": n_no_doi,
            "n_no_year": n_no_year,
        }
        print(f"    {path.name:<45} {len(file_records):>5} records "
              f"({n_no_ut} without UT, {n_no_doi} without DOI)")
        records.extend(file_records)

    print(f"  master list: {len(records)} records")
    return records, file_stats


# ================================================== #
# STAGE 2 -- DEDUPLICATE
# ================================================== #

# the copy kept is the richest one, i.e. the one with the most populated
# fields, because a topic-filtered export sometimes omits fields that an
# author-based export includes
# ties are broken by the order of the input files, then alphabetically, so
# the result is deterministic


def stage_dedup(records: list[dict],
                file_order: list[str]) -> tuple[list[dict], list[list[dict]]]:
    '''Collapse the master list to one copy per publication.

    Returns (unique, duplicate_groups).  Every record is tagged in place:
    the survivors carry _decision KEPT, the dropped copies carry DROPPED
    and the stage label "duplicate".
    '''

    rank = {name: index for index, name in enumerate(file_order)}

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        groups[dedup_key(record)].append(record)

    # --- fold DOI and title groups into an existing UT group
    # a record keyed by UT and another keyed by DOI can still be the same
    # paper, so the weaker keys are mapped onto the authoritative one
    doi_to_key: dict[str, tuple[str, str]] = {}
    title_to_key: dict[str, tuple[str, str]] = {}
    for key, group_records in list(groups.items()):
        if key[0] != "ut":
            continue
        for record in group_records:
            doi = doi_of(record)
            if doi:
                doi_to_key.setdefault(doi, key)
            title = norm_title(record)
            if title:
                year = record["fields"].get("Year", "")
                title_to_key.setdefault(f"{title}|{year}", key)

    for key in [k for k in groups if k[0] in ("doi", "title")]:
        target = (doi_to_key.get(key[1]) if key[0] == "doi"
                  else title_to_key.get(key[1]))
        if target and target in groups:
            groups[target].extend(groups.pop(key))

    # --- pick a winner per group and tag every record
    # singletons are tagged too, so the per-file report can count without
    # walking the groups again
    unique: list[dict] = []
    dup_groups: list[list[dict]] = []

    for group_id, (key, group_records) in enumerate(sorted(groups.items()),
                                                    start=1):
        group_records.sort(key=lambda r: (-richness(r),
                                          rank.get(r["source_file"], 99),
                                          r["source_file"]))
        group_files = Counter(r["source_file"] for r in group_records)
        group_dirs = sorted({r["source_dir"] for r in group_records})

        for position, record in enumerate(group_records):
            record["_group_id"] = group_id
            record["_key_type"], record["_key"] = key
            record["_n_copies"] = len(group_records)
            record["_files"] = group_files
            record["_dirs"] = group_dirs
            record["_richness"] = richness(record)
            record["_decision"] = "KEPT" if position == 0 else "DROPPED"
            if position > 0:
                record["_stage_out"] = "duplicate"

        unique.append(group_records[0])
        if len(group_records) > 1:
            dup_groups.append(group_records)

    # --- newest first, then by first author
    def sort_key(record: dict) -> tuple:
        year = year_of(record) or 0
        return (-year, record["fields"].get("Author", "").lower())

    unique.sort(key=sort_key)

    print(f"stage 2  {len(records)} records -> {len(unique)} unique "
          f"({len(records) - len(unique)} duplicate copies dropped)")
    return unique, dup_groups


# ================================================== #
# STAGES 3 AND 4 -- KEYWORD AND AFFILIATION FILTER
# ================================================== #

def stage_filter(unique: list[dict],
                 min_distinct: int,
                 kit_required: bool) -> tuple[list[dict], list[dict],
                                              list[dict]]:
    '''Apply the keyword test, then the KIT test to whatever survives.

    Returns (final, no_keywords, no_kit).  Records are tagged in place with
    the stage that removed them, so nothing is counted twice.
    '''

    no_keywords: list[dict] = []
    no_kit: list[dict] = []
    final: list[dict] = []

    for record in unique:
        record["_terms"] = terms_found(record)
        record["_has_kit"] = has_kit_affiliation(record)

        # --- stage 3, the distinct-term threshold
        if len(record["_terms"]) < min_distinct:
            record["_stage_out"] = "no_keywords"
            no_keywords.append(record)
            continue

        # --- stage 4, at least one KIT author
        if kit_required and not record["_has_kit"]:
            record["_stage_out"] = "no_kit"
            no_kit.append(record)
            continue

        record["_stage_out"] = "final"
        final.append(record)

    print(f"stage 3  {len(unique)} unique -> "
          f"{len(unique) - len(no_keywords)} with at least {min_distinct} "
          f"distinct terms ({len(no_keywords)} dropped)")
    print(f"stage 4  {len(unique) - len(no_keywords)} -> {len(final)} "
          f"with a KIT affiliation ({len(no_kit)} dropped"
          f"{'' if kit_required else ', reported only'})")
    return final, no_keywords, no_kit


# ================================================== #
# WRITING BIBTEX
# ================================================== #

def _safe_key(record: dict) -> str:
    '''Citation key without the colon, which LaTeX cannot cite.'''

    key = ut_of(record) or record["cite_key"]
    return re.sub(r"[^A-Za-z0-9_-]", "", key.replace(":", ""))


def write_bib(records: list[dict], path: Path, note: str) -> None:
    '''Write records to a .bib file, each record verbatim.

    The master list holds duplicates, and duplicates share a WoS accession
    number, so a repeated key gets a numeric suffix.  Without it the file
    would carry two entries under one citation key.
    '''

    seen_keys: Counter = Counter()

    with path.open("w", encoding="utf-8") as fh:
        fh.write("% AIDA / IMK-AAF -- Web of Science publication list\n")
        fh.write(f"% {note}\n")
        fh.write(f"% {len(records)} records, generated by "
                 f"aida_wos_merge.py, {datetime.now():%Y-%m-%d %H:%M}\n\n")

        for record in records:
            key = _safe_key(record)
            seen_keys[key] += 1
            if seen_keys[key] > 1:
                key = f"{key}_{seen_keys[key]}"
            raw = re.sub(r"^@(\w+)\s*\{\s*[^,]*,",
                         lambda m: f"@{m.group(1)}{{{key},",
                         record["_raw"], count=1)
            fh.write(raw.rstrip() + "\n\n")


# ================================================== #
# WRITING CSV
# ================================================== #

def _short(value: str) -> str:
    '''Trim a very long field so the CSV stays readable.'''

    if len(value) > csv_max_field_chars:
        return value[:csv_max_field_chars] + " ..."
    return value


def write_records_csv(records: list[dict], path: Path) -> None:
    '''One row per record, each tagged with the stage that removed it.

    Used for the whole stage 1 list and for the surviving records alike, so
    both CSVs carry the same columns.
    '''

    columns = (["group_id", "stage_out", "decision", "source_dir",
                "source_file", "wos_ut", "doi"]
               + [c.lower().replace("-", "_") for c in csv_fields]
               + ["n_copies", "n_terms", "terms_found", "has_kit"])

    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for record in records:
            terms = record.get("_terms", terms_found(record))
            writer.writerow([record["_group_id"], record["_stage_out"],
                             record["_decision"], record["source_dir"],
                             record["source_file"],
                             ut_of(record), doi_of(record)]
                            + [_short(record["fields"].get(c, ""))
                               for c in csv_fields]
                            + [record["_n_copies"], len(terms),
                               "; ".join(terms),
                               int(record.get("_has_kit",
                                              has_kit_affiliation(record)))])


def write_rejects_csv(records: list[dict], path: Path, reason: str) -> None:
    '''One row per record dropped by stage 3 or stage 4.'''

    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["reason", "source_dir", "source_file", "wos_ut",
                         "doi", "year", "first_author", "title", "journal",
                         "n_terms", "terms_found", "has_kit", "affiliations"])
        for record in records:
            terms = record.get("_terms", [])
            first_author = record["fields"].get("Author", "").split(" and ")[0]
            writer.writerow([reason, record["source_dir"],
                             record["source_file"],
                             ut_of(record), doi_of(record),
                             record["fields"].get("Year", ""),
                             first_author,
                             record["fields"].get("Title", ""),
                             record["fields"].get("Journal", ""),
                             len(terms), "; ".join(terms),
                             int(record.get("_has_kit", False)),
                             _short(_joined_fields(
                                 record, affiliation_search_fields))])


def write_duplicates(dup_groups: list[list[dict]],
                     csv_path: Path,
                     txt_path: Path) -> None:
    '''Write the stage 2 audit trail as CSV and as a readable report.'''

    # --- machine readable, one row per copy
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["group_id", "key_type", "dedup_key", "n_copies",
                         "files_involved", "decision", "source_dir",
                         "source_file", "wos_ut", "doi", "year", "journal",
                         "first_author", "title", "n_fields"])
        for group in dup_groups:
            files = "; ".join(f"{name} x{count}" for name, count
                              in sorted(group[0]["_files"].items()))
            for record in group:
                first_author = record["fields"].get("Author",
                                                    "").split(" and ")[0]
                writer.writerow([record["_group_id"], record["_key_type"],
                                 record["_key"], record["_n_copies"], files,
                                 record["_decision"], record["source_dir"],
                                 record["source_file"],
                                 ut_of(record), doi_of(record),
                                 record["fields"].get("Year", ""),
                                 record["fields"].get("Journal", ""),
                                 first_author,
                                 record["fields"].get("Title", ""),
                                 record["_richness"]])

    # --- human readable, largest groups first
    with txt_path.open("w", encoding="utf-8") as fh:
        fh.write("Stage 2 -- duplicate records in the master list\n")
        fh.write("=" * 70 + "\n\n")

        if not dup_groups:
            fh.write("No duplicates found.\n")
            return

        total_dropped = sum(len(g) - 1 for g in dup_groups)
        fh.write(f"{len(dup_groups)} duplicated publications, "
                 f"{total_dropped} copies removed.\n")
        fh.write("Copy kept = the one with the most populated WoS fields.\n\n")

        for group in sorted(dup_groups, key=lambda g: -len(g)):
            head = group[0]
            fh.write(f"[group {head['_group_id']}] x{head['_n_copies']} "
                     f"(matched on {head['_key_type']}: {head['_key']})\n")
            fh.write(f"  {head['fields'].get('Year', '')} -- "
                     f"{head['fields'].get('Title', '')[:100]}\n")
            for record in group:
                fh.write(f"    {record['_decision']:<8} "
                         f"{record['source_file']}"
                         f"   ({record['_richness']} fields)\n")
            fh.write("\n")


# ================================================== #
# WRITING THE SUMMARY
# ================================================== #

# --- table layout, one column per stage of the chain
# the removal columns are subtracted from left to right
table_columns = [
    ("records", "n_records", 9),
    ("-dup", "n_duplicates", 7),
    ("=unique", "n_unique", 9),
    ("-no kw", "n_no_keywords", 8),
    ("-no KIT", "n_no_kit", 9),
    ("=final", "n_final", 8),
]
table_name_width = 44
table_width = table_name_width + sum(width for _, _, width in table_columns)


def file_report(records: list[dict],
                file_stats: dict[str, dict]) -> dict[str, dict]:
    '''One row of numbers per input file, keyed by "folder/name".

    A record counts for the file it was READ from, and each record carries
    exactly one stage label, so every row adds up:
    records - dup - no keywords - no KIT = final.
    '''

    # --- map files to groups and back, so overlaps can be counted per file
    groups_of_file: dict[str, set[int]] = defaultdict(set)
    files_of_group: dict[int, set[str]] = defaultdict(set)
    for record in records:
        groups_of_file[record["source_file"]].add(record["_group_id"])
        files_of_group[record["_group_id"]].add(record["source_file"])

    # --- count the stage labels per file
    per_file_stage: dict[str, Counter] = defaultdict(Counter)
    for record in records:
        per_file_stage[record["source_file"]][record["_stage_out"]] += 1

    report: dict[str, dict] = {}
    for label, stats in file_stats.items():
        stage_count = per_file_stage[label]
        group_ids = groups_of_file.get(label, set())
        file_records = [r for r in records if r["source_file"] == label]

        shared = Counter()
        for group_id in group_ids:
            for other in files_of_group[group_id]:
                if other != label:
                    shared[other] += 1

        n_duplicates = stage_count["duplicate"]
        report[label] = {
            "source_dir": label.split("/")[0],
            "name": label.split("/", 1)[1],
            "n_records": stats["n_records"],
            "n_no_ut": stats["n_no_ut"],
            "n_no_doi": stats["n_no_doi"],
            "n_no_year": stats["n_no_year"],
            "n_duplicates": n_duplicates,
            "n_unique": stats["n_records"] - n_duplicates,
            "n_no_keywords": stage_count["no_keywords"],
            "n_no_kit": stage_count["no_kit"],
            "n_final": stage_count["final"],
            "n_intra_dup": len({r["_group_id"] for r in file_records
                                if r["_files"][label] > 1}),
            "n_only_here": sum(1 for gid in group_ids
                               if files_of_group[gid] == {label}),
            "shared": shared,
        }
    return report


def year_report(records: list[dict]) -> list[dict]:
    '''Publications per year, counted at each stage of the chain.

    Records with no readable year are collected in a last row, so the
    columns still add up to the totals.
    '''

    per_year: dict[int | None, Counter] = defaultdict(Counter)
    for record in records:
        per_year[year_of(record)][record["_stage_out"]] += 1
        per_year[year_of(record)]["records"] += 1

    rows = []
    known_years = sorted(y for y in per_year if y is not None)
    for year in known_years + ([None] if None in per_year else []):
        stage_count = per_year[year]
        rows.append({
            "year": str(year) if year is not None else "unknown",
            "n_records": stage_count["records"],
            "n_duplicates": stage_count["duplicate"],
            "n_unique": stage_count["records"] - stage_count["duplicate"],
            "n_no_keywords": stage_count["no_keywords"],
            "n_no_kit": stage_count["no_kit"],
            "n_final": stage_count["final"],
        })
    return rows


def _write_table(fh, title: str, rows: list[dict], label_key: str,
                 label_header: str, with_bar: bool = False) -> None:
    '''Write one table: a header, a line per row, and a TOTAL line.'''

    fh.write(f"\n{title}\n")

    if not rows:
        fh.write("  nothing to report here\n")
        return

    header = f"{label_header:<{table_name_width}}"
    header += "".join(f"{label:>{width}}"
                      for label, _, width in table_columns)
    fh.write("-" * table_width + "\n")
    fh.write(header + "\n")
    fh.write("-" * table_width + "\n")

    # the bar shows the final column, scaled to the largest row
    max_final = max((row["n_final"] for row in rows), default=0)

    for row in rows:
        name = str(row[label_key])
        if len(name) > table_name_width - 2:
            name = name[:table_name_width - 3] + "~"
        line = f"{name:<{table_name_width}}"
        line += "".join(f"{row[key]:>{width}}"
                        for _, key, width in table_columns)
        if with_bar and max_final:
            line += "  " + "#" * round(30 * row["n_final"] / max_final)
        fh.write(line.rstrip() + "\n")

    fh.write("-" * table_width + "\n")
    total = f"{'TOTAL (' + str(len(rows)) + ' rows)':<{table_name_width}}"
    total += "".join(f"{sum(r[key] for r in rows):>{width}}"
                     for _, key, width in table_columns)
    fh.write(total + "\n")


def write_merge_summary(records: list[dict],
                        unique: list[dict],
                        final: list[dict],
                        dup_groups: list[list[dict]],
                        file_stats: dict[str, dict],
                        kit_required: bool,
                        min_distinct: int,
                        path: Path) -> None:
    '''Write the readable overview of the whole four-stage run.'''

    report = file_report(records, file_stats)
    by_key_type = Counter(g[0]["_key_type"] for g in dup_groups)
    years = [y for y in (year_of(r) for r in final) if y is not None]

    with path.open("w", encoding="utf-8") as fh:
        fh.write("AIDA / IMK-AAF -- Web of Science merge summary\n")
        fh.write(f"generated by aida_wos_merge.py, "
                 f"{datetime.now():%Y-%m-%d %H:%M}\n")
        fh.write("=" * table_width + "\n\n")

        # --- what each stage did
        # one line per stage: what came out of it, and what it removed
        n_no_keywords = sum(1 for r in unique
                            if r["_stage_out"] == "no_keywords")
        n_no_kit = sum(1 for r in unique if r["_stage_out"] == "no_kit")
        stage_lines = [
            (f"1 records read from {len(file_stats)} files",
             len(records), ""),
            ("2 unique after deduplication", len(unique),
             f"{len(records) - len(unique)} copies dropped, "
             f"{len(dup_groups)} publications affected"),
            (f"3 with at least {min_distinct} distinct terms",
             len(unique) - n_no_keywords, f"{n_no_keywords} dropped"),
            ("4 with a KIT affiliation", len(final),
             f"{n_no_kit} dropped"
             f"{'' if kit_required else ', reported only'}"),
        ]
        fh.write("STAGES\n")
        for label, value, note in stage_lines:
            line = f"  {label:<40}{value:>6}"
            fh.write(line + (f"   ({note})\n" if note else "\n"))
        if by_key_type:
            matched = ", ".join(f"{k} {v}" for k, v
                                in sorted(by_key_type.items()))
            fh.write(f"  duplicates matched on {matched}\n")
        if years:
            fh.write(f"  final list spans {min(years)} to {max(years)}\n")

        # --- what the columns of the tables mean
        fh.write("\ncolumns, read from left to right as a funnel\n")
        fh.write("  records   records the file started with in stage 1\n")
        fh.write("  -dup      stage 2, removed as a duplicate copy, the "
                 "paper is kept from another file or entry\n")
        fh.write("  =unique   what reached wos_unique.bib\n")
        fh.write(f"  -no kw    stage 3, removed for holding fewer than "
                 f"{min_distinct} distinct terms of {keyword_terms}\n")
        fh.write("  -no KIT   stage 4, removed for showing no KIT "
                 "affiliation\n")
        fh.write("  =final    what reached wos_master.bib\n")
        fh.write("\nevery row adds up: records - dup - no kw - no KIT "
                 "= final\n")

        # --- one table per input folder, every file of that folder listed
        source_dirs = list(dict.fromkeys(row["source_dir"]
                                         for row in report.values()))
        for source_dir in source_dirs:
            rows = [row for row in report.values()
                    if row["source_dir"] == source_dir]
            _write_table(fh, f"{source_dir.upper()} EXPORTS", rows,
                         "name", "file")

        # --- publications per year, same columns
        fh.write("\n" + "=" * table_width + "\n")
        _write_table(fh, "PUBLICATIONS PER YEAR, the bar shows the final "
                     "column", year_report(records), "year", "year",
                     with_bar=True)

        # --- which files cover the same publications
        fh.write("\n" + "=" * table_width + "\n")
        fh.write("OVERLAP BETWEEN THE FILES\n")
        for label, row in report.items():
            fh.write(f"\n  {label}\n")
            fh.write(f"    without UT {row['n_no_ut']}, "
                     f"without DOI {row['n_no_doi']}, "
                     f"without year {row['n_no_year']}, "
                     f"duplicated inside the file {row['n_intra_dup']}\n")
            fh.write(f"    found in this file only {row['n_only_here']}\n")
            for other, count in row["shared"].most_common():
                fh.write(f"    shared with {other:<45} {count:>5}\n")

        # --- how the final list is distributed over the folders
        fh.write("\n" + "=" * table_width + "\n")
        fh.write("FINAL LIST BY SOURCE FOLDER\n")
        by_dirs = Counter(" + ".join(r["_dirs"]) for r in final)
        for label, count in by_dirs.most_common():
            fh.write(f"  {label:<45} {count:>5}\n")


# ================================================== #
# COMMAND LINE
# ================================================== #

def parse_args() -> argparse.Namespace:
    '''Command line interface.'''

    parser = argparse.ArgumentParser(
        description="Merge, deduplicate and filter Web of Science exports.")
    parser.add_argument("files", nargs="*", type=Path,
                        help="explicit .bib files "
                             "(default: all .bib in --input-dirs)")
    parser.add_argument("--input-dirs", type=Path, nargs="+",
                        default=DEFAULT_INPUT_DIRS,
                        help="folders to scan for .bib files (default: "
                             "webofscience/keywords and .../researchers)")
    parser.add_argument("--bib-dir", type=Path, default=DEFAULT_BIB_DIR,
                        help=f"folder for wos_master.bib, created if missing "
                             f"(default: {DEFAULT_BIB_DIR})")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                        help=f"folder for every other output, created if "
                             f"missing (default: {DEFAULT_OUT_DIR})")
    parser.add_argument("--prefix", default="wos_master",
                        help="basename for the master files "
                             "(default: wos_master)")
    parser.add_argument("--min-distinct", type=int,
                        default=keyword_min_distinct,
                        help=f"stage 3 threshold, how many different "
                             f"keyword terms a record needs "
                             f"(default: {keyword_min_distinct})")
    parser.add_argument("--no-kit-requirement", action="store_true",
                        help="report stage 4 but keep the records anyway")
    return parser.parse_args()


def collect_files(args: argparse.Namespace) -> list[Path]:
    '''Resolve the .bib files to read, from the command line or the folders.'''

    if args.files:
        files = list(args.files)
        missing = [f for f in files if not f.is_file()]
        if missing:
            sys.exit("File(s) not found: "
                     + ", ".join(str(m) for m in missing))
        return files

    files: list[Path] = []
    for directory in args.input_dirs:
        if not directory.is_dir():
            print(f"! input folder missing, skipped: {directory}",
                  file=sys.stderr)
            continue
        found = sorted(directory.glob("*.bib"))
        if not found:
            print(f"! no .bib files in: {directory}", file=sys.stderr)
        files.extend(found)
    return files


# ================================================== #
# MAIN
# ================================================== #

def main() -> None:
    args = parse_args()

    files = collect_files(args)

    # never read a master list produced by an earlier run
    files = [f for f in files if f.name != f"{args.prefix}.bib"]
    if not files:
        sys.exit("No .bib files found in: "
                 + ", ".join(str(d) for d in args.input_dirs))

    args.bib_dir.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    kit_required = require_kit_affiliation and not args.no_kit_requirement

    # --- stage 1, read and merge
    records, file_stats = stage_merge(files)
    write_bib(records, args.out_dir / "wos_all.bib",
              "stage 1, every record from every export, duplicates included")

    # --- stage 2, deduplicate
    # the labels must match "source_file" in the records, folder included,
    # otherwise the file-order tie-break silently never applies
    file_order = [f"{p.parent.name}/{p.name}" for p in files]
    unique, dup_groups = stage_dedup(records, file_order)
    write_bib(unique, args.out_dir / "wos_unique.bib",
              "stage 2, one copy per publication")
    write_duplicates(dup_groups,
                     args.out_dir / "wos_duplicates.csv",
                     args.out_dir / "wos_duplicates_report.txt")

    # --- stages 3 and 4, keyword and affiliation filter
    final, no_keywords, no_kit = stage_filter(unique, args.min_distinct,
                                              kit_required)
    if not kit_required:
        final = final + no_kit
    write_bib(final, args.bib_dir / f"{args.prefix}.bib",
              "the surviving records, what comes out of stage 4")
    write_rejects_csv(no_keywords, args.out_dir / "wos_no_keywords.csv",
                      "no_keywords")
    write_rejects_csv(no_kit, args.out_dir / "wos_no_kit.csv", "no_kit")

    # --- the reports over all four stages
    # the master CSV lists the survivors, wos_all.csv every record read
    write_records_csv(final, args.out_dir / f"{args.prefix}.csv")
    write_records_csv(records, args.out_dir / "wos_all.csv")
    write_merge_summary(records, unique, final, dup_groups, file_stats,
                        kit_required, args.min_distinct,
                        args.out_dir / "wos_merge_summary.txt")

    # --- what was written where
    print(f"\nWritten to {args.bib_dir}/")
    print(f"  {args.prefix}.bib        {len(final):>5} surviving records")
    print(f"Written to {args.out_dir}/")
    print(f"  {args.prefix}.csv        {len(final):>5} rows")
    print(f"  wos_all.bib             {len(records):>5} records")
    print(f"  wos_all.csv             {len(records):>5} rows")
    print(f"  wos_unique.bib          {len(unique):>5} records")
    print(f"  wos_no_keywords.csv     {len(no_keywords):>5} rows")
    print(f"  wos_no_kit.csv          {len(no_kit):>5} rows")
    for name in ("wos_duplicates.csv", "wos_duplicates_report.txt",
                 "wos_merge_summary.txt"):
        print(f"  {name}")


if __name__ == "__main__":
    main()
