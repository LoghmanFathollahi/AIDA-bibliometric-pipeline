#!/usr/bin/env python3
'''
1_c_aida_openalex_merge.py

   Merge the OpenAlex BibTeX harvests and take them through the whole
   selection chain in four stages, reporting what every stage removes.

Purpose

   OpenAlex is harvested for the AIDA record in two ways, by keyword and
   by researcher, so the same paper usually appears in more than one file.
   This script runs the complete chain:

      stage 1  read every .bib and merge into one master list
               nothing is removed, duplicates included
      stage 2  find duplicates, keep one copy, drop the rest
      stage 3  apply the keyword test from config.py
      stage 4  apply the KIT affiliation test from config.py

   Duplicates are linked by OpenAlex work ID, by DOI, and by normalised
   title plus year for records without a DOI.  Two records with different
   DOIs are never merged on the title alone.

   Every record keeps the file it was read from, so each stage can be
   reported per input file and per year.

Usage

   python 1_c_aida_openalex_merge.py
      run the four stages on the two default harvest folders
         <root>/data/openalex/keywords/*.bib      keyword harvests
         <root>/data/openalex/researchers/*.bib   researcher harvests
      a missing folder is reported and skipped
      <root> is the project root found by config.py, the same on Linux
      and Windows, so no path has to be given

   python 1_c_aida_openalex_merge.py ottmar_mohler.bib ice_nucleation.bib
      run on named files instead

   options
      --input-dirs DIR [DIR ...]   folders to scan for .bib files
      --bib-dir DIR                folder for openalex_master.bib
      --out-dir DIR                folder for every other output
      --prefix NAME                basename of the master files
      --no-kit-requirement         report stage 4 but keep the records
      --min-distinct N             override the keyword threshold

Output

   the master list, --bib-dir, default <root>/master_bibs/
      openalex_master.bib              the SURVIVING records only, what
                                       comes out of stage 4, records
                                       verbatim, citation keys LaTeX-safe

   everything else, --out-dir, default <root>/processed_data/openalex/
      openalex_master.csv              one row per surviving record
      openalex_all.bib                 stage 1, every record from every
                                       file, duplicates included
      openalex_all.csv                 one row per record read, each tagged
                                       with the stage that removed it
      openalex_unique.bib              stage 2, one copy per publication
      openalex_duplicates.csv          one row per COPY of every duplicated
                                       record, flagged KEPT or DROPPED
      openalex_duplicates_report.txt   the same audit trail, human readable
      openalex_no_keywords.csv         records removed by stage 3
      openalex_no_kit.csv              records removed by stage 4
      openalex_merge_summary.txt       totals, one table per input folder
                                       showing what each stage removes for
                                       each file, publications per year,
                                       and the overlap between the files

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
# every setting falls back to a local default, so the script also runs
# on its own, outside the pipeline, or with an older config.py
# the folder of this script is put on the import path, so config.py next to it
# is found from any working directory, on Linux and on Windows alike
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
try:
    import config
except ImportError:
    config = None


def setting(name: str, default):
    '''A value from config.py, or the default when config lacks it.'''

    return getattr(config, name, default)


# --- project root when config.py is missing
# the same rule as in config.py: the folder above scripts, or AIDA_ROOT
FALLBACK_ROOT = Path(os.environ.get("AIDA_ROOT") or (
    SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == "scripts" else SCRIPT_DIR))

AIDA_ROOT = Path(setting("BASE_DIR", FALLBACK_ROOT))
OPENALEX_DATA_DIR = Path(setting("OPENALEX_DATA_DIR", AIDA_ROOT / "data" / "openalex"))

DEFAULT_INPUT_DIRS = [Path(path) for path in setting(
    "OPENALEX_INPUT_DIRS",
    [OPENALEX_DATA_DIR / "keywords", OPENALEX_DATA_DIR / "researchers"])]
DEFAULT_BIB_DIR = Path(setting("MASTER_BIBS_DIR", AIDA_ROOT / "master_bibs"))
DEFAULT_OUT_DIR = Path(setting("OPENALEX_MERGE_DIR",
                               AIDA_ROOT / "processed_data" / "openalex"))


# ================================================== #
# SELECTION RULES
# ================================================== #

# the keyword terms and KIT patterns are shared with the WoS chain, so both
# sources are judged by exactly the same definitions
# only the field names differ, OpenAlex harvests use lowercase names and
# carry no abstract, so the keyword test sees the title and the concepts

keyword_terms = setting("keyword_terms",
                        ["AIDA", "ice", "nucleation", "chamber", "aerosol"])
keyword_min_distinct = setting("keyword_min_distinct", 2)
keyword_whole_word = setting("keyword_whole_word", True)
keyword_variants = setting("keyword_variants", {
    "ice": ["icy"],
    "nucleation": ["nucleating", "nucleate", "nucleated",
                   "nucleus", "nuclei"],
    "chamber": ["chambers"],
    "aerosol": ["aerosols"],
})
require_kit_affiliation = setting("require_kit_affiliation", True)
kit_affiliation_patterns = setting("kit_affiliation_patterns", [
    r"Karlsruhe Institute of Technology",
    r"Karlsruhe Inst\.? Technol",
    r"Forschungszentrum Karlsruhe",
    r"Kernforschungszentrum Karlsruhe",
    r"Research Cent(?:er|re) Karlsruhe",
    r"IMK[ .-]?AAF",
    r"Inst(?:itut|\.)? f(?:ü|u)r Meteorologie und Klimaforschung",
    r"\bKIT\b",
    r"\bFZK\b",
])
loose_karlsruhe = setting("loose_karlsruhe", False)

# --- OpenAlex specific field names, lowercase
keyword_search_fields = [name.lower() for name in setting(
    "openalex_keyword_search_fields", ["title", "abstract", "keywords"])]
affiliation_search_fields = [name.lower() for name in setting(
    "openalex_affiliation_search_fields", ["affiliation", "affiliations"])]
csv_max_field_chars = setting("openalex_csv_max_field_chars",
                              setting("wos_csv_max_field_chars", 2000))

# --- the four stages, in order
# the label a record carries once it leaves the chain
stage_labels = ["duplicate", "no_keywords", "no_kit", "final"]


# ================================================== #
# PARSING
# ================================================== #

# aida_openalex.py writes one entry per record, opened by
#     @article{mohler2008_heterogeneous,
# one field per line, and closed by a line holding a single brace
# field names are lowercased on reading, so hand edited files still match
entry_start = re.compile(r"^@(\w+)\s*\{\s*(.*?)\s*,?\s*$")
field_start = re.compile(r"^\s*([A-Za-z][A-Za-z0-9\-]*)\s*=\s*(.*)$")

# fields lifted into the CSVs
# everything else is still preserved in the .bib files
csv_fields = [
    "author", "title", "journal", "booktitle", "publisher", "year",
    "volume", "number", "pages", "issn", "affiliation", "keywords",
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
    '''Parse an OpenAlex BibTeX harvest into a list of record dicts.

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
                field_name = match_field.group(1).lower()
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
            "entry_type": entry_type.lower(),
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

