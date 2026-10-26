'''
3_d_aida_merge_corrections.py

   Reads the review packets that the authors send back, applies what only they
   can answer, and reports everything that several people share.

Purpose

   Corrections come in two kinds, and they must not be treated the same way.

      Author level   the role on a paper, the affiliation at the time, whether
                     a name form is really that person. Only the author knows,
                     nobody else answers for the same slot, so these are
                     applied directly and can never conflict.

      Paper level    does the paper belong to AIDA, is it peer reviewed, is the
                     row an article or only a discussion page. Every author of
                     that paper is asked, so answers can disagree. These are
                     counted as votes: a unanimous answer becomes a proposal,
                     a split answer becomes a line in the conflict report and
                     is decided by hand.

   Nothing is changed in the master bib file. The script writes proposals, and
   --apply copies them into the live files.

Usage

   python 3_d_aida_merge_corrections.py
   python 3_d_aida_merge_corrections.py --apply          write into the live files
   python 3_d_aida_merge_corrections.py --author aut0007 only this packet
   python 3_d_aida_merge_corrections.py --verbose

   The returned files are expected in <root>/info/aut/corrections/, in any
   layout: loose files, one folder per author, or unpacked zips. Both
   corrections.csv and missing_papers.txt are recognised by their content,
   not by their file name. A corrections.csv saved by Excel on Windows, in
   cp1252 or with ; as separator, is read as well.

   All folders come from config.py and are relative to the project root,
   so the script runs the same on Linux and Windows without editing paths.

Output

   <root>/info/aut/corrections/_merged/summary.txt
   <root>/info/aut/corrections/_merged/corresponding_overrides.proposed.csv
   <root>/info/aut/corrections/_merged/yesAIDA_additions.proposed.txt
   <root>/info/aut/corrections/_merged/noAIDA_additions.proposed.txt
   <root>/info/aut/corrections/_merged/conflicts.csv        paper level answers that disagree
   <root>/info/aut/corrections/_merged/manual_review.csv    affiliation and identity questions
   <root>/info/aut/corrections/_merged/missing_without_doi.bib
   with --apply the proposals are also written into the live files
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import csv
import datetime
import io
import os
import re
import sys
from collections import Counter, defaultdict
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
BASE_DIR = Path(getattr(config, "BASE_DIR", FALLBACK_ROOT))
MASTER_DIR = Path(getattr(config, "MASTER_BIB_DIR", BASE_DIR / "AIDA_master"))
INPUT_BIB_PATH = Path(getattr(config, "MASTER_BIB_FILE", MASTER_DIR / "AIDA_Master_bib.bib"))
AUTHORS_OUTPUT_DIR = Path(getattr(config, "AUTHORS_HTML_DIR", BASE_DIR / "output" / "authors"))
REVIEW_DIR = Path(getattr(config, "AUTHOR_REVIEW_DIR", BASE_DIR / "info" / "aut" / "review"))
CORRECTIONS_DIR = Path(getattr(config, "AUTHOR_CORRECTIONS_DIR",
                               BASE_DIR / "info" / "aut" / "corrections"))

# --- the two shared filter lists read by aida_master_bib.py
NO_AIDA_PATH = Path(getattr(config, "NO_AIDA_FILE", BASE_DIR / "data" / "noAIDA.txt"))
YES_AIDA_PATH = Path(getattr(config, "YES_AIDA_FILE", BASE_DIR / "data" / "yesAIDA.txt"))

# --- answers accepted in the correction file
yes_words = {"yes", "y", "true", "1", "correct", "ok", "richtig", "ja"}
no_words = {"no", "n", "false", "0", "wrong", "falsch", "nein"}

# --- the four roles, and what each one says about being the corresponding author
role_meanings = {
    "first author and corresponding author": (True, True),
    "first author, not corresponding": (True, False),
    "corresponding author, not first author": (False, True),
    "co-author": (False, False),
    # --- short forms people type by hand
    "first + corresponding": (True, True),
    "first and corresponding": (True, True),
    "first author only": (True, False),
    "first author": (True, False),
    "corresponding, not first": (False, True),
    "corresponding not first": (False, True),
    "corresponding author": (False, True),
    "coauthor": (False, False),
    "co author": (False, False),
}

doi_pattern = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>,;)\]]+", re.IGNORECASE)


# ================================================== #
# SMALL HELPERS
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


def write_csv(CSV_PATH, header, rows):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def value_of(row, *names):
    for name in names:
        for header, value in (row or {}).items():
            if header and header.strip().lower() == name:
                return (value or "").strip()
    return ""


def answer(text):
    # "yes", "no" or "" when the cell was left empty
    text = (text or "").strip().lower()
    if text in yes_words:
        return "yes"
    if text in no_words:
        return "no"
    return ""


def clean_doi(text):
    # bare lowercase DOI, without the resolver and without trailing punctuation
    text = (text or "").strip()
    text = re.sub(r"^\s*(https?://)?(dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*doi:\s*", "", text, flags=re.IGNORECASE)
    match = doi_pattern.search(text)
    if not match:
        return ""
    return match.group(0).rstrip(".,;)]").lower()


def role_flags(text):
    # (is_first, is_corresponding) for a role name, or None when not understood
    key = re.sub(r"\s+", " ", (text or "").strip().lower()).rstrip(".")
    return role_meanings.get(key)


# ================================================== #
# READING WHAT CAME BACK
# ================================================== #

def find_returned_files(ROOT_DIR):
    # corrections files and missing paper files are told apart by their content
    correction_files, missing_files = [], []
    if not ROOT_DIR.is_dir():
        return correction_files, missing_files
    for PATH in sorted(ROOT_DIR.rglob("*")):
        if not PATH.is_file() or "_merged" in PATH.parts:
            continue
        suffix = PATH.suffix.lower()
        if suffix == ".csv":
            rows = read_csv_rows(PATH)
            if rows and value_of(rows[0], "author_id") and "doi" in {
                    (header or "").strip().lower() for header in rows[0]}:
                correction_files.append(PATH)
        elif suffix in (".txt", ".bib"):
            text = PATH.read_text(encoding="utf-8", errors="replace")
            if doi_pattern.search(text) or "@" in text:
                missing_files.append(PATH)
    return correction_files, missing_files


def load_registry(REGISTRY_PATH):
    by_id, by_key = {}, {}
    for row in read_csv_rows(REGISTRY_PATH):
        author_id = value_of(row, "author_id")
        if not author_id:
            continue
        entry = {"author_id": author_id, "author_key": value_of(row, "author_key"),
                 "display_name": value_of(row, "display_name")}
        by_id[author_id] = entry
        if entry["author_key"]:
            by_key[entry["author_key"]] = entry
    return by_id, by_key


def master_dois(INPUT_PATH):
    # every DOI already in the master bib, so a "missing" paper is not proposed twice
    if not INPUT_PATH.is_file():
        return set()
    text = INPUT_PATH.read_text(encoding="utf-8", errors="replace")
    found = set()
    for match in re.finditer(r"\bdoi\s*=\s*[{\"]([^}\"]+)[}\"]", text, re.IGNORECASE):
        doi = clean_doi(match.group(1))
        if doi:
            found.add(doi)
    return found


def existing_list(LIST_PATH):
    # identifiers already in noAIDA.txt or yesAIDA.txt; both DOIs and citation keys
    if not LIST_PATH.is_file():
        return set()
    found = set()
    for line in LIST_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        doi = clean_doi(line)
        if doi:
            found.add(doi)
            continue
        found.add(line.split()[0].strip().lower())
    return found


# ================================================== #
# AUTHOR LEVEL: ROLE, AFFILIATION, IDENTITY
# ================================================== #

def collect_corrections(correction_files, by_id, by_key, wanted, verbose):
    # returns author level results, paper level votes and the problems found
    role_rows = []
    manual_rows = []
    votes = defaultdict(lambda: defaultdict(list))
    seen_authors = {}
    folder_author = {}
    problems = []
    counts = Counter()

    for PATH in correction_files:
        rows = read_csv_rows(PATH)
        for line_number, row in enumerate(rows, start=2):
            author_id = value_of(row, "author_id")
            author_key = value_of(row, "author_key")
            author_name = value_of(row, "author_name")
            entry = by_id.get(author_id) or by_key.get(author_key)
            if entry is None:
                problems.append("%s line %d: unknown author %s / %s"
                                % (PATH.name, line_number, author_id or "-", author_key or "-"))
                counts["rows_unknown_author"] += 1
                continue
            if wanted and entry["author_id"] not in wanted:
                continue
            seen_authors[entry["author_id"]] = entry
            folder_author.setdefault(str(PATH.parent.resolve()), entry["author_id"])
            counts["rows_read"] += 1

            doi = clean_doi(value_of(row, "doi"))
            paper_key = value_of(row, "paper_key")
            paper_id = doi or paper_key.lower()
            title = value_of(row, "title")
            if not paper_id:
                problems.append("%s line %d: row without DOI and without paper key"
                                % (PATH.name, line_number))
                continue

            listed_role = value_of(row, "listed_role")
            comment = value_of(row, "comment")

            # --- role: only the corresponding flag can be set from here,
            # the first author position comes from the author order in the bib
            if answer(value_of(row, "role_correct")) == "no":
                correct = value_of(row, "correct_role")
                flags = role_flags(correct)
                listed = role_flags(listed_role)
                if flags is None:
                    problems.append('%s line %d: role "%s" not understood, expected one of: %s'
                                    % (PATH.name, line_number, correct,
                                       "; ".join(sorted({key for key in role_meanings}))[:120]))
                    counts["role_not_understood"] += 1
                else:
                    # --- the corresponding flag is written down every time the author
                    # states it, also when it already matches. The statement then
                    # survives the next harvest instead of being silently undone.
                    role_rows.append([paper_id, author_name or entry["display_name"],
                                      "yes" if flags[1] else "no",
                                      "%s %s: %s -> %s" % (entry["author_id"], today_text(),
                                                           listed_role or "?", correct)])
                    counts["role_corrections"] += 1
                    if listed is not None and listed[1] == flags[1]:
                        counts["role_no_change"] += 1
                    # --- the first author position comes from the author order in the bib
                    # entry, so a change there is a paper level problem, not an override
                    if listed is not None and listed[0] != flags[0]:
                        manual_rows.append([entry["author_id"], entry["display_name"], paper_id,
                                            title, "author order",
                                            "listed as %s, says %s; the author order in the bib "
                                            "entry has to be checked" % (listed_role, correct),
                                            comment])
                        counts["author_order"] += 1

            # --- affiliation: never applied automatically, it needs an alias decision
            if answer(value_of(row, "affiliation_correct")) == "no":
                manual_rows.append([entry["author_id"], entry["display_name"], paper_id, title,
                                    "affiliation",
                                    "listed: %s; says: %s" % (value_of(row, "listed_affiliation"),
                                                              value_of(row, "correct_affiliation")),
                                    comment])
                counts["affiliation_corrections"] += 1

            # --- identity: almost always another person with the same surname
            if answer(value_of(row, "this_is_me")) == "no":
                manual_rows.append([entry["author_id"], entry["display_name"], paper_id, title,
                                    "not this person",
                                    "says this paper is not theirs; most likely another person "
                                    "with the same surname and initial, fix in author_aliases.csv",
                                    comment])
                counts["identity_problems"] += 1

            # --- paper level answers are votes, they are counted, not applied
            for column in ("belongs_to_aida", "peer_reviewed"):
                reply = answer(value_of(row, column))
                if reply:
                    votes[paper_id][column].append(
                        (entry["author_id"], entry["display_name"], reply, title, comment))
                    counts["paper_level_answers"] += 1

    if verbose and problems:
        print("")
        for line in problems:
            print("  %s" % line)
    return role_rows, manual_rows, votes, seen_authors, folder_author, counts, problems


def today_text():
    return datetime.date.today().isoformat()


# ================================================== #
# PAPER LEVEL: VOTES AND CONFLICTS
# ================================================== #

def resolve_votes(votes):
    # unanimous answers become proposals, split answers become conflicts
    remove_rows = []
    conflict_rows = []
    for paper_id in sorted(votes):
        for column, replies in sorted(votes[paper_id].items()):
            answers = {reply[2] for reply in replies}
            title = replies[0][3]
            who = "; ".join("%s %s: %s" % (reply[0], reply[1], reply[2]) for reply in replies)
            notes = "; ".join(reply[4] for reply in replies if reply[4])
            if len(answers) > 1:
                conflict_rows.append([paper_id, title, column, who, notes])
                continue
            if answers == {"no"}:
                reason = ("does not belong to AIDA" if column == "belongs_to_aida"
                          else "not a peer reviewed article")
                remove_rows.append([paper_id, title, reason, who, notes])
    return remove_rows, conflict_rows


# ================================================== #
# MISSING PAPERS
# ================================================== #

def collect_missing(missing_files, by_id, by_key, known_dois, wanted, folder_author):
    # DOIs and pasted BibTeX entries from the missing_papers files
    found = {}
    entries_without_doi = []
    counts = Counter()
    for PATH in missing_files:
        # --- the author is taken from the folder name or from the file name
        entry = by_id.get(folder_author.get(str(PATH.parent.resolve()), ""))
        if entry is None:
            stem = " ".join(PATH.parts[-3:]).lower()
            for author_id, item in by_id.items():
                if author_id.lower() in stem:
                    entry = item
                    break
        who = entry["author_id"] if entry else PATH.parent.name

        text = PATH.read_text(encoding="utf-8", errors="replace")
        if wanted and entry and entry["author_id"] not in wanted:
            continue

        # --- whole BibTeX entries first, so their DOI is not counted twice
        blocks = re.split(r"(?=@\w+\s*\{)", text)
        for block in blocks:
            if not block.lstrip().startswith("@"):
                continue
            match = re.search(r"\bdoi\s*=\s*[{\"]([^}\"]+)[}\"]", block, re.IGNORECASE)
            doi = clean_doi(match.group(1)) if match else ""
            if doi:
                found.setdefault(doi, {"who": set(), "note": ""})["who"].add(who)
            else:
                entries_without_doi.append((who, block.strip()))
                counts["entries_without_doi"] += 1
            text = text.replace(block, " ")

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("%"):
                continue
            doi = clean_doi(line)
            if not doi:
                continue
            item = found.setdefault(doi, {"who": set(), "note": ""})
            item["who"].add(who)
            # --- anything after the DOI is kept as the author's own note
            rest = line[line.lower().find(doi) + len(doi):].strip(" \t-:;,")
            if rest and not item["note"]:
                item["note"] = rest

    new_dois = {doi: item for doi, item in found.items() if doi not in known_dois}
    counts["missing_named"] = len(found)
    counts["missing_new"] = len(new_dois)
    counts["missing_already_listed"] = len(found) - len(new_dois)
    return new_dois, entries_without_doi, counts


# ================================================== #
# MERGING WITH WHAT IS ALREADY THERE
# ================================================== #

def merge_override_rows(EXISTING_PATH, role_rows):
    # existing corrections are kept; a new answer for the same pair wins
    header = ["doi_or_key", "author", "corresponding", "note"]
    merged = {}
    for row in read_csv_rows(EXISTING_PATH):
        paper = clean_doi(value_of(row, "doi_or_key", "doi")) or value_of(
            row, "doi_or_key", "paper_key", "key").lower()
        author = value_of(row, "author", "name")
        if paper and author:
            merged[(paper, author.lower())] = [paper, author,
                                               value_of(row, "corresponding", "is_corresponding"),
                                               value_of(row, "note")]
    added = 0
    for row in role_rows:
        key = (row[0], row[1].lower())
        if key not in merged:
            added += 1
        merged[key] = row
    return header, [merged[key] for key in sorted(merged)], added


def append_list_file(LIST_PATH, lines, heading, write_enabled):
    if not lines:
        return 0
    if not write_enabled:
        return len(lines)
    LIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = LIST_PATH.read_text(encoding="utf-8") if LIST_PATH.is_file() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n# --- %s, %s\n" % (heading, today_text())
    text += "\n".join(lines) + "\n"
    LIST_PATH.write_text(text, encoding="utf-8")
    return len(lines)


# ================================================== #
# REPORT
# ================================================== #

def write_summary(SUMMARY_PATH, counts, seen_authors, remove_rows, conflict_rows,
                  manual_rows, new_dois, problems, applied):
    lines = []
    lines.append("AIDA author corrections, merged %s" % today_text())
    lines.append("")
    lines.append("Packets read              %d" % len(seen_authors))
    for author_id in sorted(seen_authors):
        lines.append("    %-10s %s" % (author_id, seen_authors[author_id]["display_name"]))
    lines.append("")
    lines.append("Rows read                 %d" % counts["rows_read"])
    lines.append("")
    lines.append("Author level, applied directly")
    lines.append("  role corrections        %d" % counts["role_corrections"])
    lines.append("    of those, already right %d" % counts["role_no_change"])
    lines.append("")
    lines.append("Author level, needs a decision by hand")
    lines.append("  affiliation questions   %d" % counts["affiliation_corrections"])
    lines.append("  identity questions      %d" % counts["identity_problems"])
    lines.append("  author order questions  %d" % counts["author_order"])
    lines.append("")
    lines.append("Paper level, shared between the authors")
    lines.append("  answers received        %d" % counts["paper_level_answers"])
    lines.append("  agreed removals         %d" % len(remove_rows))
    lines.append("  conflicts to decide     %d" % len(conflict_rows))
    lines.append("")
    lines.append("Missing papers")
    lines.append("  named                   %d" % counts["missing_named"])
    lines.append("  already in the bib      %d" % counts["missing_already_listed"])
    lines.append("  new, to be added        %d" % counts["missing_new"])
    lines.append("  without a DOI           %d" % counts["entries_without_doi"])
    if problems:
        lines.append("")
        lines.append("Problems in the returned files  %d" % len(problems))
        for line in problems:
            lines.append("  %s" % line)
    if conflict_rows:
        lines.append("")
        lines.append("Conflicts, decide these with Ottmar")
        for row in conflict_rows:
            lines.append("  %s  %s" % (row[0], row[1][:70]))
            lines.append("      %s: %s" % (row[2], row[3]))
    lines.append("")
    lines.append("Applied to the live files: %s" % ("yes" if applied else "no, proposals only"))
    text = "\n".join(lines) + "\n"
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(text, encoding="utf-8")
    print("")
    print(text)


# ================================================== #
# ARGUMENTS
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Merge the author review packets that came back.")
    parser.add_argument("--input", default=str(INPUT_BIB_PATH), help="master BibTeX file")
    parser.add_argument("--corrections-dir", default=str(CORRECTIONS_DIR),
                        help="folder with the returned files")
    parser.add_argument("--review-dir", default=str(REVIEW_DIR),
                        help="folder with authors_registry.csv")
    parser.add_argument("--authors-dir", default=str(AUTHORS_OUTPUT_DIR),
                        help="folder of 3_b, holds corresponding_overrides.csv")
    parser.add_argument("--no-aida", default=str(NO_AIDA_PATH), help="shared noAIDA.txt")
    parser.add_argument("--yes-aida", default=str(YES_AIDA_PATH), help="shared yesAIDA.txt")
    parser.add_argument("--author", action="append", default=[],
                        help="merge only this author id, can be repeated")
    parser.add_argument("--apply", action="store_true",
                        help="write the proposals into the live files as well")
    parser.add_argument("--verbose", action="store_true", help="more details while running")
    return parser.parse_args()


# ================================================== #
# MAIN
# ================================================== #

def main():
    arguments = parse_arguments()
    RETURNED_DIR = Path(arguments.corrections_dir)
    MERGED_DIR = RETURNED_DIR / "_merged"
    REGISTRY_PATH = Path(arguments.review_dir) / "authors_registry.csv"
    OVERRIDES_PATH = Path(arguments.authors_dir) / "corresponding_overrides.csv"
    wanted = {item.strip() for item in arguments.author if item.strip()}

    by_id, by_key = load_registry(REGISTRY_PATH)
    if not by_id:
        print("No author registry at %s" % REGISTRY_PATH)
        print("Run 3_c_aida_author_packets.py first.")
        sys.exit(1)
    print("Registry: %d authors" % len(by_id))

    correction_files, missing_files = find_returned_files(RETURNED_DIR)
    print("Returned files in %s" % RETURNED_DIR)
    print("  %d correction files, %d missing paper files"
          % (len(correction_files), len(missing_files)))
    if not correction_files and not missing_files:
        print("Nothing to merge yet.")
        return

    # --- author level and the paper level votes
    role_rows, manual_rows, votes, seen_authors, folder_author, counts, problems = \
        collect_corrections(correction_files, by_id, by_key, wanted, arguments.verbose)
    remove_rows, conflict_rows = resolve_votes(votes)

    # --- missing papers, without the ones already in the bib
    known_dois = master_dois(Path(arguments.input))
    new_dois, entries_without_doi, missing_counts = collect_missing(
        missing_files, by_id, by_key, known_dois, wanted, folder_author)
    counts.update(missing_counts)

    # --- proposals
    MERGED_DIR.mkdir(parents=True, exist_ok=True)
    header, override_rows, added = merge_override_rows(OVERRIDES_PATH, role_rows)
    write_csv(MERGED_DIR / "corresponding_overrides.proposed.csv", header, override_rows)

    already_yes = existing_list(Path(arguments.yes_aida))
    yes_lines = []
    for doi in sorted(new_dois):
        if doi in already_yes:
            continue
        item = new_dois[doi]
        yes_lines.append("%s   # from %s%s" % (doi, ", ".join(sorted(item["who"])),
                                               (": " + item["note"]) if item["note"] else ""))
    (MERGED_DIR / "yesAIDA_additions.proposed.txt").write_text(
        "# proposed additions, %s\n" % today_text() + "\n".join(yes_lines) + "\n",
        encoding="utf-8")

    already_no = existing_list(Path(arguments.no_aida))
    no_lines = []
    for row in remove_rows:
        if row[0] in already_no:
            continue
        no_lines.append("%s   # %s (%s)" % (row[0], row[2], row[3]))
    (MERGED_DIR / "noAIDA_additions.proposed.txt").write_text(
        "# proposed removals, %s\n" % today_text() + "\n".join(no_lines) + "\n",
        encoding="utf-8")

    write_csv(MERGED_DIR / "conflicts.csv",
              ["doi_or_key", "title", "question", "answers", "comments"], conflict_rows)
    write_csv(MERGED_DIR / "manual_review.csv",
              ["author_id", "author_name", "doi_or_key", "title", "kind", "detail", "comment"],
              manual_rows)
    if entries_without_doi:
        text = "\n\n".join("%% from %s\n%s" % pair for pair in entries_without_doi)
        (MERGED_DIR / "missing_without_doi.bib").write_text(text + "\n", encoding="utf-8")

    # --- writing into the live files
    if arguments.apply:
        write_csv(OVERRIDES_PATH, header, override_rows)
        append_list_file(Path(arguments.yes_aida), yes_lines, "from author review", True)
        append_list_file(Path(arguments.no_aida), no_lines, "from author review", True)
        print("")
        print("Applied:")
        print("  %s  %d rows (%d new)" % (OVERRIDES_PATH.name, len(override_rows), added))
        print("  %s  %d lines added" % (Path(arguments.yes_aida).name, len(yes_lines)))
        print("  %s  %d lines added" % (Path(arguments.no_aida).name, len(no_lines)))
        print("")
        print("Now rebuild: aida_master_bib.py, then 3_b_aida_bib_authors.py")

    write_summary(MERGED_DIR / "summary.txt", counts, seen_authors, remove_rows,
                  conflict_rows, manual_rows, new_dois, problems, arguments.apply)
    print("Written to %s" % MERGED_DIR)


if __name__ == "__main__":
    main()
