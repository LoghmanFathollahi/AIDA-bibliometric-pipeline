'''
3_b_aida_bib_figures.py

   Draws the plots and maps of the AIDA publication statistics as PDF
   files: one folder per researcher of AIDA Scientists.txt and one folder
   for all of AIDA.

Purpose

   The figures show what the Excel workbooks of 3_b_aida_bib_excel.py hold
   as tables. The numbers are computed by the same functions of that script,
   so a figure and its sheet always agree. The data is the dataset of
   3_b_aida_bib_authors.py, read from its caches.

   Per researcher, named after the sheet they belong to:

      Summary_roles.pdf      papers by role: first and corresponding, first only,
                             corresponding not first, co-author
      Per_year.pdf           papers per year, stacked by role
      Journals.pdf           the journals with the most papers, top 15
      Co-authors.pdf         the co-authors with the most joint papers, top 15
      Institutions.pdf       the institutions with the most joint papers, top 15
      Countries.pdf          papers per country
      Map_Affiliations.pdf   his own institutions, sized by papers
      Map_Co-authors.pdf     his co-authors' institutions, with lines from his
                             main institution
      Map_Institutions.pdf   every institution on his papers, his own marked
      Map_Countries.pdf      world map, countries shaded by papers

   For all of AIDA: Per_year, Journals, Institutions, Authors, Countries and
   Researchers (every researcher's papers by role), and the maps
   Map_Institutions, Map_Authors (every author at his main institution),
   Map_Countries and Map_Researchers (the main institution of every
   researcher).

   Institutions are placed with the coordinates 3_b_aida_bib_authors.py
   collected (OpenAlex, the manual coordinates file, the coordinates table,
   cached ROR matches). Without that run no institution has coordinates and
   the institution maps are skipped, with a warning on the screen. Nothing
   is written on the figures themselves; Notes.txt in each folder says how
   many institutions every map shows and which countries are not on the map.

   The country outlines are the Natural Earth file 3_b keeps in its cache.
   When the file is missing it is downloaded once (about 750 kB, no key).

   The plot style of config.py (MPL_STYLE, LoFaStyle.mplstyle) is used when
   the file exists.

Usage

   python 3_b_aida_bib_figures.py
      all researchers and all of AIDA, as PDF files

   python 3_b_aida_bib_figures.py --only "Robert Wagner"
      one researcher only, can be repeated; the AIDA figures are skipped

   python 3_b_aida_bib_figures.py --format png --dpi 300
      PNG images instead of PDF files

   python 3_b_aida_bib_figures.py --no-download
      never download the country outlines

   Run 3_a_aida_master_bib.py and 3_b_aida_bib_authors.py first.
   3_b_aida_bib_excel.py must be next to this script. All paths come from
   config.py, the same on Linux and Windows.

Output

   <root>/output/figures/AIDA_Master/*.pdf             all of AIDA (PDF, vector graphics)
   <root>/output/figures/authors/<First_Last>/*.pdf    one folder per researcher (PDF)
   <folder>/Notes.txt                                  per folder: institutions placed, countries not on the map (text)
   a line per researcher on the screen: figures written, institutions placed
'''

# ================================================== #
# IMPORTS
# ================================================== #

import argparse
import datetime
import importlib.util
import json
import math
import os
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker
    import matplotlib.colors
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MplPath
except ImportError:
    sys.exit("matplotlib is missing, install it with:  python -m pip install matplotlib")

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
EXCEL_SCRIPT_PATH = SCRIPT_DIR / "3_b_aida_bib_excel.py"
AUTHORS_DIR = Path(getattr(config, "AUTHORS_HTML_DIR", BASE_DIR / "output" / "authors"))
CACHE_DIR = AUTHORS_DIR / "cache"
WORLD_FILE = CACHE_DIR / "world_countries_50m.json"
MANUAL_COORDS_FILE = AUTHORS_DIR / "affiliation_coords_manual.csv"
INSTITUTION_COORDS_FILE = Path(getattr(config, "INSTITUTION_COORDS_FILE",
                                       BASE_DIR / "info" / "institution_coords.csv"))
FIGURES_DIR = Path(getattr(config, "FIGURES_OUTPUT_DIR", BASE_DIR / "output" / "figures"))
FIGURES_AUTHORS_DIR = Path(getattr(config, "FIGURES_AUTHORS_DIR", FIGURES_DIR / "authors"))
FIGURES_MASTER_DIR = Path(getattr(config, "FIGURES_MASTER_DIR", FIGURES_DIR / "AIDA_Master"))
MPL_STYLE = getattr(config, "MPL_STYLE", None)
# the same outlines 3_b_aida_bib_authors.py downloads, fetched here only when its cache has none
world_map_url = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json"