# three identifiers, in this order of trust
#   1. openalex_id   OpenAlex work ID (W...), exact and authoritative
#   2. doi           lowercased DOI, for works OpenAlex split into two IDs
#   3. title         normalised title and year, only for records without DOI

openalex_id_pattern = re.compile(r"(?:openalex\.org/|OpenAlex:\s*)(W\d+)",
                                 re.I)
bare_id_pattern = re.compile(r"^\s*(W\d{5,})\s*$", re.I)


def openalex_id_of(record: dict) -> str:
    '''OpenAlex work ID, from the note field or a dedicated ID field.'''

    for field_name in ("note", "openalex", "url", "id"):
        value = record["fields"].get(field_name, "")
        match_id = (openalex_id_pattern.search(value)
                    or bare_id_pattern.match(value))
        if match_id:
            return match_id.group(1).upper()
    return ""


def doi_of(record: dict) -> str:
    '''Bare lowercased DOI, resolver prefix and trailing punctuation removed.'''

    doi = record["fields"].get("doi", "")
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = doi.replace("\\_", "_")
    doi = doi.split()[0] if doi.split() else ""
    return doi.strip().strip(".,;").lower()


def norm_title(record: dict) -> str:
    '''Title reduced to lowercase ASCII letters and digits only.'''

    title = record["fields"].get("title", "")
    title = unicodedata.normalize("NFKD", title)
    title = title.encode("ascii", "ignore").decode("ascii").lower()
    title = re.sub(r"[^a-z0-9]+", "", title)
    return title


