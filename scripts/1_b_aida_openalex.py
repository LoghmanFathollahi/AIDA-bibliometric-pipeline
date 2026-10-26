'''
1_b_aida_openalex.py

   Harvest publication records from the OpenAlex API for every AIDA
   researcher and write one BibTeX file per researcher.

Purpose

   Replace the manual Web of Science export step with an automated,
   API based harvest that produces the same folder layout of .bib files,
   so the existing merge and filter chain can be reused unchanged.
   Every name in AIDA Scientists.txt is resolved to its OpenAlex author
   identifiers and queried separately, one output file per researcher.

Input

   <root>/data/AIDA Scientists.txt   one researcher name per line

Usage

   python 1_b_aida_openalex.py
   python 1_b_aida_openalex.py --verbose
   python 1_b_aida_openalex.py --min-year 2000 --max-records 2000
   python 1_b_aida_openalex.py --loose-authors
   python 1_b_aida_openalex.py --dry-run

   <root> is the project root found by config.py, the same on Linux and
   Windows, so no path has to be given.

Output

   <root>/data/openalex/researchers/<slug>.bib   one file per researcher
   <root>/data/openalex/fetch_log.csv            per researcher status and counts
'''

import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

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
FALLBACK_ROOT = Path(os.environ.get('AIDA_ROOT') or (
    SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == 'scripts' else SCRIPT_DIR))

# --- contact address and KIT identifier
contact_email = getattr(config, 'CONTACT_EMAIL', 'loghman.fathollahi@kit.edu')
kit_ror = getattr(config, 'KIT_ROR', '04t3en479')

# --- input name list
DATA_DIR = Path(getattr(config, 'DATA_DIR', FALLBACK_ROOT / 'data'))
RESEARCHERS_FILE = Path(getattr(config, 'RESEARCHERS_FILE', DATA_DIR / 'AIDA Scientists.txt'))

# --- accepted spelling variants of the input file
# the harvest still starts if the file was renamed
researchers_file_variants = ['AIDA Scientists.txt', 'AIDA Scientists .txt', 'aida_scientists.txt']

# --- output locations, created automatically
OPENALEX_DIR = Path(getattr(config, 'OPENALEX_DATA_DIR', DATA_DIR / 'openalex'))
RESEARCHERS_OUT_DIR = Path(getattr(config, 'OPENALEX_RESEARCHERS_DIR', OPENALEX_DIR / 'researchers'))
LOG_FILE = Path(getattr(config, 'OPENALEX_FETCH_LOG', OPENALEX_DIR / 'fetch_log.csv'))

# --- api settings
api_base = 'https://api.openalex.org'
user_agent = 'aida-bibliometrics/1.0 (mailto:%s)' % contact_email
page_size = 200
request_pause = 0.15
max_retries = 4
retry_backoff = 3.0
timeout = 60

# --- defaults, all overridable from the command line
default_min_year = 1990
default_max_records = 1000

# --- OpenAlex work type to BibTeX entry type
entry_type_map = {
    'journal-article': 'article',
    'article': 'article',
    'review': 'article',
    'proceedings-article': 'inproceedings',
    'book-chapter': 'incollection',
    'book': 'book',
    'monograph': 'book',
    'dissertation': 'phdthesis',
    'report': 'techreport',
    'preprint': 'misc',
    'dataset': 'misc',
}


# ================================================== #
# HELPERS
# ================================================== #

def slugify(text):
    # strip accents, then reduce to lowercase words joined by underscores
    plain = unicodedata.normalize('NFKD', text)
    plain = plain.encode('ascii', 'ignore').decode('ascii')
    slug = re.sub(r'[^A-Za-z0-9]+', '_', plain).strip('_').lower()
    return slug or 'term'


def resolve_input_file(preferred, variants):
    # use the requested file when it exists
    # otherwise try the known spelling variants inside the same directory
    if preferred.is_file():
        return preferred
    for name in variants:
        candidate = preferred.parent / name
        if candidate.is_file():
            return candidate
    return preferred


def read_terms(path):
    # one term per line, blank lines and lines starting with # are ignored
    if not path.is_file():
        return []
    terms = []
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        terms.append(line)
    return terms


def escape_bibtex(value):
    # escape the characters BibTeX treats as special
    # non ascii characters are left as UTF-8, biblatex handles them directly
    text = str(value)
    for char in ['&', '%', '$', '#', '_']:
        text = text.replace(char, '\\' + char)
    return text.replace('{', '(').replace('}', ')')