# --- settings
# PDF keeps text and lines sharp at any size; PNG is there for slides and web pages
figure_format_default = "pdf"
figure_formats = ("pdf", "png")
dpi_default = 200
top_n = 15
label_length = 48
# colours of the four roles, the same in every figure (Okabe and Ito, readable for colour blindness)
role_colors = ["#0072B2", "#56B4E9", "#E69F00", "#B8B8B8"]
main_color = "#00705F"
other_color = "#E69F00"
land_color = "#ECEFF0"
# lower edges of the paper classes of the country maps
class_edges = [1, 2, 3, 5, 10, 20, 50, 100, 200, 500, 1000]
border_color = "#B9C3C6"

# --- left out of the maps: it circles the pole and cannot be drawn flat, and has no institutions
skipped_countries = {"Antarctica"}

# --- country names of our data that the Natural Earth file writes differently
country_aliases = {
    "united states": "United States of America", "usa": "United States of America",
    "czech republic": "Czechia", "russian federation": "Russia",
    "korea, republic of": "South Korea", "republic of korea": "South Korea", "korea": "South Korea",
    "viet nam": "Vietnam", "iran, islamic republic of": "Iran",
    "democratic republic of the congo": "Dem. Rep. Congo", "bosnia and herzegovina": "Bosnia and Herz.",
    "dominican republic": "Dominican Rep.", "central african republic": "Central African Rep.",
    "north macedonia": "Macedonia", "cote d'ivoire": "Côte d'Ivoire", "ivory coast": "Côte d'Ivoire",
    "eswatini": "eSwatini", "swaziland": "eSwatini", "taiwan, province of china": "Taiwan",
    "the netherlands": "Netherlands", "england": "United Kingdom", "scotland": "United Kingdom",
    "wales": "United Kingdom", "northern ireland": "United Kingdom", "uk": "United Kingdom",
    "peoples r china": "China", "people's republic of china": "China",
}


# ================================================== #
# THE EXCEL SCRIPT AND THE DATASET
# ================================================== #

def load_module(SCRIPT_PATH, name):
    # the scripts start with a digit, so they are loaded by path
    if not SCRIPT_PATH.is_file():
        sys.exit("Needed next to this script: %s" % SCRIPT_PATH.name)
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OfflineClient:
    # given to 3_b's ROR lookup: cached matches are used, nothing is downloaded
    enabled = False
    offline = True


def build_data(module, excel, verbose):
    # the steps of 3_b_aida_bib_authors.py, from its caches, with the coordinates
    if not excel.INPUT_BIB_PATH.is_file():
        sys.exit("Master bib not found: %s\nRun 3_a_aida_master_bib.py first." % excel.INPUT_BIB_PATH)
    entries = module.parse_bibtex(excel.INPUT_BIB_PATH.read_text(encoding="utf-8", errors="replace"), verbose)
    records = [module.build_base_record(entry) for entry in entries]
    print("Read %d papers from %s" % (len(records), excel.INPUT_BIB_PATH))

    works_cache = module.load_cache(CACHE_DIR / "openalex_works.json")
    institution_cache = module.load_cache(CACHE_DIR / "openalex_institutions.json")
    if not works_cache:
        print("   WARNING: no OpenAlex cache in %s, run 3_b_aida_bib_authors.py first" % CACHE_DIR)
    alias_keys, forced_names = module.load_author_aliases(AUTHORS_DIR / "author_aliases.csv")
    author_registry = module.AuthorRegistry(alias_keys, forced_names)
    affiliation_registry = module.AffiliationRegistry(
        module.load_affiliation_aliases(AUTHORS_DIR / "affiliation_aliases.csv"))
    overrides = module.load_corresponding_overrides(AUTHORS_DIR / "corresponding_overrides.csv")
    papers, journals, raw_texts, _ = module.build_dataset(
        records, works_cache, institution_cache, author_registry, affiliation_registry, overrides)

    # --- coordinates, in the order 3_b uses: manual file, OpenAlex, coordinates table, ROR
    module.locate_affiliations(affiliation_registry, institution_cache,
                               module.load_coordinate_table(MANUAL_COORDS_FILE),
                               module.load_coordinate_table(INSTITUTION_COORDS_FILE),
                               module.load_cache(CACHE_DIR / "ror_affiliations.json"),
                               OfflineClient(), False, True, verbose)
    remap = affiliation_registry.merge_by_ror()
    data = module.finalize_data(papers, journals, raw_texts, author_registry,
                                affiliation_registry, remap, excel.INPUT_BIB_PATH.name)
    confirmed = {entry["key"]: module.plain(entry["fields"].get("aida_confirmed_by", "")) for entry in entries}
    for paper in data["papers"]:
        paper["cb"] = confirmed.get(paper["k"], "")
    placed = sum(1 for aff in data["affs"] if aff["lat"] is not None)
    print("   %d authors, %d institutions, %d of them with coordinates"
          % (len(data["authors"]), len(data["affs"]), placed))
    return data, author_registry


