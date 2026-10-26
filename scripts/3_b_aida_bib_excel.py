'''
3_b_aida_bib_excel.py

   Writes the AIDA publication statistics as Excel workbooks: one for every
   researcher of AIDA Scientists.txt and one master workbook for all of AIDA.

Purpose

   3_b_aida_bib_authors.py collects, for every paper of the master
   bibliography, its authors in order, the corresponding authors and the
   institution of every author with its country (from OpenAlex and from
   Web of Science addresses). This script loads that same dataset, from
   the caches 3_b wrote, so it needs no internet and gives the same numbers
   as the HTML pages.

   Every researcher workbook holds these sheets:

      Summary         all counts at a glance, each linked to its sheet
      Papers          every paper: year, title, journal, DOI, type, his position
                      and role, his affiliation on it, confirmed by
      Affiliations    his own institutions, with the text as printed on the papers
      Co-authors      everyone he published with, joint papers and years
      Institutions    every institution on his papers, his own and his co-authors'
      Countries       the countries of those institutions
      Journals        where he published
      Per year        papers per year, split by role
      Data coverage   which papers have affiliation and role data, and which not

   The master workbook has the same sheets for all papers, plus Researchers:
   one row per AIDA researcher with all his counts side by side, and Authors:
   every author of every paper.

   Roles follow 3_b: first author and corresponding, first author only,
   corresponding but not first, co-author. Papers an author confirmed in his
   own list (aida_confirmed_by in the master bib) are marked in every sheet.

Usage

   python 3_b_aida_bib_excel.py
      all researcher workbooks and the master workbook

   python 3_b_aida_bib_excel.py --only "Robert Wagner"
      one researcher only, can be repeated; the master workbook is skipped

   python 3_b_aida_bib_excel.py --dry-run
      read and count, write nothing

   Run 3_a_aida_master_bib.py and 3_b_aida_bib_authors.py first; this script
   reuses the OpenAlex cache of 3_b. All paths come from config.py, the same
   on Linux and Windows. Close the workbooks in Excel before a new run.

Output

   <root>/output/excel/AIDA_Master.xlsx                 all of AIDA (Excel)
   <root>/output/excel/authors/<First_Last>.xlsx        one per researcher (Excel)
   researchers without a paper in the master get no file; the screen says why
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import datetime
import importlib.util
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.hyperlink import Hyperlink
except ImportError:
    sys.exit("openpyxl is missing, install it with:  python -m pip install openpyxl")

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
INPUT_BIB_PATH = Path(getattr(config, "MASTER_BIB_FILE", MASTER_DIR / "master" / "AIDA_Master_bib.bib"))
AUTHORS_SCRIPT_PATH = Path(getattr(config, "AUTHORS_SCRIPT_FILE", SCRIPT_DIR / "3_b_aida_bib_authors.py"))
AUTHORS_DIR = Path(getattr(config, "AUTHORS_HTML_DIR", BASE_DIR / "output" / "authors"))
RESEARCHERS_FILE = Path(getattr(config, "RESEARCHERS_FILE", BASE_DIR / "data" / "AIDA Scientists.txt"))
EXCEL_DIR = Path(getattr(config, "EXCEL_DIR", BASE_DIR / "output" / "excel"))
EXCEL_AUTHORS_DIR = Path(getattr(config, "EXCEL_AUTHORS_DIR", EXCEL_DIR / "authors"))
EXCEL_MASTER_FILE = Path(getattr(config, "EXCEL_MASTER_FILE", EXCEL_DIR / "AIDA_Master.xlsx"))

# --- settings
researchers_file_variants = ["AIDA Scientists.txt", "AIDA Scientists .txt", "aida_scientists.txt"]
role_titles = ["First author and corresponding", "First author only",
               "Corresponding, not first", "Co-author"]
source_titles = {"o": "OpenAlex", "w": "Web of Science address", "": "unknown"}
unknown = "(unknown)"

# --- look of the sheets
header_font = Font(bold=True, color="FFFFFF")
header_fill = PatternFill("solid", fgColor="00705F")
title_font = Font(bold=True, size=14)
link_font = Font(color="00705F", underline="single")
max_column_width = 70


# ================================================== #
# THE AUTHORS SCRIPT
# ================================================== #

def load_authors_module():
    # 3_b_aida_bib_authors.py starts with a digit, so it is loaded by path
    if not AUTHORS_SCRIPT_PATH.is_file():
        sys.exit("Needed next to this script: %s" % AUTHORS_SCRIPT_PATH.name)
    spec = importlib.util.spec_from_file_location("aida_bib_authors", AUTHORS_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ================================================== #
# THE DATASET
# ================================================== #

def build_data(module, verbose):
    # the same steps as 3_b_aida_bib_authors.py, from its caches, without network
    if not INPUT_BIB_PATH.is_file():
        sys.exit("Master bib not found: %s\nRun 3_a_aida_master_bib.py first." % INPUT_BIB_PATH)
    CACHE_DIR = AUTHORS_DIR / "cache"
    entries = module.parse_bibtex(INPUT_BIB_PATH.read_text(encoding="utf-8", errors="replace"), verbose)
    records = [module.build_base_record(entry) for entry in entries]
    print("Read %d papers from %s" % (len(records), INPUT_BIB_PATH))

    works_cache = module.load_cache(CACHE_DIR / "openalex_works.json")
    institution_cache = module.load_cache(CACHE_DIR / "openalex_institutions.json")
    if not works_cache:
        print("   WARNING: no OpenAlex cache in %s" % CACHE_DIR)
        print("   run 3_b_aida_bib_authors.py first, otherwise most affiliations are missing")

    alias_keys, forced_names = module.load_author_aliases(AUTHORS_DIR / "author_aliases.csv")
    author_registry = module.AuthorRegistry(alias_keys, forced_names)
    affiliation_registry = module.AffiliationRegistry(
        module.load_affiliation_aliases(AUTHORS_DIR / "affiliation_aliases.csv"))
    overrides = module.load_corresponding_overrides(AUTHORS_DIR / "corresponding_overrides.csv")
    papers, journals, raw_texts, stats = module.build_dataset(
        records, works_cache, institution_cache, author_registry, affiliation_registry, overrides)
    remap = affiliation_registry.merge_by_ror()
    data = module.finalize_data(papers, journals, raw_texts, author_registry,
                                affiliation_registry, remap, INPUT_BIB_PATH.name)

    # --- the confirmations of the authors' own lists, by citation key
    confirmed = {entry["key"]: module.plain(entry["fields"].get("aida_confirmed_by", ""))
                 for entry in entries}
    for paper in data["papers"]:
        paper["cb"] = confirmed.get(paper["k"], "")
    print("   %d authors, %d institutions, %d journals"
          % (len(data["authors"]), len(data["affs"]), len(data["journals"])))
    return data, author_registry


# ================================================== #
# SMALL HELPERS
# ================================================== #

def file_name_for(name):
    # "Ottmar Möhler" becomes "Ottmar_Moehler", the same names as the author masters
    text = name.replace("ß", "ss")
    for source, target in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue")):
        text = text.replace(source, target)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "author"


def researcher_names():
    # one name per line, "First Last"; blank lines and lines starting with # are skipped
    candidates = [RESEARCHERS_FILE] + [RESEARCHERS_FILE.parent / name for name in researchers_file_variants]
    FILE_PATH = next((candidate for candidate in candidates if candidate.is_file()), None)
    if FILE_PATH is None:
        return [], RESEARCHERS_FILE
    names = []
    for line in FILE_PATH.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line not in names:
            names.append(line)
    return names, FILE_PATH


def years_text(years):
    years = [year for year in years if year]
    if not years:
        return ""
    return str(min(years)) if min(years) == max(years) else "%d to %d" % (min(years), max(years))


def doi_link(doi):
    return "https://doi.org/" + doi if doi else ""


# ================================================== #
# THE TABLES
# ================================================== #

# Every table function works on a list of papers and, for a researcher, his
# author index. With author None the tables cover all of AIDA.

def role_of(module, paper, author):
    position = paper["a"].index(author)
    return module.role_index(position, author, set(paper["c"]))


def paper_rows(module, data, papers, author):
    rows = []
    for paper in papers:
        journal = data["journals"][paper["j"]] if paper["j"] >= 0 else ""
        institutions = sorted({data["affs"][index]["n"] for index in paper["pa"]})
        countries = sorted({data["affs"][index]["co"] or unknown for index in paper["pa"]})
        row = [paper["y"] or "", paper["tp"], journal, paper["ty"], paper["doi"]]
        if author is None:
            row += ["; ".join(data["authors"][index]["n"] for index in paper["a"]),
                    "; ".join(data["authors"][index]["n"] for index in paper["c"]),
                    "; ".join(institutions), "; ".join(countries), paper["cb"]]
        else:
            position = paper["a"].index(author)
            own = paper["aa"][position]
            printed = sorted({data["raws"][pair[1]] for pair in paper["ar"][position]})
            row += ["%d of %d" % (position + 1, len(paper["a"])), role_titles[role_of(module, paper, author)],
                    "; ".join(data["affs"][index]["n"] for index in own) or unknown,
                    " | ".join(printed), len(paper["a"]) - 1, "; ".join(countries), paper["cb"]]
        rows.append(row)
    return rows


def affiliation_rows(data, papers, author):
    # his own institutions, with every text as printed on the papers
    found = defaultdict(lambda: {"papers": 0, "years": [], "printed": Counter(), "source": Counter()})
    for paper in papers:
        position = paper["a"].index(author)
        for index in paper["aa"][position]:
            item = found[index]
            item["papers"] += 1
            item["years"].append(paper["y"])
            item["source"][source_titles.get(paper["as"][position], "")] += 1
        for affiliation_index, raw_index in paper["ar"][position]:
            found[affiliation_index]["printed"][data["raws"][raw_index]] += 1
    rows = []
    for index, item in sorted(found.items(), key=lambda pair: -pair[1]["papers"]):
        aff = data["affs"][index]
        rows.append([aff["n"], aff["co"] or unknown, aff["ci"], item["papers"], years_text(item["years"]),
                     " | ".join(text for text, _ in item["printed"].most_common()),
                     ", ".join(source for source, _ in item["source"].most_common()), aff["ror"]])
    return rows


def coauthor_rows(data, papers, author):
    # everyone on his papers except himself
    found = defaultdict(lambda: {"papers": 0, "years": [], "institutions": Counter(), "first": 0})
    for paper in papers:
        for position, other in enumerate(paper["a"]):
            if other == author:
                continue
            item = found[other]
            item["papers"] += 1
            item["years"].append(paper["y"])
            item["first"] += 1 if position == 0 else 0
            for index in paper["aa"][position]:
                item["institutions"][data["affs"][index]["n"]] += 1
    rows = []
    for other, item in sorted(found.items(), key=lambda pair: (-pair[1]["papers"], data["authors"][pair[0]]["n"])):
        rows.append([data["authors"][other]["n"], item["papers"], years_text(item["years"]), item["first"],
                     "; ".join(name for name, _ in item["institutions"].most_common())])
    return rows


def institution_rows(data, papers, author):
    # every institution on the papers; for a researcher, whether it is one of his own
    found = defaultdict(lambda: {"papers": set(), "years": [], "people": set(), "own": False})
    for number, paper in enumerate(papers):
        for position, person in enumerate(paper["a"]):
            for index in paper["aa"][position]:
                item = found[index]
                item["papers"].add(number)
                item["years"].append(paper["y"])
                item["people"].add(person)
                if person == author:
                    item["own"] = True
        # --- papers whose institutions are known only for the paper, not per author
        for index in paper["pa"]:
            found[index]["papers"].add(number)
            found[index]["years"].append(paper["y"])
    rows = []
    for index, item in sorted(found.items(), key=lambda pair: (-len(pair[1]["papers"]), data["affs"][pair[0]]["n"])):
        aff = data["affs"][index]
        row = [aff["n"], aff["co"] or unknown, aff["ci"], len(item["papers"]), len(item["people"]),
               years_text(item["years"]), aff["ror"]]
        if author is not None:
            row.insert(3, "yes" if item["own"] else "")
        rows.append(row)
    return rows


def country_rows(data, papers, author):
    found = defaultdict(lambda: {"papers": set(), "institutions": set(), "people": set()})
    for number, paper in enumerate(papers):
        for position, person in enumerate(paper["a"]):
            for index in paper["aa"][position]:
                item = found[data["affs"][index]["co"] or unknown]
                item["papers"].add(number)
                item["institutions"].add(index)
                if person != author:
                    item["people"].add(person)
        for index in paper["pa"]:
            item = found[data["affs"][index]["co"] or unknown]
            item["papers"].add(number)
            item["institutions"].add(index)
    rows = []
    for country, item in sorted(found.items(), key=lambda pair: (-len(pair[1]["papers"]), pair[0])):
        rows.append([country, len(item["papers"]), len(item["institutions"]), len(item["people"]),
                     "; ".join(sorted(data["affs"][index]["n"] for index in item["institutions"]))])
    return rows


def journal_rows(data, papers):
    found = defaultdict(list)
    for paper in papers:
        found[data["journals"][paper["j"]] if paper["j"] >= 0 else unknown].append(paper["y"])
    return [[journal, len(years), years_text(years)]
            for journal, years in sorted(found.items(), key=lambda pair: (-len(pair[1]), pair[0]))]


def year_rows(module, papers, author):
    found = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    for paper in papers:
        counts = found[paper["y"] or 0]
        counts[0] += 1
        counts[5] += 1 if paper["cb"] else 0
        if author is not None:
            counts[1 + role_of(module, paper, author)] += 1
    rows = []
    for year in sorted(found):
        counts = found[year]
        row = [year or unknown, counts[0]]
        if author is not None:
            row += counts[1:5]
        rows.append(row + [counts[5]])
    return rows


def coverage_rows(data, papers, author):
    # which papers lack affiliation or role data, so missing data is not read as a fact
    rows = []
    for paper in papers:
        if author is None:
            known = sum(1 for ids in paper["aa"] if ids)
            gap = "" if known == len(paper["a"]) else "%d of %d authors without affiliation" % (
                len(paper["a"]) - known, len(paper["a"]))
        else:
            position = paper["a"].index(author)
            gap = "" if paper["aa"][position] else "his affiliation is not known"
        if not paper["pa"]:
            gap = "no affiliation known for any author"
        roles = "" if paper["ck"] else "corresponding author not known"
        if gap or roles:
            rows.append([paper["y"] or "", paper["tp"], paper["doi"], gap, roles])
    return rows


def researcher_summary(module, data, author, papers):
    # the counts of one researcher, used in his Summary sheet and in the master Researchers sheet
    roles = Counter(role_of(module, paper, author) for paper in papers)
    own = {index for paper in papers for index in paper["aa"][paper["a"].index(author)]}
    institutions = {index for paper in papers for ids in paper["aa"] for index in ids}
    institutions |= {index for paper in papers for index in paper["pa"]}
    return {
        "papers": len(papers),
        "years": years_text([paper["y"] for paper in papers]),
        "roles": [roles[number] for number in range(4)],
        "confirmed": sum(1 for paper in papers if paper["cb"]),
        "affiliations": len(own),
        "coauthors": len({person for paper in papers for person in paper["a"] if person != author}),
        "institutions": len(institutions),
        "countries": len({data["affs"][index]["co"] or unknown for index in institutions}),
        "journals": len({paper["j"] for paper in papers}),
        "own_known": sum(1 for paper in papers if paper["aa"][paper["a"].index(author)]),
        "roles_known": sum(1 for paper in papers if paper["ck"]),
    }


# ================================================== #
# WRITING SHEETS
# ================================================== #

def write_sheet(workbook, title, header, rows, link_columns=()):
    # one table: header row, frozen, filter on, columns as wide as their content
    # link_columns: column numbers (from 1) whose value is a DOI or a URL
    sheet = workbook.create_sheet(title)
    sheet.append(header)
    for cell in sheet[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in rows:
        sheet.append(row)
    for column in link_columns:
        for cell in sheet.iter_rows(min_row=2, min_col=column, max_col=column):
            value = cell[0].value
            if value:
                cell[0].hyperlink = value if str(value).startswith("http") else doi_link(value)
                cell[0].font = link_font
    sheet.freeze_panes = "A2"
    if rows:
        sheet.auto_filter.ref = sheet.dimensions
    for number, name in enumerate(header, start=1):
        values = [str(name)] + [str(row[number - 1]) for row in rows[:500] if number - 1 < len(row)]
        sheet.column_dimensions[get_column_letter(number)].width = min(max_column_width,
                                                                       max(len(value) for value in values) + 2)
    return sheet


def write_summary(workbook, heading, lines):
    # lines: (label, value, sheet to link or None); the first sheet of the workbook
    sheet = workbook.active
    sheet.title = "Summary"
    sheet["A1"] = heading
    sheet["A1"].font = title_font
    row = 3
    for label, value, target in lines:
        sheet.cell(row=row, column=1, value=label)
        cell = sheet.cell(row=row, column=2, value=value)
        if target:
            # a link inside the workbook: Excel wants the sheet in "location", without a #
            cell.hyperlink = Hyperlink(ref=cell.coordinate, location="'%s'!A1" % target)
            cell.font = link_font
        row += 1
    sheet.column_dimensions["A"].width = 46
    sheet.column_dimensions["B"].width = 60


def save(workbook, FILE_PATH):
    FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        workbook.save(str(FILE_PATH))
    except PermissionError:
        sys.exit("Cannot write %s, it is probably open in Excel. Close it and run again." % FILE_PATH)


# ================================================== #
# THE WORKBOOKS
# ================================================== #

def researcher_workbook(module, data, author, name, papers, built):
    numbers = researcher_summary(module, data, author, papers)
    workbook = Workbook()
    write_summary(workbook, "AIDA publications of %s" % name, [
        ("Name in the papers", data["authors"][author]["n"], None),
        ("Other name forms", "; ".join(data["authors"][author]["v"]), None),
        ("Papers", numbers["papers"], "Papers"),
        ("Years", numbers["years"], "Per year"),
        ("  First author and corresponding", numbers["roles"][0], "Papers"),
        ("  First author only", numbers["roles"][1], "Papers"),
        ("  Corresponding, not first", numbers["roles"][2], "Papers"),
        ("  Co-author", numbers["roles"][3], "Papers"),
        ("Papers confirmed by an author's own list", numbers["confirmed"], "Papers"),
        ("Affiliations (his own institutions)", numbers["affiliations"], "Affiliations"),
        ("Co-authors", numbers["coauthors"], "Co-authors"),
        ("Institutions worked with (his and co-authors')", numbers["institutions"], "Institutions"),
        ("Countries", numbers["countries"], "Countries"),
        ("Journals", numbers["journals"], "Journals"),
        ("Papers with his affiliation known", "%d of %d" % (numbers["own_known"], numbers["papers"]),
         "Data coverage"),
        ("Papers with the corresponding author known", "%d of %d" % (numbers["roles_known"], numbers["papers"]),
         "Data coverage"),
        ("Source", "%s, built %s" % (data["source"], built), None),
    ])
    write_sheet(workbook, "Papers",
                ["Year", "Title", "Journal", "Type", "DOI", "His position", "His role",
                 "His affiliation", "His affiliation as printed", "Co-authors", "Countries", "Confirmed by"],
                paper_rows(module, data, papers, author), link_columns=(5,))
    write_sheet(workbook, "Affiliations",
                ["Institution", "Country", "City", "Papers", "Years", "As printed on the papers", "Source", "ROR"],
                affiliation_rows(data, papers, author), link_columns=(8,))
    write_sheet(workbook, "Co-authors",
                ["Co-author", "Joint papers", "Years", "Of these as first author", "Institutions"],
                coauthor_rows(data, papers, author))
    write_sheet(workbook, "Institutions",
                ["Institution", "Country", "City", "His own", "Joint papers", "People", "Years", "ROR"],
                institution_rows(data, papers, author), link_columns=(8,))
    write_sheet(workbook, "Countries",
                ["Country", "Papers", "Institutions", "Co-authors", "Institutions in it"],
                country_rows(data, papers, author))
    write_sheet(workbook, "Journals", ["Journal", "Papers", "Years"], journal_rows(data, papers))
    write_sheet(workbook, "Per year",
                ["Year", "Papers"] + role_titles + ["Confirmed"], year_rows(module, papers, author))
    write_sheet(workbook, "Data coverage", ["Year", "Title", "DOI", "Affiliation", "Roles"],
                coverage_rows(data, papers, author), link_columns=(3,))
    return workbook, numbers


def master_workbook(module, data, researcher_rows, built):
    papers = data["papers"]
    institutions = {index for paper in papers for ids in paper["aa"] for index in ids}
    institutions |= {index for paper in papers for index in paper["pa"]}
    with_affiliation = sum(1 for paper in papers if paper["pa"])
    workbook = Workbook()
    write_summary(workbook, "AIDA publications", [
        ("Papers", len(papers), "Papers"),
        ("Years", years_text([paper["y"] for paper in papers]), "Per year"),
        ("Papers confirmed by an author's own list", sum(1 for paper in papers if paper["cb"]), "Papers"),
        ("AIDA researchers with a workbook", len(researcher_rows), "Researchers"),
        ("Authors", len({person for paper in papers for person in paper["a"]}), "Authors"),
        ("Institutions", len(institutions), "Institutions"),
        ("Countries", len({data["affs"][index]["co"] or unknown for index in institutions}), "Countries"),
        ("Journals", len({paper["j"] for paper in papers}), "Journals"),
        ("Papers with an affiliation known", "%d of %d" % (with_affiliation, len(papers)), "Data coverage"),
        ("Papers with the corresponding author known",
         "%d of %d" % (sum(1 for paper in papers if paper["ck"]), len(papers)), "Data coverage"),
        ("Source", "%s, built %s" % (data["source"], built), None),
    ])
    write_sheet(workbook, "Researchers",
                ["Researcher", "Papers", "Years"] + role_titles +
                ["Confirmed", "Affiliations", "Co-authors", "Institutions", "Countries", "Journals", "Workbook"],
                researcher_rows)
    write_sheet(workbook, "Papers",
                ["Year", "Title", "Journal", "Type", "DOI", "Authors", "Corresponding",
                 "Institutions", "Countries", "Confirmed by"],
                paper_rows(module, data, papers, None), link_columns=(5,))

    # --- every author of every paper, with his roles
    found = defaultdict(lambda: {"roles": [0, 0, 0, 0], "years": [], "institutions": Counter()})
    for paper in papers:
        for position, person in enumerate(paper["a"]):
            item = found[person]
            item["roles"][role_of(module, paper, person)] += 1
            item["years"].append(paper["y"])
            for index in paper["aa"][position]:
                item["institutions"][data["affs"][index]["n"]] += 1
    author_rows = [[data["authors"][person]["n"], sum(item["roles"]), years_text(item["years"])] + item["roles"] +
                   ["; ".join(name for name, _ in item["institutions"].most_common(5))]
                   for person, item in sorted(found.items(), key=lambda pair: (-sum(pair[1]["roles"]),
                                                                              data["authors"][pair[0]]["n"]))]
    write_sheet(workbook, "Authors", ["Author", "Papers", "Years"] + role_titles + ["Main institutions"], author_rows)
    write_sheet(workbook, "Institutions",
                ["Institution", "Country", "City", "Papers", "People", "Years", "ROR"],
                institution_rows(data, papers, None), link_columns=(7,))
    write_sheet(workbook, "Countries", ["Country", "Papers", "Institutions", "Authors", "Institutions in it"],
                country_rows(data, papers, None))
    write_sheet(workbook, "Journals", ["Journal", "Papers", "Years"], journal_rows(data, papers))
    write_sheet(workbook, "Per year", ["Year", "Papers", "Confirmed"], year_rows(module, papers, None))
    write_sheet(workbook, "Data coverage", ["Year", "Title", "DOI", "Affiliation", "Roles"],
                coverage_rows(data, papers, None), link_columns=(3,))
    return workbook


# ================================================== #
# MAIN
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(description="Excel workbooks per AIDA researcher and for all of AIDA.")
    parser.add_argument("--only", action="append", default=[],
                        help='one researcher, as in AIDA Scientists.txt, e.g. --only "Robert Wagner"')
    parser.add_argument("--dry-run", action="store_true", help="read and count, write nothing")
    parser.add_argument("--verbose", action="store_true", help="more details while reading")
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    module = load_authors_module()
    data, author_registry = build_data(module, arguments.verbose)
    built = datetime.date.today().isoformat()

    names, NAMES_PATH = researcher_names()
    if not names:
        sys.exit("No researchers found, checked %s" % NAMES_PATH)
    if arguments.only:
        wanted = {name.strip().lower() for name in arguments.only}
        names = [name for name in names if name.lower() in wanted]
        if not names:
            sys.exit("None of %s is in %s" % (", ".join(arguments.only), NAMES_PATH.name))

    # --- one workbook per researcher; the folder belongs to this script
    if not arguments.dry_run and not arguments.only:
        EXCEL_AUTHORS_DIR.mkdir(parents=True, exist_ok=True)
        for OLD_FILE in EXCEL_AUTHORS_DIR.glob("*.xlsx"):
            try:
                OLD_FILE.unlink()
            except PermissionError:
                sys.exit("Cannot replace %s, it is open in Excel. Close it and run again." % OLD_FILE)
    print("Researchers: %d in %s" % (len(names), NAMES_PATH.name))
    researcher_rows = []
    for name in names:
        key = author_registry.key_for(*module.split_name(name))
        author = author_registry.index_by_key.get(key)
        papers = [paper for paper in data["papers"] if author is not None and author in paper["a"]]
        if not papers:
            print("   %-28s no paper in the master, no workbook" % name)
            continue
        workbook, numbers = researcher_workbook(module, data, author, name, papers, built)
        FILE_PATH = EXCEL_AUTHORS_DIR / (file_name_for(name) + ".xlsx")
        researcher_rows.append([name, numbers["papers"], numbers["years"]] + numbers["roles"] +
                               [numbers["confirmed"], numbers["affiliations"], numbers["coauthors"],
                                numbers["institutions"], numbers["countries"], numbers["journals"],
                                "authors/" + FILE_PATH.name])
        print("   %-28s %4d %-7s %3d co-authors, %3d institutions, %2d countries"
              % (name, numbers["papers"], "paper," if numbers["papers"] == 1 else "papers,",
                 numbers["coauthors"], numbers["institutions"], numbers["countries"]))
        if not arguments.dry_run:
            save(workbook, FILE_PATH)

    if arguments.dry_run:
        print("Dry run, nothing written.")
        return
    print("Written: %d workbooks in %s" % (len(researcher_rows), EXCEL_AUTHORS_DIR))

    # --- the master workbook, only for a full run
    if arguments.only:
        print("Master workbook skipped, it is only made in a full run")
        return
    researcher_rows.sort(key=lambda row: (-row[1], row[0]))
    save(master_workbook(module, data, researcher_rows, built), EXCEL_MASTER_FILE)
    print("Written: %s" % EXCEL_MASTER_FILE)


if __name__ == "__main__":
    main()