def year_of(record: dict) -> int | None:
    '''Publication year as an integer, None if absent or unreadable.'''

    match_year = re.search(r"(1[89]\d{2}|20\d{2})",
                           record["fields"].get("year", ""))
    return int(match_year.group(1)) if match_year else None


def title_key_of(record: dict) -> str:
    '''Normalised title and year joined, empty if the title is missing.'''

    title = norm_title(record)
    if not title:
        return ""
    year = year_of(record)
    return f"{title}|{year if year is not None else ''}"


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
        body = "|".join(re.escape(form) for form in forms)
        if keyword_whole_word:
            patterns[term] = re.compile(rf"\b(?:{body})\b", re.I)
        else:
            patterns[term] = re.compile(rf"(?:{body})", re.I)
    return patterns


term_patterns = _build_term_patterns()
kit_patterns = [re.compile(pattern, re.I)
                for pattern in kit_affiliation_patterns]


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
# STAGE 1, MERGE
# ================================================== #

def stage_merge(files: list[Path]) -> tuple[list[dict], dict[str, dict]]:
    '''Read every harvest into one list, nothing removed.

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
        n_no_id = sum(1 for record in file_records
                      if not openalex_id_of(record))
        n_no_doi = sum(1 for record in file_records if not doi_of(record))
        n_no_year = sum(1 for record in file_records
                        if year_of(record) is None)

        file_stats[f"{path.parent.name}/{path.name}"] = {
            "n_records": len(file_records),
            "n_no_id": n_no_id,
            "n_no_doi": n_no_doi,
            "n_no_year": n_no_year,
        }
        print(f"    {path.name:<45} {len(file_records):>5} records "
              f"({n_no_id} without OpenAlex ID, {n_no_doi} without DOI)")
        records.extend(file_records)

    print(f"  master list: {len(records)} records")
    return records, file_stats


# ================================================== #
# STAGE 2, DEDUPLICATE
# ================================================== #

# records are linked into groups with a union find, so a chain of links
# is followed to the end: A shares an ID with B, B shares a DOI with C,
# all three become one group
# the copy kept is the richest one, i.e. the one with the most populated
# fields, ties broken by the order of the input files, then alphabetically,
# so the result is deterministic


def _link_records(records: list[dict]) -> list[int]:
    '''Group index of every record, after following all identifier links.'''

    parent = list(range(len(records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(index_a: int, index_b: int) -> None:
        root_a, root_b = find(index_a), find(index_b)
        if root_a != root_b:
            parent[max(root_a, root_b)] = min(root_a, root_b)

    # --- link on OpenAlex ID and on DOI
    first_by_id: dict[str, int] = {}
    first_by_doi: dict[str, int] = {}
    for index, record in enumerate(records):
        work_id = openalex_id_of(record)
        if work_id:
            if work_id in first_by_id:
                union(index, first_by_id[work_id])
            else:
                first_by_id[work_id] = index

        doi = doi_of(record)
        if doi:
            if doi in first_by_doi:
                union(index, first_by_doi[doi])
            else:
                first_by_doi[doi] = index

    # --- link records without a DOI on title and year
    # the anchor for each title is preferably a record that has a DOI, so a
    # DOI-less copy joins the real publication, while two records with
    # different DOIs are never joined through their title
    first_by_title: dict[str, int] = {}
    for with_doi in (True, False):
        for index, record in enumerate(records):
            if bool(doi_of(record)) != with_doi:
                continue
            title_key = title_key_of(record)
            if title_key:
                first_by_title.setdefault(title_key, index)

    for index, record in enumerate(records):
        if doi_of(record):
            continue
        title_key = title_key_of(record)
        if title_key and first_by_title[title_key] != index:
            union(index, first_by_title[title_key])

    return [find(index) for index in range(len(records))]


def _describe_group(group_records: list[dict]) -> tuple[str, str]:
    '''How a group was matched, as (key_type, key) for the reports.'''

    id_counts = Counter(openalex_id_of(record) for record in group_records)
    id_counts.pop("", None)
    doi_counts = Counter(doi_of(record) for record in group_records)
    doi_counts.pop("", None)

    # --- a singleton is described by its strongest identifier
    if len(group_records) == 1:
        record = group_records[0]
        if openalex_id_of(record):
            return "openalex_id", openalex_id_of(record)
        if doi_of(record):
            return "doi", doi_of(record)
        if title_key_of(record):
            return "title", title_key_of(record)
        return "unkeyed", f"{record['source_file']}|{record['cite_key']}"

    # --- a group lists every kind of link that holds it together
    kinds: list[str] = []
    keys: list[str] = []
    shared_ids = [work_id for work_id, count in id_counts.items() if count > 1]
    shared_dois = [doi for doi, count in doi_counts.items() if count > 1]
    if shared_ids:
        kinds.append("openalex_id")
        keys.append(sorted(shared_ids)[0])
    if shared_dois:
        kinds.append("doi")
        keys.append(sorted(shared_dois)[0])
    if any(not doi_of(record) for record in group_records):
        title_counts = Counter(title_key_of(record)
                               for record in group_records)
        title_counts.pop("", None)
        shared_titles = [title for title, count in title_counts.items()
                         if count > 1]
        if shared_titles:
            kinds.append("title")
            keys.append(sorted(shared_titles)[0])

    if not kinds:
        return "linked", group_records[0]["cite_key"]
    return "+".join(kinds), keys[0]


def stage_dedup(records: list[dict],
                file_order: list[str]) -> tuple[list[dict], list[list[dict]]]:
    '''Collapse the master list to one copy per publication.

    Returns (unique, duplicate_groups).  Every record is tagged in place:
    the survivors carry _decision KEPT, the dropped copies carry DROPPED
    and the stage label "duplicate".
    '''

    rank = {name: index for index, name in enumerate(file_order)}

    groups_by_root: dict[int, list[dict]] = defaultdict(list)
    for record, root in zip(records, _link_records(records)):
        groups_by_root[root].append(record)

    described = [(_describe_group(group_records), group_records)
                 for group_records in groups_by_root.values()]
    described.sort(key=lambda item: item[0])

    # --- pick a winner per group and tag every record
    # singletons are tagged too, so the per-file report can count without
    # walking the groups again
    unique: list[dict] = []
    dup_groups: list[list[dict]] = []

    for group_id, (key, group_records) in enumerate(described, start=1):
        group_records.sort(key=lambda record: (
            -richness(record),
            rank.get(record["source_file"], len(rank)),
            record["source_file"]))
        group_files = Counter(record["source_file"]
                              for record in group_records)
        group_dirs = sorted({record["source_dir"]
                             for record in group_records})

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
        return (-year, record["fields"].get("author", "").lower())

    unique.sort(key=sort_key)

    print(f"stage 2  {len(records)} records, {len(unique)} unique "
          f"({len(records) - len(unique)} duplicate copies dropped)")
    return unique, dup_groups


# ================================================== #
# STAGES 3 AND 4, KEYWORD AND AFFILIATION FILTER
# ================================================== #

def stage_filter(unique: list[dict],
                 min_distinct: int,
                 kit_required: bool) -> tuple[list[dict], list[dict],
                                              list[dict]]:
    '''Apply the keyword test, then the KIT test to whatever survives.

    Returns (final, no_keywords, no_kit).  Records are tagged in place with
    the stage that removed them, so nothing is counted twice.  Without the
    KIT requirement the records lacking KIT are still listed in no_kit, but
    they are tagged "final" and stay in the final list.
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
        if not record["_has_kit"]:
            no_kit.append(record)
            if kit_required:
                record["_stage_out"] = "no_kit"
                continue

        record["_stage_out"] = "final"
        final.append(record)

    n_passed = len(unique) - len(no_keywords)
    print(f"stage 3  {len(unique)} unique, {n_passed} with at least "
          f"{min_distinct} distinct terms ({len(no_keywords)} dropped)")
    if kit_required:
        print(f"stage 4  {n_passed} checked, {len(final)} with a KIT "
              f"affiliation ({len(no_kit)} dropped)")
    else:
        print(f"stage 4  {n_passed} checked, {len(no_kit)} without a KIT "
              f"affiliation, kept because the requirement is off")
    return final, no_keywords, no_kit