# ================================================== #
# WORLD OUTLINES
# ================================================== #

def download_world():
    # one download of about 750 kB, saved where 3_b_aida_bib_authors.py keeps it
    import urllib.request
    try:
        with urllib.request.urlopen(world_map_url, timeout=60) as response:
            payload = response.read()
        json.loads(payload.decode("utf-8"))
    except Exception as error:
        print("   country outlines could not be downloaded (%s)" % error)
        return False
    WORLD_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORLD_FILE.write_bytes(payload)
    print("   country outlines downloaded to %s" % WORLD_FILE)
    return True


def load_world(allow_download=True):
    # the TopoJSON of 3_b's cache: shared borders are stored once as "arcs",
    # delta coded and scaled; a country is a list of rings made of arcs
    # returns {country name: matplotlib path}, or {} when the file is missing
    if not WORLD_FILE.is_file() and not (allow_download and download_world()):
        print("   WARNING: no country outlines in %s" % WORLD_FILE)
        print("   the country maps are skipped, the institution maps have no land drawn")
        return {}
    topology = json.loads(WORLD_FILE.read_text(encoding="utf-8"))
    transform = topology.get("transform")
    arcs = []
    for arc in topology["arcs"]:
        if transform:
            (scale_x, scale_y), (shift_x, shift_y) = transform["scale"], transform["translate"]
            x = y = 0
            points = []
            for step_x, step_y in arc:
                x += step_x
                y += step_y
                points.append((x * scale_x + shift_x, y * scale_y + shift_y))
        else:
            points = [tuple(point) for point in arc]
        arcs.append(points)

    def ring(indices):
        # a negative index ~i means arc i backwards; arcs share their end points
        points = []
        for index in indices:
            arc = arcs[index] if index >= 0 else arcs[~index][::-1]
            points.extend(arc if not points else arc[1:])
        # a ring that crosses the date line jumps from +180 to -180; without this
        # it would be drawn as a band across the whole map (Russia, Fiji, Alaska)
        unwrapped = points[:1]
        for lon, lat in points[1:]:
            previous = unwrapped[-1][0]
            while lon - previous > 180:
                lon -= 360
            while lon - previous < -180:
                lon += 360
            unwrapped.append((lon, lat))
        return unwrapped

    world = {}
    for geometry in topology["objects"]["countries"]["geometries"]:
        if geometry.get("type") == "Polygon":
            rings = [ring(indices) for indices in geometry["arcs"]]
        elif geometry.get("type") == "MultiPolygon":
            rings = [ring(indices) for polygon in geometry["arcs"] for indices in polygon]
        else:
            continue
        name = (geometry.get("properties") or {}).get("name", "")
        if name in skipped_countries:
            continue
        vertices, codes = [], []
        for points in rings:
            if len(points) < 3:
                continue
            # the part beyond the date line is drawn again on the other side of the map
            shifts = [0]
            if max(lon for lon, _ in points) > 180:
                shifts.append(-360)
            if min(lon for lon, _ in points) < -180:
                shifts.append(360)
            for shift in shifts:
                moved = [(lon + shift, lat) for lon, lat in points]
                vertices += moved + [moved[0]]
                codes += [MplPath.MOVETO] + [MplPath.LINETO] * (len(moved) - 1) + [MplPath.CLOSEPOLY]
        if vertices:
            world[name] = MplPath(vertices, codes)
    return world


def world_name(country, world):
    # our country name as the Natural Earth file writes it, or "" when unknown
    if not country:
        return ""
    if country in world:
        return country
    folded = country.strip().lower()
    alias = country_aliases.get(folded)
    if alias and alias in world:
        return alias
    for name in world:
        if name.lower() == folded:
            return name
    return ""