def api_get(path, params, verbose=False):
    # single GET against OpenAlex with polite pool identification and retries
    query = dict(params)
    query['mailto'] = contact_email
    url = api_base + path + '?' + urllib.parse.urlencode(query)
    if verbose:
        print('   GET %s' % url)
    for attempt in range(1, max_retries + 1):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': user_agent})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode('utf-8'))
            time.sleep(request_pause)
            return payload
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                time.sleep(retry_backoff * attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < max_retries:
                time.sleep(retry_backoff * attempt)
                continue
            raise
    return None


# ================================================== #
# OPENALEX QUERIES
# ================================================== #

def count_works(filters, search=None, verbose=False):
    # single request that reads meta.count only
    # used by --dry-run so nothing is paged through
    params = {'filter': ','.join(filters), 'per-page': 1}
    if search:
        params['search'] = search
    payload = api_get('/works', params, verbose=verbose)
    if not payload:
        return 0
    return payload.get('meta', {}).get('count', 0)


def fetch_works(filters, search=None, max_records=default_max_records, verbose=False):
    # cursor paged retrieval of the works endpoint
    works = []
    cursor = '*'
    while cursor and len(works) < max_records:
        params = {
            'filter': ','.join(filters),
            'per-page': min(page_size, max_records - len(works)),
            'cursor': cursor,
        }
        if search:
            params['search'] = search
        payload = api_get('/works', params, verbose=verbose)
        if not payload:
            break
        works.extend(payload.get('results', []))
        cursor = payload.get('meta', {}).get('next_cursor')
    return works[:max_records]


def clean_search_term(term):
    # OpenAlex search filters reject wildcards and treat comma, pipe and colon
    # as filter syntax, so those characters are removed
    # the boolean words AND, OR and NOT are supported and stay untouched
    cleaned = term.replace('*', ' ')
    cleaned = re.sub(r'[,:|()]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def name_tokens(text):
    # split a name into comparable lowercase ascii tokens
    return [slugify(part) for part in re.split(r'[\s.,\-]+', text) if slugify(part)]


def name_matches(name, candidate):
    # the surname must appear in the candidate name or one of its variants
    # and one remaining token must start with the first name initial
    # this rejects the namesakes that the fuzzy author search returns
    wanted = name_tokens(name)
    if not wanted:
        return False
    surname = wanted[-1]
    initial = wanted[0][:1] if len(wanted) > 1 else ''
    variants = [candidate.get('display_name') or '']
    variants.extend(candidate.get('display_name_alternatives') or [])
    for variant in variants:
        tokens = name_tokens(variant)
        if surname not in tokens:
            continue
        if not initial:
            return True
        if any(token.startswith(initial) for token in tokens if token != surname):
            return True
    return False


def is_kit_author(candidate):
    # accept both the last known institution and the full affiliation history
    # so researchers who have since left KIT are still found
    institutions = list(candidate.get('last_known_institutions') or [])
    for affiliation in candidate.get('affiliations') or []:
        institution = affiliation.get('institution') or {}
        if institution:
            institutions.append(institution)
    for institution in institutions:
        ror = institution.get('ror') or ''
        display = (institution.get('display_name') or '').lower()
        if kit_ror in ror or 'karlsruhe institute of technology' in display:
            return True
    return False


def search_authors(query, verbose=False):
    # one author lookup, empty list on no result
    payload = api_get('/authors',
                      {'filter': 'display_name.search:%s' % clean_search_term(query),
                       'per-page': 50},
                      verbose=verbose)
    return payload.get('results', []) if payload else []


def resolve_author(name, prefer_kit=True, verbose=False):
    # map a plain name to OpenAlex author ids
    # the full name is tried first
    # older records are often indexed with initials only, for example
    # U. Schurath instead of Ulrich Schurath, so the surname alone is the
    # fallback query and the initial check then filters the candidates
    candidates = search_authors(name, verbose=verbose)
    tokens = name_tokens(name)
    if not candidates and len(tokens) > 1:
        candidates = search_authors(tokens[-1], verbose=verbose)
    if not candidates:
        return [], 'not_found'

    # --- keep only candidates whose name really matches
    named = [item for item in candidates if name_matches(name, item)]
    if not named:
        return [], 'name_mismatch'

    # --- prefer the KIT affiliated subset
    kit_matches = [item for item in named if is_kit_author(item)]
    if kit_matches:
        return [item['id'].rsplit('/', 1)[-1] for item in kit_matches], 'kit_match'
    if prefer_kit:
        return [], 'no_kit_match'
    return [named[0]['id'].rsplit('/', 1)[-1]], 'best_guess'


# ================================================== #
# BIBTEX CONVERSION
# ================================================== #

def build_entry_key(work, used_keys):
    # surname + year + first significant title word, made unique per file
    authorships = work.get('authorships') or []
    if authorships:
        display_name = authorships[0].get('author', {}).get('display_name', 'anon')
        surname = slugify(display_name.split()[-1])
    else:
        surname = 'anon'
    year = work.get('publication_year') or 'nodate'
    title_words = re.findall(r'[A-Za-z]{4,}', work.get('title') or '')
    tail = slugify(title_words[0]) if title_words else 'work'
    base = '%s%s_%s' % (surname, year, tail)
    key = base
    suffix = ord('a')
    while key in used_keys:
        key = base + chr(suffix)
        suffix += 1
    used_keys.add(key)
    return key


def work_to_bibtex(work, used_keys):
    # convert one OpenAlex work record into a single BibTeX entry
    entry_type = entry_type_map.get(work.get('type', ''), 'misc')
    key = build_entry_key(work, used_keys)
    fields = []

    authorships = work.get('authorships') or []
    authors = [item.get('author', {}).get('display_name') for item in authorships]
    authors = [name for name in authors if name]
    if authors:
        fields.append(('author', ' and '.join(authors)))
    if work.get('title'):
        fields.append(('title', work['title']))

    location = work.get('primary_location') or {}
    source = location.get('source') or {}
    venue = source.get('display_name')
    if venue:
        field_name = 'booktitle' if entry_type in ('inproceedings', 'incollection') else 'journal'
        fields.append((field_name, venue))
    if source.get('host_organization_name'):
        fields.append(('publisher', source['host_organization_name']))

    biblio = work.get('biblio') or {}
    if work.get('publication_year'):
        fields.append(('year', work['publication_year']))
    if biblio.get('volume'):
        fields.append(('volume', biblio['volume']))
    if biblio.get('issue'):
        fields.append(('number', biblio['issue']))
    first_page = biblio.get('first_page')
    last_page = biblio.get('last_page')
    if first_page and last_page:
        fields.append(('pages', '%s--%s' % (first_page, last_page)))
    elif first_page:
        fields.append(('pages', first_page))

    if work.get('doi'):
        doi = work['doi'].replace('https://doi.org/', '')
        fields.append(('doi', doi))
    if source.get('issn_l'):
        fields.append(('issn', source['issn_l']))

    # affiliation strings are kept so the KIT filter downstream still works
    affiliations = []
    for item in authorships:
        for institution in item.get('institutions') or []:
            if institution.get('display_name'):
                affiliations.append(institution['display_name'])
    if affiliations:
        unique_affiliations = list(dict.fromkeys(affiliations))
        fields.append(('affiliation', '; '.join(unique_affiliations)))

    concepts = [item.get('display_name') for item in (work.get('concepts') or [])[:8]]
    concepts = [name for name in concepts if name]
    if concepts:
        fields.append(('keywords', '; '.join(concepts)))

    fields.append(('note', 'OpenAlex: %s' % work.get('id', '')))

    lines = ['@%s{%s,' % (entry_type, key)]
    for name, value in fields:
        lines.append('  %s = {%s},' % (name, escape_bibtex(value)))
    lines[-1] = lines[-1].rstrip(',')
    lines.append('}')
    return '\n'.join(lines)


def write_bib_file(path, term, works):
    # deduplicate inside the file, then write the entries
    used_keys = set()
    seen_ids = set()
    entries = []
    for work in works:
        identifier = (work.get('doi') or work.get('id') or '').lower()
        if identifier and identifier in seen_ids:
            continue
        seen_ids.add(identifier)
        entries.append(work_to_bibtex(work, used_keys))
    header = [
        '% AIDA OpenAlex harvest',
        '%% researcher: %s' % term,
        '%% records: %d' % len(entries),
        '',
    ]
    path.write_text('\n'.join(header) + '\n\n'.join(entries) + '\n', encoding='utf-8')
    return len(entries)


# ================================================== #
# TERM PROCESSING
# ================================================== #

def process_researcher(name, out_dir, args, log_rows):
    out_path = out_dir / ('%s.bib' % slugify(name))

    # --- an existing file is left alone so an interrupted run can be resumed
    if out_path.is_file() and not args.overwrite and not args.dry_run:
        print('   %-35s exists, skipped' % name)
        log_rows.append({'kind': 'researcher', 'term': name, 'status': 'skipped',
                         'records': 0, 'file': str(out_path)})
        return

    author_ids, status = resolve_author(name, prefer_kit=not args.loose_authors,
                                        verbose=args.verbose)
    if not author_ids:
        print('   %-35s %s' % (name, status))
        log_rows.append({'kind': 'researcher', 'term': name, 'status': status,
                         'records': 0, 'file': ''})
        return
    filters = ['author.id:' + '|'.join(author_ids),
               'publication_year:>%d' % (args.min_year - 1)]

    if args.dry_run:
        available = count_works(filters, verbose=args.verbose)
        print('   %-35s %5d available, %d ids, %s' % (name, available,
                                                      len(author_ids), status))
        log_rows.append({'kind': 'researcher', 'term': name, 'status': status,
                         'records': available, 'file': ''})
        return

    works = fetch_works(filters, max_records=args.max_records, verbose=args.verbose)
    written = write_bib_file(out_path, name, works)
    print('   %-35s %5d records -> %s' % (name, written, out_path.name))
    log_rows.append({'kind': 'researcher', 'term': name, 'status': status,
                     'records': written, 'file': str(out_path)})


# ================================================== #
# MAIN
# ================================================== #

def run_term(handler, kind, term, out_dir, args, log_rows):
    # one researcher is one isolated unit of work
    # a failing request is logged and the harvest continues with the next name
    try:
        handler(term, out_dir, args, log_rows)
    except urllib.error.HTTPError as error:
        print('   %-35s http error %d' % (term, error.code))
        log_rows.append({'kind': kind, 'term': term,
                         'status': 'http_%d' % error.code, 'records': 0, 'file': ''})
    except (urllib.error.URLError, TimeoutError) as error:
        print('   %-35s network error, %s' % (term, error))
        log_rows.append({'kind': kind, 'term': term, 'status': 'network_error',
                         'records': 0, 'file': ''})
    except json.JSONDecodeError:
        print('   %-35s invalid response' % term)
        log_rows.append({'kind': kind, 'term': term, 'status': 'bad_response',
                         'records': 0, 'file': ''})


def parse_arguments():
    parser = argparse.ArgumentParser(description='Harvest AIDA researcher publications from OpenAlex.')
    parser.add_argument('--min-year', type=int, default=default_min_year,
                        help='earliest publication year to keep')
    parser.add_argument('--max-records', type=int, default=default_max_records,
                        help='record cap per search term')
    parser.add_argument('--loose-authors', action='store_true',
                        help='accept the best matching author even without a KIT affiliation')
    parser.add_argument('--researchers-file', type=Path, default=RESEARCHERS_FILE)
    parser.add_argument('--overwrite', action='store_true',
                        help='rewrite .bib files that already exist')
    parser.add_argument('--dry-run', action='store_true',
                        help='report available record counts without writing any file')
    parser.add_argument('--verbose', action='store_true',
                        help='print every API request')
    return parser.parse_args()


def main():
    args = parse_arguments()

    # --- create data/openalex/researchers
    for directory in (OPENALEX_DIR, RESEARCHERS_OUT_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    log_rows = []

    researchers_path = resolve_input_file(args.researchers_file, researchers_file_variants)
    researchers = read_terms(researchers_path)
    if not researchers:
        print('Researchers: no names found, checked %s' % researchers_path)
    else:
        print('Researchers: %d names from %s' % (len(researchers), researchers_path))
    for name in researchers:
        run_term(process_researcher, 'researcher', name,
                 RESEARCHERS_OUT_DIR, args, log_rows)

    # --- summary
    total_records = sum(row['records'] for row in log_rows)
    statuses = Counter(row['status'] for row in log_rows)
    print('')
    print('Names queried : %d' % len(log_rows))
    print('Records total : %d' % total_records)
    for status, count in sorted(statuses.items()):
        print('   %-15s %d' % (status, count))

    if not args.dry_run and log_rows:
        with LOG_FILE.open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.DictWriter(handle, fieldnames=['kind', 'term', 'status',
                                                        'records', 'file'])
            writer.writeheader()
            writer.writerows(log_rows)
        print('Log written   : %s' % LOG_FILE)


if __name__ == '__main__':
    sys.exit(main())