# ================================================== #
# WRITING BIBTEX
# ================================================== #

def _safe_key(record: dict) -> str:
    '''Citation key reduced to characters LaTeX can always cite.'''

    key = re.sub(r"[^A-Za-z0-9_-]", "", record["cite_key"])
    return key or openalex_id_of(record) or "entry"


def write_bib(records: list[dict], path: Path, note: str) -> None:
    '''Write records to a .bib file, each record verbatim.

    Keys from aida_openalex.py are unique only inside one harvest file, and
    the master list holds duplicates, so a repeated key gets a numeric
    suffix.  Without it the file would carry two entries under one key.
    '''

    seen_keys: Counter = Counter()

    with path.open("w", encoding="utf-8") as file_handle:
        file_handle.write("% AIDA / IMK-AAF, OpenAlex publication list\n")
        file_handle.write(f"% {note}\n")
        file_handle.write(f"% {len(records)} records, generated by "
                          f"aida_openalex_merge.py, "
                          f"{datetime.now():%Y-%m-%d %H:%M}\n\n")

        for record in records:
            key = _safe_key(record)
            seen_keys[key] += 1
            if seen_keys[key] > 1:
                key = f"{key}_{seen_keys[key]}"
            raw = re.sub(r"^@(\w+)\s*\{\s*[^,]*,",
                         lambda match_head: f"@{match_head.group(1)}{{{key},",
                         record["_raw"], count=1)
            file_handle.write(raw.rstrip() + "\n\n")