# ================================================== #
# DRAWING HELPERS
# ================================================== #

def apply_style():
    # your own style file when it exists; LaTeX text is switched off when LaTeX is not installed
    if MPL_STYLE and Path(MPL_STYLE).is_file():
        try:
            plt.style.use(str(MPL_STYLE))
        except (OSError, ValueError) as error:
            print("   style file not used (%s)" % error)
        if plt.rcParams.get("text.usetex") and not shutil.which("latex"):
            plt.rcParams["text.usetex"] = False


# the format of this run, set in main
output = {"format": figure_format_default}
# the notes of every figure, by folder, written to Notes.txt at the end
notes = defaultdict(list)


def short(text, length=label_length):
    text = str(text)
    return text if len(text) <= length else text[:length - 3] + "..."


def footer(figure, note):
    # nothing is written on the figure; the note (how many institutions were placed,
    # which countries are not on the map) is kept for Notes.txt of the folder
    # the first part (name, bibliography, date) is the same everywhere and is left out
    figure._aida_note = "; ".join(note.split("; ")[1:])


def save(figure, FILE_PATH, dpi):
    # FILE_PATH has no suffix, the format of this run is added here
    FILE_PATH = FILE_PATH.with_name(FILE_PATH.name + "." + output["format"])
    FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(str(FILE_PATH), dpi=dpi, bbox_inches="tight", format=output["format"])
    note = getattr(figure, "_aida_note", "")
    if note:
        notes[FILE_PATH.parent].append("%s: %s" % (FILE_PATH.name, note))
    plt.close(figure)


def bar_chart(rows, title, xlabel, FILE_PATH, note, dpi, color=main_color):
    # rows: (label, value), drawn as horizontal bars, the largest on top
    rows = [row for row in rows if row[1]][:top_n]
    if not rows:
        return False
    figure, axis = plt.subplots(figsize=(8, 0.38 * len(rows) + 1.6))
    labels = [short(label) for label, _ in rows][::-1]
    values = [value for _, value in rows][::-1]
    axis.barh(labels, values, color=color)
    for position, value in enumerate(values):
        axis.text(value, position, " %d" % value, va="center", fontsize=8)
    axis.set_title(title)
    axis.set_xlabel(xlabel)
    axis.xaxis.get_major_locator().set_params(integer=True)
    axis.spines[["top", "right"]].set_visible(False)
    footer(figure, note)
    save(figure, FILE_PATH, dpi)
    return True


