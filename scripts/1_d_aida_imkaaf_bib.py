'''
1_d_aida_imkaaf_bib.py

   Downloads the BibTeX record of every peer reviewed publication listed on
   the IMK-AAF publications page and merges them into one master .bib file.

 Purpose

   The page https://www.imkaaf.kit.edu/60.php links each publication to its
   KITopen landing page (publikationen.bibliothek.kit.edu/<kitopen_id>).
   KITopen offers a BibTeX export for every record, so this script:
     1. scrapes the page and collects all KITopen ids (and DOIs as fallback)
     2. downloads the BibTeX export for each id
     3. falls back to doi.org content negotiation if KITopen export fails
     4. caches each record, so reruns only fetch missing entries
     5. merges all records into one file with unique citation keys

 Usage

   python 1_d_aida_imkaaf_bib.py                 full run
   python 1_d_aida_imkaaf_bib.py --dry-run       only list found publications
   python 1_d_aida_imkaaf_bib.py --limit 10      test with the first 10 records
   python 1_d_aida_imkaaf_bib.py --refresh       ignore cache, download again
   python 1_d_aida_imkaaf_bib.py --delay 1.0     seconds between requests
   python 1_d_aida_imkaaf_bib.py --verbose       print every request

   Works the same on Linux and Windows. All folders come from config.py and
   are relative to the project root, so nothing has to be set by hand.

 Output

   <root>/master_bibs/imkaaf_master.bib        merged BibTeX file
   <root>/data/imkaaf/<id>.bib                 one cached record per id
   <root>/master_bibs/imkaaf_failed.csv        records that failed
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup, FeatureNotFound
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ================================================== #
# CONFIGURATION
# ================================================== #

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

# --- paths
# the per-record cache lives with the raw data, the merged file with the masters
MASTER_DIR = Path(getattr(config, "IMKAAF_MASTER_DIR", FALLBACK_ROOT / "master_bibs"))
CACHE_DIR = Path(getattr(config, "IMKAAF_CACHE_DIR", FALLBACK_ROOT / "data" / "imkaaf"))

OUTPUT_BIB = MASTER_DIR / "imkaaf_master.bib"
FAILED_CSV = MASTER_DIR / "imkaaf_failed.csv"

# --- urls
PAGE_URL = "https://www.imkaaf.kit.edu/60.php"
KITOPEN_EXPORT_URL = "https://publikationen.bibliothek.kit.edu/publikationslisten/get.php"
DOI_URL = "https://doi.org/"

# --- request settings
user_agent = "AIDA-bibliometrics/1.0 (KIT IMK-AAF research use)"
request_timeout = 30
request_delay = 0.5

# --- patterns
# landing page link: only the id, no trailing file number (that is the PDF link)
landing_pattern = re.compile(r"^https?://publikationen\.bibliothek\.kit\.edu/(\d+)/?$")
doi_pattern = re.compile(r"^https?://(dx\.)?doi\.org/(.+)$", re.IGNORECASE)
# first line of a BibTeX entry: @type{key,
entry_head_pattern = re.compile(r"@\s*(\w+)\s*\{\s*([^,\s]*)\s*,")


# ================================================== #
# HELPERS
# ================================================== #

# --- http session
def build_session():
    # retry on temporary server errors and rate limits
    retry_rule = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry_rule))
    session.mount("http://", HTTPAdapter(max_retries=retry_rule))
    session.headers.update({"User-Agent": user_agent})
    return session


# --- validation
def is_valid_bibtex(text):
    # an export is valid if it contains a BibTeX entry head and no HTML
    if not text:
        return False
    if "<html" in text[:500].lower():
        return False
    return entry_head_pattern.search(text) is not None


# --- logging
def log(message, verbose_only=False, verbose=False):
    if verbose_only and not verbose:
        return
    print(message, flush=True)


# ================================================== #
# STEP 1: SCRAPE PUBLICATION LIST
# ================================================== #

def scrape_publications(session, verbose=False):
    log(f"Fetching publication list: {PAGE_URL}")
    response = session.get(PAGE_URL, timeout=request_timeout)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"

    # lxml is faster, the built in parser needs no extra install on Windows
    try:
        soup = BeautifulSoup(response.text, "lxml")
    except FeatureNotFound:
        soup = BeautifulSoup(response.text, "html.parser")

    # walk all links in document order
    # a landing link starts a new record, the next DOI link belongs to it
    records = []
    seen_ids = set()
    current = None

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()

        landing_match = landing_pattern.match(href)
        if landing_match:
            kitopen_id = landing_match.group(1)
            if kitopen_id in seen_ids:
                current = None
                continue
            seen_ids.add(kitopen_id)
            current = {
                "kitopen_id": kitopen_id,
                "title": anchor.get_text(" ", strip=True),
                "doi": "",
            }
            records.append(current)
            continue

        doi_match = doi_pattern.match(href)
        if doi_match and current is not None and not current["doi"]:
            current["doi"] = doi_match.group(2)

    log(f"Found {len(records)} unique publications")
    for record in records:
        log(f"  {record['kitopen_id']}  {record['doi']:<35}  {record['title'][:70]}",
            verbose_only=True, verbose=verbose)
    return records


# ================================================== #
# STEP 2: DOWNLOAD BIBTEX RECORDS
# ================================================== #

# --- KITopen export
def fetch_kitopen_bibtex(session, kitopen_id, verbose=False):
    params = {"id": kitopen_id, "format": "bibtex"}
    log(f"    KITopen export {kitopen_id}", verbose_only=True, verbose=verbose)
    response = session.get(KITOPEN_EXPORT_URL, params=params, timeout=request_timeout)
    if response.status_code != 200:
        return ""
    # the export is UTF-8, set it explicitly to keep umlauts correct
    response.encoding = "utf-8"
    return response.text.strip()


# --- DOI fallback
def fetch_doi_bibtex(session, doi, verbose=False):
    if not doi:
        return ""
    log(f"    DOI fallback {doi}", verbose_only=True, verbose=verbose)
    headers = {"Accept": "application/x-bibtex; charset=utf-8"}
    response = session.get(DOI_URL + doi, headers=headers, timeout=request_timeout)
    if response.status_code != 200:
        return ""
    response.encoding = "utf-8"
    return response.text.strip()


# --- one record with cache
def get_bibtex(session, record, refresh=False, verbose=False):
    cache_file = CACHE_DIR / f"{record['kitopen_id']}.bib"

    # reuse cached record unless refresh is requested
    if cache_file.exists() and not refresh:
        cached_text = cache_file.read_text(encoding="utf-8")
        if is_valid_bibtex(cached_text):
            return cached_text, "cache"

    bib_text = ""
    source = ""

    try:
        bib_text = fetch_kitopen_bibtex(session, record["kitopen_id"], verbose)
        source = "kitopen"
    except requests.RequestException as error:
        log(f"    KITopen error: {error}", verbose_only=True, verbose=verbose)

    if not is_valid_bibtex(bib_text):
        try:
            bib_text = fetch_doi_bibtex(session, record["doi"], verbose)
            source = "doi"
        except requests.RequestException as error:
            log(f"    DOI error: {error}", verbose_only=True, verbose=verbose)

    if not is_valid_bibtex(bib_text):
        return "", "failed"

    cache_file.write_text(bib_text + "\n", encoding="utf-8")
    return bib_text, source


# ================================================== #
# STEP 3: CLEAN AND MERGE
# ================================================== #

def prepare_entry(bib_text, kitopen_id, used_keys):
    # keep only the first entry of the export
    head_match = entry_head_pattern.search(bib_text)
    entry_text = bib_text[head_match.start():]
    next_match = entry_head_pattern.search(entry_text, pos=1)
    if next_match:
        entry_text = entry_text[:next_match.start()].rstrip()

    head_match = entry_head_pattern.match(entry_text)
    entry_type = head_match.group(1)
    cite_key = head_match.group(2) or f"kitopen{kitopen_id}"

    # make the citation key unique across the merged file
    base_key = cite_key
    suffix_index = 0
    while cite_key.lower() in used_keys:
        suffix_index += 1
        cite_key = f"{base_key}_{chr(96 + suffix_index)}"
    used_keys.add(cite_key.lower())

    # rebuild the head line and add the KITopen id for later linking
    new_head = f"@{entry_type}{{{cite_key},"
    body_text = entry_text[head_match.end():]
    if "kitopen_id" not in body_text.lower():
        new_head += f"\n  kitopen_id = {{{kitopen_id}}},"

    return new_head + body_text.rstrip() + "\n"


def write_master_bib(entries, total_found):
    header_lines = [
        "% imkaaf_master.bib",
        f"% source page : {PAGE_URL}",
        f"% generated   : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"% entries     : {len(entries)} of {total_found} publications",
        "",
    ]
    with open(OUTPUT_BIB, "w", encoding="utf-8") as bib_file:
        bib_file.write("\n".join(header_lines) + "\n")
        bib_file.write("\n".join(entries))


def write_failed_csv(failed_records):
    with open(FAILED_CSV, "w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=["kitopen_id", "doi", "title"])
        writer.writeheader()
        writer.writerows(failed_records)


# ================================================== #
# MAIN
# ================================================== #

def parse_arguments():
    parser = argparse.ArgumentParser(description="Merge IMK-AAF publication BibTeX records.")
    parser.add_argument("--dry-run", action="store_true", help="only list found publications")
    parser.add_argument("--verbose", action="store_true", help="print every request")
    parser.add_argument("--refresh", action="store_true", help="ignore cached records")
    parser.add_argument("--limit", type=int, default=0, help="process only the first N records")
    parser.add_argument("--delay", type=float, default=request_delay, help="seconds between requests")
    return parser.parse_args()


def main():
    args = parse_arguments()

    # --- folders
    MASTER_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    session = build_session()

    # --- scrape
    try:
        records = scrape_publications(session, verbose=args.verbose or args.dry_run)
    except requests.RequestException as error:
        sys.exit(f"Could not load publication page: {error}")

    if args.limit > 0:
        records = records[:args.limit]
        log(f"Limited to first {len(records)} records")

    if args.dry_run:
        log("Dry run finished, nothing downloaded.")
        return

    # --- download
    entries = []
    failed_records = []
    used_keys = set()
    source_counts = {"cache": 0, "kitopen": 0, "doi": 0, "failed": 0}

    for index, record in enumerate(records, start=1):
        log(f"[{index}/{len(records)}] {record['kitopen_id']}  {record['title'][:60]}")
        bib_text, source = get_bibtex(session, record, refresh=args.refresh, verbose=args.verbose)
        source_counts[source] += 1

        if source == "failed":
            log("    FAILED")
            failed_records.append(record)
        else:
            entries.append(prepare_entry(bib_text, record["kitopen_id"], used_keys))

        # be polite to the server, no pause needed for cached records
        if source != "cache":
            time.sleep(args.delay)

    # --- save
    write_master_bib(entries, len(records))
    if failed_records:
        write_failed_csv(failed_records)
    elif FAILED_CSV.exists():
        FAILED_CSV.unlink()

    # --- summary
    log("")
    log(f"Saved {len(entries)} entries to {OUTPUT_BIB}")
    log(f"  from cache  : {source_counts['cache']}")
    log(f"  from KITopen: {source_counts['kitopen']}")
    log(f"  from DOI    : {source_counts['doi']}")
    log(f"  failed      : {source_counts['failed']}")
    if failed_records:
        log(f"Failed records listed in {FAILED_CSV}")


if __name__ == "__main__":
    main()