# ================================================== #
# WRITING CSV
# ================================================== #

def _short(value: str) -> str:
    '''Trim a very long field so the CSV stays readable.'''

    if len(value) > csv_max_field_chars:
        return value[:csv_max_field_chars] + " ..."
    return value


def _first_author(record: dict) -> str:
    '''The first name of the BibTeX author list.'''

    return record["fields"].get("author", "").split(" and ")[0]


def write_records_csv(records: list[dict], path: Path) -> None:
    '''One row per record, each tagged with the stage that removed it.

    Used for the whole stage 1 list and for the surviving records alike, so
    both CSVs carry the same columns.
    '''

    columns = (["group_id", "stage_out", "decision", "source_dir",
                "source_file", "openalex_id", "doi", "entry_type"]
               + csv_fields
               + ["n_copies", "n_terms", "terms_found", "has_kit"])

    with path.open("w", encoding="utf-8-sig", newline="") as file_handle:
        writer = csv.writer(file_handle)
        writer.writerow(columns)
        for record in records:
            terms = record.get("_terms", terms_found(record))
            has_kit = record.get("_has_kit", has_kit_affiliation(record))
            writer.writerow([record["_group_id"], record["_stage_out"],
                             record["_decision"], record["source_dir"],
                             record["source_file"],
                             openalex_id_of(record), doi_of(record),
                             record["entry_type"]]
                            + [_short(record["fields"].get(name, ""))
                               for name in csv_fields]
                            + [record["_n_copies"], len(terms),
                               "; ".join(terms), int(has_kit)])


def write_rejects_csv(records: list[dict], path: Path, reason: str) -> None:
    '''One row per record dropped, or only flagged, by stage 3 or stage 4.'''

    with path.open("w", encoding="utf-8-sig", newline="") as file_handle:
        writer = csv.writer(file_handle)
        writer.writerow(["reason", "source_dir", "source_file",
                         "openalex_id", "doi", "year", "first_author",
                         "title", "journal", "n_terms", "terms_found",
                         "has_kit", "affiliations"])
        for record in records:
            terms = record.get("_terms", [])
            writer.writerow([reason, record["source_dir"],
                             record["source_file"],
                             openalex_id_of(record), doi_of(record),
                             record["fields"].get("year", ""),
                             _first_author(record),
                             record["fields"].get("title", ""),
                             record["fields"].get("journal", "")
                             or record["fields"].get("booktitle", ""),
                             len(terms), "; ".join(terms),
                             int(record.get("_has_kit", False)),
                             _short(_joined_fields(
                                 record, affiliation_search_fields))])