def stacked_bars(categories, series, labels, title, FILE_PATH, note, dpi, horizontal=False):
    # series: one list of values per label, stacked in the role colours
    if not categories:
        return False
    if horizontal:
        figure, axis = plt.subplots(figsize=(8, 0.38 * len(categories) + 1.8))
        left = [0] * len(categories)
        for values, label, color in zip(series, labels, role_colors):
            axis.barh(categories[::-1], values[::-1], left=left[::-1], label=label, color=color)
            left = [a + b for a, b in zip(left, values)]
        axis.set_xlabel("Papers")
        axis.xaxis.get_major_locator().set_params(integer=True)
    else:
        figure, axis = plt.subplots(figsize=(max(6, 0.32 * len(categories) + 2), 4.2))
        bottom = [0] * len(categories)
        for values, label, color in zip(series, labels, role_colors):
            axis.bar(categories, values, bottom=bottom, label=label, color=color)
            bottom = [a + b for a, b in zip(bottom, values)]
        axis.set_ylabel("Papers")
        axis.yaxis.get_major_locator().set_params(integer=True)
        if len(categories) > 12:
            axis.tick_params(axis="x", labelrotation=90)
        # a few years keep a normal bar width instead of filling the whole figure
        padding = max(0.0, (6 - len(categories)) / 2)
        axis.set_xlim(-0.5 - padding, len(categories) - 0.5 + padding)
    axis.set_title(title)
    # the legend sits beside the bars, so it never covers them
    axis.legend(frameon=False, fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    axis.spines[["top", "right"]].set_visible(False)
    footer(figure, note)
    save(figure, FILE_PATH, dpi)
    return True


def draw_land(axis, world, highlight=None):
    # every country in light grey; highlight: {world name: colour}
    for name, path in world.items():
        color = (highlight or {}).get(name, land_color)
        axis.add_patch(PathPatch(path, facecolor=color, edgecolor=border_color, linewidth=0.3, zorder=1))


def frame_points(axis, longitudes, latitudes):
    # the view around the points, never smaller than 30 by 18 degrees
    west, east = min(longitudes), max(longitudes)
    south, north = min(latitudes), max(latitudes)
    width = max(30.0, (east - west) * 1.25)
    height = max(18.0, (north - south) * 1.25)
    middle_x, middle_y = (west + east) / 2, (south + north) / 2
    axis.set_xlim(max(-180, middle_x - width / 2), min(180, middle_x + width / 2))
    axis.set_ylim(max(-60, middle_y - height / 2), min(85, middle_y + height / 2))
    # an equal area of the map looks equal, the meridians are drawn closer near the poles
    axis.set_aspect(1 / max(0.2, math.cos(math.radians(middle_y))))


def finish_map(figure, axis, title, note):
    axis.set_title(title)
    axis.set_xticks([])
    axis.set_yticks([])
    for spine in axis.spines.values():
        spine.set_edgecolor(border_color)
    axis.set_facecolor("#F7FAFB")
    footer(figure, note)


def point_map(points, title, FILE_PATH, note, dpi, world, lines_from=None, legend=None):
    # points: (lon, lat, size value, label, colour); labels are written for the largest eight
    if not points:
        return False
    figure, axis = plt.subplots(figsize=(10, 6))
    draw_land(axis, world)
    largest = max(point[2] for point in points) or 1
    if lines_from:
        for lon, lat, value, _, _ in points:
            if (lon, lat) != lines_from:
                axis.plot([lines_from[0], lon], [lines_from[1], lat], color=main_color,
                          alpha=0.15 + 0.5 * value / largest, linewidth=0.6 + 1.6 * value / largest, zorder=2)
    for lon, lat, value, label, color in sorted(points, key=lambda point: point[2]):
        axis.scatter(lon, lat, s=25 + 260 * math.sqrt(value / largest), color=color,
                     edgecolor="white", linewidth=0.6, alpha=0.9, zorder=3)
    for lon, lat, value, label, _ in sorted(points, key=lambda point: -point[2])[:8]:
        axis.annotate("\n".join(short(line, 32) for line in label.split("\n")), (lon, lat), xytext=(5, 4), textcoords="offset points",
                      fontsize=7, zorder=4)
    frame_points(axis, [point[0] for point in points], [point[1] for point in points])
    if legend:
        for label, color in legend:
            axis.scatter([], [], color=color, s=40, label=label)
        axis.legend(frameon=False, fontsize=8, loc="lower left")
    finish_map(figure, axis, title, note)
    save(figure, FILE_PATH, dpi)
    return True


def country_map(counts, title, FILE_PATH, note, dpi, world):
    # counts: {country: papers}; the world map with the countries shaded by papers
    if not world or not counts:
        return False, []
    matched = {}
    missing = []
    for country, value in counts.items():
        name = world_name(country, world)
        if name:
            matched[name] = matched.get(name, 0) + value
        else:
            missing.append(country)
    if not matched:
        return False, missing
    if missing:
        note += "; not on the map: " + ", ".join(sorted(missing))
    # classes of papers (1, 2, 3 to 4, 5 to 9, ...), one shade of one colour each, darker for more;
    # the map and the colour bar use the same classes, so a colour always means the same count
    largest = max(matched.values())
    edges = [edge for edge in class_edges if edge <= largest] + [largest + 1]
    colors = [plt.get_cmap("YlOrRd")(0.3 + 0.7 * step / max(1, len(edges) - 2)) for step in range(len(edges) - 1)]
    colormap = matplotlib.colors.ListedColormap(colors)
    norm = matplotlib.colors.BoundaryNorm(edges, colormap.N)
    shade = {name: colormap(norm(value)) for name, value in matched.items()}
    figure, axis = plt.subplots(figsize=(11, 5.6))
    draw_land(axis, world, shade)
    axis.set_xlim(-180, 180)
    axis.set_ylim(-58, 85)
    axis.set_aspect(1.15)
    mappable = plt.cm.ScalarMappable(cmap=colormap, norm=norm)
    colorbar = figure.colorbar(mappable, ax=axis, shrink=0.6, pad=0.01, spacing="uniform")
    colorbar.set_label("Papers")
    colorbar.set_ticks([(low + high) / 2 for low, high in zip(edges, edges[1:])])
    colorbar.set_ticklabels([str(low) if high - low == 1 else "%d to %d" % (low, high - 1)
                             for low, high in zip(edges, edges[1:])])
    colorbar.ax.tick_params(length=0)
    finish_map(figure, axis, title, note)
    save(figure, FILE_PATH, dpi)
    return True, missing


# ================================================== #
# THE FIGURES OF ONE RESEARCHER
# ================================================== #

def institution_points(data, counts, color_of=None):
    # counts: {institution index: value}; returns the points and how many have no coordinates
    points = []
    not_placed = 0
    for index, value in counts.items():
        aff = data["affs"][index]
        if aff["lat"] is None or aff["lon"] is None:
            not_placed += 1
            continue
        color = color_of(index) if color_of else main_color
        points.append((aff["lon"], aff["lat"], value, aff["n"], color))
    return points, not_placed


def placed_note(points, not_placed, what):
    total = len(points) + not_placed
    return "%d of %d %s placed%s" % (len(points), total, what,
                                     "" if not not_placed else ", %d without coordinates" % not_placed)


def researcher_figures(excel, module, data, author, name, papers, FOLDER, dpi, world, built):
    note_base = "%s, AIDA master bibliography, %s" % (name, built)
    written = []

    # --- roles, per year
    numbers = excel.researcher_summary(module, data, author, papers)
    if bar_chart(list(zip(excel.role_titles, numbers["roles"])), "%s: papers by role" % name, "Papers",
                 FOLDER / "Summary_roles", note_base, dpi, color=role_colors[0]):
        written.append("Summary_roles")
    years = excel.year_rows(module, papers, author)
    categories = [str(row[0]) for row in years]
    series = [[row[2 + role] for row in years] for role in range(4)]
    if stacked_bars(categories, series, excel.role_titles, "%s: papers per year" % name,
                    FOLDER / "Per_year", note_base, dpi):
        written.append("Per_year")

    # --- the top lists, from the same tables as the Excel sheets
    for title, rows, file_name in [
            ("journals", [(row[0], row[1]) for row in excel.journal_rows(data, papers)], "Journals"),
            ("co-authors, joint papers", [(row[0], row[1]) for row in excel.coauthor_rows(data, papers, author)],
             "Co-authors"),
            ("institutions, joint papers",
             [(row[0], row[4]) for row in excel.institution_rows(data, papers, author)], "Institutions"),
            ("papers per country", [(row[0], row[1]) for row in excel.country_rows(data, papers, author)],
             "Countries")]:
        if bar_chart(rows, "%s: %s" % (name, title), "Papers", FOLDER / file_name, note_base, dpi):
            written.append(file_name)

    # --- map of his own institutions
    own = Counter()
    for paper in papers:
        for index in paper["aa"][paper["a"].index(author)]:
            own[index] += 1
    points, not_placed = institution_points(data, own)
    if point_map(points, "%s: his affiliations" % name, FOLDER / "Map_Affiliations",
                 "%s; %s" % (note_base, placed_note(points, not_placed, "affiliations")), dpi, world):
        written.append("Map_Affiliations")
    placed_own = len(points), len(points) + not_placed

    # --- map of his co-authors' institutions, lines from his main institution
    coauthor_places = defaultdict(set)
    for paper in papers:
        for position, person in enumerate(paper["a"]):
            if person != author:
                for index in paper["aa"][position]:
                    coauthor_places[index].add(person)
    points, not_placed = institution_points(data, {index: len(people) for index, people in coauthor_places.items()})
    home = next((data["affs"][index] for index, _ in own.most_common() if data["affs"][index]["lat"] is not None), None)
    if point_map(points, "%s: where his co-authors work" % name, FOLDER / "Map_Co-authors",
                 "%s; point size: co-authors; %s" % (note_base, placed_note(points, not_placed, "institutions")),
                 dpi, world, lines_from=(home["lon"], home["lat"]) if home else None):
        written.append("Map_Co-authors")

    # --- map of every institution on his papers, his own in the main colour
    every = Counter()
    for paper in papers:
        for index in set(index for ids in paper["aa"] for index in ids) | set(paper["pa"]):
            every[index] += 1
    points, not_placed = institution_points(data, every, lambda index: main_color if index in own else other_color)
    if point_map(points, "%s: institutions on his papers" % name, FOLDER / "Map_Institutions",
                 "%s; point size: joint papers; %s" % (note_base, placed_note(points, not_placed, "institutions")),
                 dpi, world, legend=[("his own", main_color), ("co-authors'", other_color)]):
        written.append("Map_Institutions")

    # --- world map of the countries
    countries = {row[0]: row[1] for row in excel.country_rows(data, papers, author) if row[0] != excel.unknown}
    done, _ = country_map(countries, "%s: papers per country" % name, FOLDER / "Map_Countries",
                          note_base, dpi, world)
    if done:
        written.append("Map_Countries")
    return written, placed_own


# ================================================== #
# THE FIGURES OF ALL OF AIDA
# ================================================== #

def master_figures(excel, module, data, researchers, FOLDER, dpi, world, built):
    papers = data["papers"]
    note = "AIDA master bibliography, %s" % built
    written = []

    years = excel.year_rows(module, papers, None)
    if bar_chart_years(years, FOLDER / "Per_year", note, dpi):
        written.append("Per_year")

    # --- every author's paper count, as in the Authors sheet
    author_counts = Counter(person for paper in papers for person in paper["a"])
    for title, rows, file_name in [
            ("AIDA: journals", [(row[0], row[1]) for row in excel.journal_rows(data, papers)], "Journals"),
            ("AIDA: institutions", [(row[0], row[3]) for row in excel.institution_rows(data, papers, None)],
             "Institutions"),
            ("AIDA: authors", [(data["authors"][person]["n"], count) for person, count in author_counts.most_common()],
             "Authors"),
            ("AIDA: papers per country", [(row[0], row[1]) for row in excel.country_rows(data, papers, None)],
             "Countries")]:
        if bar_chart(rows, title, "Papers", FOLDER / file_name, note, dpi):
            written.append(file_name)

    # --- the researchers side by side, stacked by role
    if researchers:
        names = [item["name"] for item in researchers]
        series = [[item["roles"][role] for item in researchers] for role in range(4)]
        if stacked_bars(names, series, excel.role_titles, "AIDA researchers: papers by role",
                        FOLDER / "Researchers", note, dpi, horizontal=True):
            written.append("Researchers")

    # --- maps
    every = Counter()
    for paper in papers:
        for index in set(index for ids in paper["aa"] for index in ids) | set(paper["pa"]):
            every[index] += 1
    points, not_placed = institution_points(data, every)
    if point_map(points, "AIDA: institutions", FOLDER / "Map_Institutions",
                 "%s; point size: papers; %s" % (note, placed_note(points, not_placed, "institutions")), dpi, world):
        written.append("Map_Institutions")
    # --- every author at his main institution, the one on most of his papers (Authors sheet)
    places = defaultdict(Counter)
    for paper in papers:
        for position, person in enumerate(paper["a"]):
            for index in paper["aa"][position]:
                places[person][index] += 1
    authors_at = Counter()
    for person, counts in places.items():
        home = next((index for index, _ in counts.most_common() if data["affs"][index]["lat"] is not None), None)
        if home is not None:
            authors_at[home] += 1
    points, _ = institution_points(data, authors_at)
    if point_map(points, "AIDA: authors by main institution", FOLDER / "Map_Authors",
                 "%s; point size: authors; %d of %d authors placed (%d without a known institution)"
                 % (note, sum(authors_at.values()), len(data["authors"]), len(data["authors"]) - len(places)),
                 dpi, world):
        written.append("Map_Authors")

    countries = {row[0]: row[1] for row in excel.country_rows(data, papers, None) if row[0] != excel.unknown}
    done, _ = country_map(countries, "AIDA: papers per country", FOLDER / "Map_Countries",
                          note, dpi, world)
    if done:
        written.append("Map_Countries")
    # researchers at the same institution share one point, labelled with all their names
    homes = [item for item in researchers if item["home"] is not None]
    by_home = defaultdict(list)
    for item in homes:
        by_home[item["home"]].append(item)
    points = []
    for index, items in by_home.items():
        aff = data["affs"][index]
        label = "\n".join([aff["n"]] + ["  " + item["name"] for item in items])
        points.append((aff["lon"], aff["lat"], sum(item["papers"] for item in items), label, main_color))
    if point_map(points, "AIDA researchers: main institution", FOLDER / "Map_Researchers",
                 "%s; point size: papers of the researchers there; %d of %d researchers placed"
                 % (note, len(homes), len(researchers)),
                 dpi, world):
        written.append("Map_Researchers")
    return written


def bar_chart_years(years, FILE_PATH, note, dpi):
    # all of AIDA per year, the confirmed papers darker
    if not years:
        return False
    categories = [str(row[0]) for row in years]
    confirmed = [row[2] for row in years]
    others = [row[1] - row[2] for row in years]
    return stacked_bars(categories, [confirmed, others],
                        ["Confirmed by an author's own list", "Other papers"],
                        "AIDA: papers per year", FILE_PATH, note, dpi)


# ================================================== #
# MAIN
# ================================================== #

def write_notes():
    # one Notes.txt per folder: institutions placed on each map, countries not on the map
    for FOLDER, lines in notes.items():
        (FOLDER / "Notes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def clear_folder(FOLDER):
    # the folders belong to this script; images of earlier runs are replaced
    FOLDER.mkdir(parents=True, exist_ok=True)
    for extension in figure_formats:
        for OLD_FILE in FOLDER.glob("*." + extension):
            OLD_FILE.unlink()
    if (FOLDER / "Notes.txt").is_file():
        (FOLDER / "Notes.txt").unlink()


def parse_arguments():
    parser = argparse.ArgumentParser(description="Plots and maps per AIDA researcher and for all of AIDA.")
    parser.add_argument("--only", action="append", default=[],
                        help='one researcher, as in AIDA Scientists.txt, e.g. --only "Robert Wagner"')
    parser.add_argument("--format", choices=figure_formats, default=figure_format_default,
                        help="file format of the figures (default: %(default)s)")
    parser.add_argument("--dpi", type=int, default=dpi_default,
                        help="resolution of PNG files (default: %(default)s); PDF files are vector graphics")
    parser.add_argument("--no-download", action="store_true",
                        help="do not download the country outlines when they are missing")
    parser.add_argument("--verbose", action="store_true", help="more details while reading")
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    excel = load_module(EXCEL_SCRIPT_PATH, "aida_bib_excel")
    module = excel.load_authors_module()
    data, author_registry = build_data(module, excel, arguments.verbose)
    output["format"] = arguments.format
    world = load_world(allow_download=not arguments.no_download)
    placed = sum(1 for aff in data["affs"] if aff["lat"] is not None)
    if not placed:
        print("   WARNING: none of the %d institutions has coordinates, so the institution maps are skipped"
              % len(data["affs"]))
        print("   run 3_b_aida_bib_authors.py first: it finds the coordinates in OpenAlex and ROR")
    apply_style()
    built = datetime.date.today().isoformat()

    names, NAMES_PATH = excel.researcher_names()
    if not names:
        sys.exit("No researchers found, checked %s" % NAMES_PATH)
    if arguments.only:
        wanted = {name.strip().lower() for name in arguments.only}
        names = [name for name in names if name.lower() in wanted]
        if not names:
            sys.exit("None of %s is in %s" % (", ".join(arguments.only), NAMES_PATH.name))
    if not arguments.only and FIGURES_AUTHORS_DIR.is_dir():
        # researchers removed from the list leave no old folder behind
        for OLD_FOLDER in FIGURES_AUTHORS_DIR.iterdir():
            if OLD_FOLDER.is_dir():
                shutil.rmtree(OLD_FOLDER, ignore_errors=True)

    print("Researchers: %d in %s" % (len(names), NAMES_PATH.name))
    researchers = []
    for name in names:
        key = author_registry.key_for(*module.split_name(name))
        author = author_registry.index_by_key.get(key)
        papers = [paper for paper in data["papers"] if author is not None and author in paper["a"]]
        if not papers:
            print("   %-28s no paper in the master, no figures" % name)
            continue
        FOLDER = FIGURES_AUTHORS_DIR / excel.file_name_for(name)
        clear_folder(FOLDER)
        written, (placed, total) = researcher_figures(excel, module, data, author, name, papers,
                                                      FOLDER, arguments.dpi, world, built)
        numbers = excel.researcher_summary(module, data, author, papers)
        own = Counter(index for paper in papers for index in paper["aa"][paper["a"].index(author)])
        home = next((index for index, _ in own.most_common() if data["affs"][index]["lat"] is not None), None)
        researchers.append({"name": name, "papers": numbers["papers"], "roles": numbers["roles"], "home": home})
        print("   %-28s %2d figures, %d of %d own institutions on the map" % (name, len(written), placed, total))
    print("Written: %d folders in %s" % (len(researchers), FIGURES_AUTHORS_DIR))

    if arguments.only:
        print("AIDA figures skipped, they are only made in a full run")
        write_notes()
        return
    researchers.sort(key=lambda item: (-item["papers"], item["name"]))
    clear_folder(FIGURES_MASTER_DIR)
    written = master_figures(excel, module, data, researchers, FIGURES_MASTER_DIR, arguments.dpi, world, built)
    print("Written: %d figures in %s" % (len(written), FIGURES_MASTER_DIR))
    write_notes()


if __name__ == "__main__":
    main()