def write_duplicates(dup_groups: list[list[dict]],
                     csv_path: Path,
                     txt_path: Path) -> None:
    '''Write the stage 2 audit trail as CSV and as a readable report.'''

    # --- machine readable, one row per copy
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file_handle:
        writer = csv.writer(file_handle)
        writer.writerow(["group_id", "key_type", "dedup_key", "n_copies",
                         "files_involved", "decision", "source_dir",
                         "source_file", "openalex_id", "doi", "year",
                         "journal", "first_author", "title", "n_fields"])
        for group in dup_groups:
            files = "; ".join(f"{name} x{count}" for name, count
                              in sorted(group[0]["_files"].items()))
            for record in group:
                writer.writerow([record["_group_id"], record["_key_type"],
                                 record["_key"], record["_n_copies"], files,
                                 record["_decision"], record["source_dir"],
                                 record["source_file"],
                                 openalex_id_of(record), doi_of(record),
                                 record["fields"].get("year", ""),
                                 record["fields"].get("journal", ""),
                                 _first_author(record),
                                 record["fields"].get("title", ""),
                                 record["_richness"]])

    # --- human readable, largest groups first
    with txt_path.open("w", encoding="utf-8") as file_handle:
        file_handle.write("Stage 2: duplicate records in the master list\n")
        file_handle.write("=" * 70 + "\n\n")

        if not dup_groups:
            file_handle.write("No duplicates found.\n")
            return

        total_dropped = sum(len(group) - 1 for group in dup_groups)
        file_handle.write(f"{len(dup_groups)} duplicated publications, "
                          f"{total_dropped} copies removed.\n")
        file_handle.write("Copy kept = the one with the most populated "
                          "fields.\n\n")

        for group in sorted(dup_groups, key=lambda group: -len(group)):
            head = group[0]
            file_handle.write(f"[group {head['_group_id']}] "
                              f"x{head['_n_copies']} "
                              f"(matched on {head['_key_type']}: "
                              f"{head['_key']})\n")
            file_handle.write(f"  {head['fields'].get('year', '')}: "
                              f"{head['fields'].get('title', '')[:100]}\n")
            for record in group:
                file_handle.write(f"    {record['_decision']:<8} "
                                  f"{record['source_file']}"
                                  f"   ({record['_richness']} fields)\n")
            file_handle.write("\n")


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
        file_records = [record for record in records
                        if record["source_file"] == label]

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
            "n_no_id": stats["n_no_id"],
            "n_no_doi": stats["n_no_doi"],
            "n_no_year": stats["n_no_year"],
            "n_duplicates": n_duplicates,
            "n_unique": stats["n_records"] - n_duplicates,
            "n_no_keywords": stage_count["no_keywords"],
            "n_no_kit": stage_count["no_kit"],
            "n_final": stage_count["final"],
            "n_intra_dup": len({record["_group_id"] for record in file_records
                                if record["_files"][label] > 1}),
            "n_only_here": sum(1 for group_id in group_ids
                               if files_of_group[group_id] == {label}),
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
    known_years = sorted(year for year in per_year if year is not None)
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


def _write_table(file_handle, title: str, rows: list[dict], label_key: str,
                 label_header: str, with_bar: bool = False) -> None:
    '''Write one table: a header, a line per row, and a TOTAL line.'''

    file_handle.write(f"\n{title}\n")

    if not rows:
        file_handle.write("  nothing to report here\n")
        return

    header = f"{label_header:<{table_name_width}}"
    header += "".join(f"{label:>{width}}"
                      for label, _, width in table_columns)
    file_handle.write("-" * table_width + "\n")
    file_handle.write(header + "\n")
    file_handle.write("-" * table_width + "\n")

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
        file_handle.write(line.rstrip() + "\n")

    file_handle.write("-" * table_width + "\n")
    total = f"{'TOTAL (' + str(len(rows)) + ' rows)':<{table_name_width}}"
    total += "".join(f"{sum(row[key] for row in rows):>{width}}"
                     for _, key, width in table_columns)
    file_handle.write(total + "\n")


def write_merge_summary(records: list[dict],
                        unique: list[dict],
                        final: list[dict],
                        no_keywords: list[dict],
                        no_kit: list[dict],
                        dup_groups: list[list[dict]],
                        file_stats: dict[str, dict],
                        kit_required: bool,
                        min_distinct: int,
                        path: Path) -> None:
    '''Write the readable overview of the whole four-stage run.'''

    report = file_report(records, file_stats)
    by_key_type = Counter(group[0]["_key_type"] for group in dup_groups)
    years = [year for year in (year_of(record) for record in final)
             if year is not None]

    with path.open("w", encoding="utf-8") as file_handle:
        file_handle.write("AIDA / IMK-AAF, OpenAlex merge summary\n")
        file_handle.write(f"generated by aida_openalex_merge.py, "
                          f"{datetime.now():%Y-%m-%d %H:%M}\n")
        file_handle.write("=" * table_width + "\n\n")

        # --- what each stage did
        # one line per stage: what came out of it, and what it removed
        kit_note = (f"{len(no_kit)} dropped" if kit_required
                    else f"{len(no_kit)} without KIT, kept, "
                         f"requirement off")
        stage_lines = [
            (f"1 records read from {len(file_stats)} files",
             len(records), ""),
            ("2 unique after deduplication", len(unique),
             f"{len(records) - len(unique)} copies dropped, "
             f"{len(dup_groups)} publications affected"),
            (f"3 with at least {min_distinct} distinct terms",
             len(unique) - len(no_keywords),
             f"{len(no_keywords)} dropped"),
            ("4 after the KIT affiliation test", len(final), kit_note),
        ]
        file_handle.write("STAGES\n")
        for label, value, note in stage_lines:
            line = f"  {label:<40}{value:>6}"
            file_handle.write(line + (f"   ({note})\n" if note else "\n"))
        if by_key_type:
            matched = ", ".join(f"{key_type} {count}" for key_type, count
                                in sorted(by_key_type.items()))
            file_handle.write(f"  duplicates matched on {matched}\n")
        if years:
            file_handle.write(f"  final list spans {min(years)} to "
                              f"{max(years)}\n")
        file_handle.write(f"  keyword test searched the fields "
                          f"{keyword_search_fields}\n")

        # --- what the columns of the tables mean
        file_handle.write("\ncolumns, read from left to right as a funnel\n")
        file_handle.write("  records   records the file started with in "
                          "stage 1\n")
        file_handle.write("  -dup      stage 2, removed as a duplicate copy, "
                          "the paper is kept from another file or entry\n")
        file_handle.write("  =unique   what reached openalex_unique.bib\n")
        file_handle.write(f"  -no kw    stage 3, removed for holding fewer "
                          f"than {min_distinct} distinct terms of "
                          f"{keyword_terms}\n")
        file_handle.write("  -no KIT   stage 4, removed for showing no KIT "
                          "affiliation\n")
        file_handle.write("  =final    what reached openalex_master.bib\n")
        file_handle.write("\nevery row adds up: records - dup - no kw - "
                          "no KIT = final\n")

        # --- one table per input folder, every file of that folder listed
        source_dirs = list(dict.fromkeys(row["source_dir"]
                                         for row in report.values()))
        for source_dir in source_dirs:
            rows = [row for row in report.values()
                    if row["source_dir"] == source_dir]
            _write_table(file_handle, f"{source_dir.upper()} HARVESTS", rows,
                         "name", "file")

        # --- publications per year, same columns
        file_handle.write("\n" + "=" * table_width + "\n")
        _write_table(file_handle, "PUBLICATIONS PER YEAR, the bar shows the "
                     "final column", year_report(records), "year", "year",
                     with_bar=True)

        # --- which files cover the same publications
        file_handle.write("\n" + "=" * table_width + "\n")
        file_handle.write("OVERLAP BETWEEN THE FILES\n")
        for label, row in report.items():
            file_handle.write(f"\n  {label}\n")
            file_handle.write(f"    without OpenAlex ID {row['n_no_id']}, "
                              f"without DOI {row['n_no_doi']}, "
                              f"without year {row['n_no_year']}, "
                              f"duplicated inside the file "
                              f"{row['n_intra_dup']}\n")
            file_handle.write(f"    found in this file only "
                              f"{row['n_only_here']}\n")
            for other, count in row["shared"].most_common():
                file_handle.write(f"    shared with {other:<45} "
                                  f"{count:>5}\n")

        # --- how the final list is distributed over the folders
        file_handle.write("\n" + "=" * table_width + "\n")
        file_handle.write("FINAL LIST BY SOURCE FOLDER\n")
        by_dirs = Counter(" + ".join(record["_dirs"]) for record in final)
        for label, count in by_dirs.most_common():
            file_handle.write(f"  {label:<45} {count:>5}\n")


# ================================================== #
# COMMAND LINE
# ================================================== #

def parse_args() -> argparse.Namespace:
    '''Command line interface.'''

    parser = argparse.ArgumentParser(
        description="Merge, deduplicate and filter OpenAlex harvests.")
    parser.add_argument("files", nargs="*", type=Path,
                        help="explicit .bib files "
                             "(default: all .bib in --input-dirs)")
    parser.add_argument("--input-dirs", type=Path, nargs="+",
                        default=DEFAULT_INPUT_DIRS,
                        help="folders to scan for .bib files (default: "
                             "openalex/keywords and openalex/researchers)")
    parser.add_argument("--bib-dir", type=Path, default=DEFAULT_BIB_DIR,
                        help=f"folder for openalex_master.bib, created if "
                             f"missing (default: {DEFAULT_BIB_DIR})")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                        help=f"folder for every other output, created if "
                             f"missing (default: {DEFAULT_OUT_DIR})")
    parser.add_argument("--prefix", default="openalex_master",
                        help="basename for the master files "
                             "(default: openalex_master)")
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
        missing = [path for path in files if not path.is_file()]
        if missing:
            sys.exit("File(s) not found: "
                     + ", ".join(str(path) for path in missing))
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
    files = [path for path in files if path.name != f"{args.prefix}.bib"]
    if not files:
        sys.exit("No .bib files found in: "
                 + ", ".join(str(directory) for directory in args.input_dirs))

    args.bib_dir.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    kit_required = require_kit_affiliation and not args.no_kit_requirement

    # --- stage 1, read and merge
    records, file_stats = stage_merge(files)
    write_bib(records, args.out_dir / "openalex_all.bib",
              "stage 1, every record from every harvest, "
              "duplicates included")

    # --- stage 2, deduplicate
    # the labels must match "source_file" in the records, folder included,
    # otherwise the file-order tie-break silently never applies
    file_order = [f"{path.parent.name}/{path.name}" for path in files]
    unique, dup_groups = stage_dedup(records, file_order)
    write_bib(unique, args.out_dir / "openalex_unique.bib",
              "stage 2, one copy per publication")
    write_duplicates(dup_groups,
                     args.out_dir / "openalex_duplicates.csv",
                     args.out_dir / "openalex_duplicates_report.txt")

    # --- stages 3 and 4, keyword and affiliation filter
    final, no_keywords, no_kit = stage_filter(unique, args.min_distinct,
                                              kit_required)
    write_bib(final, args.bib_dir / f"{args.prefix}.bib",
              "the surviving records, what comes out of stage 4")
    write_rejects_csv(no_keywords, args.out_dir / "openalex_no_keywords.csv",
                      "no_keywords")
    write_rejects_csv(no_kit, args.out_dir / "openalex_no_kit.csv",
                      "no_kit" if kit_required else "no_kit_kept")

    # --- the reports over all four stages
    # the master CSV lists the survivors, openalex_all.csv every record read
    write_records_csv(final, args.out_dir / f"{args.prefix}.csv")
    write_records_csv(records, args.out_dir / "openalex_all.csv")
    write_merge_summary(records, unique, final, no_keywords, no_kit,
                        dup_groups, file_stats, kit_required,
                        args.min_distinct,
                        args.out_dir / "openalex_merge_summary.txt")

    # --- what was written where
    print(f"\nWritten to {args.bib_dir}/")
    print(f"  {args.prefix + '.bib':<32}{len(final):>6} surviving records")
    print(f"Written to {args.out_dir}/")
    print(f"  {args.prefix + '.csv':<32}{len(final):>6} rows")
    print(f"  {'openalex_all.bib':<32}{len(records):>6} records")
    print(f"  {'openalex_all.csv':<32}{len(records):>6} rows")
    print(f"  {'openalex_unique.bib':<32}{len(unique):>6} records")
    print(f"  {'openalex_no_keywords.csv':<32}{len(no_keywords):>6} rows")
    print(f"  {'openalex_no_kit.csv':<32}{len(no_kit):>6} rows")
    for name in ("openalex_duplicates.csv", "openalex_duplicates_report.txt",
                 "openalex_merge_summary.txt"):
        print(f"  {name}")


if __name__ == "__main__":
    main()
