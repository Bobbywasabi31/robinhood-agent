#!/usr/bin/env python3
"""Check the local S&P 500 universe against Wikipedia's constituent list.

Read-only data hygiene: this replaces the manual 503-ticker diff against
Wikipedia's "List of S&P 500 companies" that used to be done by hand.
It verifies both the constituent membership and the Materials-sector flag
(`materials` list in universe.json). Never touches trading logic.

Exit codes:
    0 - local universe matches Wikipedia (membership + Materials flag)
    1 - drift found (report on stdout, or JSON with --json)
    2 - the page could not be fetched or parsed; never reports "clean" then
"""
import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
USER_AGENT = (
    "robinhood-agent-universe-check/1.0 "
    "(https://github.com/Bobbywasabi31/robinhood-agent)"
)
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_UNIVERSE = REPO_ROOT / "universe.json"
# Wikipedia's constituent table is the full index; anything far below this
# means the page format changed and we must not report a bogus diff.
MIN_EXPECTED_ROWS = 100


class FetchError(Exception):
    """The Wikipedia page could not be downloaded."""


class ParseError(Exception):
    """The Wikipedia page did not contain a recognizable constituent table."""


def normalize_symbol(raw):
    """Map a ticker to the local convention: upper-case, dots as dashes.

    Wikipedia writes BRK.B / BF.B; universe.json stores BRK-B / BF-B.
    Footnote refs (e.g. "TGT[3]") are stripped.
    """
    cleaned = re.sub(r"\[\w+\]$", "", raw).strip().upper()
    return cleaned.replace(".", "-")


def fetch_wiki_html(url=WIKI_URL, timeout=30):
    """Download the Wikipedia page. Raises FetchError on any transport issue."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise FetchError("could not fetch %s: %s" % (url, exc))


class _WikiTableParser(HTMLParser):
    """Collect every top-level table as (header_cells, data_rows)."""

    def __init__(self):
        super().__init__()
        self.tables = []
        self._depth = 0
        self._in_row = False
        self._in_cell = False
        self._cell_text = []
        self._current_cells = []
        self._header = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._header = None
                self._rows = []
        elif self._depth == 1:
            if tag == "tr":
                self._in_row = True
                self._current_cells = []
            elif tag in ("td", "th") and self._in_row:
                self._in_cell = True
                self._cell_text = []

    def handle_endtag(self, tag):
        if tag == "table":
            if self._depth == 1:
                self.tables.append((self._header, self._rows))
            self._depth = max(0, self._depth - 1)
        elif self._depth == 1:
            if tag in ("td", "th") and self._in_cell:
                self._in_cell = False
                text = html.unescape("".join(self._cell_text)).strip()
                self._current_cells.append(text)
            elif tag == "tr" and self._in_row:
                self._in_row = False
                if self._header is None:
                    self._header = self._current_cells
                elif self._current_cells:
                    self._rows.append(self._current_cells)

    def handle_data(self, data):
        if self._in_cell and self._depth == 1:
            self._cell_text.append(data)


def parse_wiki_constituents(page_html, min_rows=MIN_EXPECTED_ROWS):
    """Extract [(symbol, gics_sector)] from Wikipedia's constituent table.

    Selects the first table whose header mentions both "Symbol" and
    "GICS Sector" (this skips the page's "changes" table, which has
    neither). Raises ParseError when the table or enough rows are missing.

    min_rows is a sanity floor against page-format changes: with the real
    page the table always holds ~500 rows, so a tiny parse means the layout
    moved and we must not report a bogus diff. Tests may lower it.
    """
    parser = _WikiTableParser()
    parser.feed(page_html)
    for header, rows in parser.tables:
        if header is None:
            continue
        lowered = [h.lower() for h in header]
        if "symbol" not in lowered or "gics sector" not in lowered:
            continue
        sym_idx = lowered.index("symbol")
        sec_idx = lowered.index("gics sector")
        constituents = []
        for row in rows:
            if len(row) <= max(sym_idx, sec_idx):
                continue
            symbol = normalize_symbol(row[sym_idx])
            sector = row[sec_idx].strip()
            if symbol:
                constituents.append((symbol, sector))
        if len(constituents) < min_rows:
            raise ParseError(
                "constituent table parsed to %d rows (expected >= %d); "
                "the page format probably changed" % (len(constituents),
                                                      min_rows)
            )
        return constituents
    raise ParseError("no constituent table (header with Symbol + GICS Sector) "
                     "found on the Wikipedia page")


def compare_universe(universe, wiki_constituents):
    """Diff the local universe against Wikipedia's constituent list."""
    wiki = {}
    for symbol, sector in wiki_constituents:
        wiki[normalize_symbol(symbol)] = sector
    local_symbols = {normalize_symbol(s) for s in universe["sp500"]}
    wiki_symbols = set(wiki)

    wiki_materials = sorted(s for s, sec in wiki.items() if sec == "Materials")
    local_materials = sorted(normalize_symbol(s)
                             for s in universe.get("materials", []))

    missing = sorted(wiki_symbols - local_symbols)
    extra = sorted(local_symbols - wiki_symbols)
    materials_missing = sorted(set(wiki_materials) - set(local_materials))
    materials_extra = sorted(set(local_materials) - set(wiki_materials))
    drift = bool(missing or extra or materials_missing or materials_extra)

    return {
        "wiki_count": len(wiki_symbols),
        "local_count": len(local_symbols),
        "missing": missing,
        "extra": extra,
        "materials_missing": materials_missing,
        "materials_extra": materials_extra,
        "drift": drift,
    }


def format_report(report):
    """Human-readable drift report."""
    lines = [
        "S&P 500 universe drift check",
        "  Wikipedia constituents: %d | local: %d"
        % (report["wiki_count"], report["local_count"]),
    ]
    if report["missing"]:
        lines.append("  In index but missing locally (%d): %s"
                     % (len(report["missing"]), ", ".join(report["missing"])))
    if report["extra"]:
        lines.append("  Local but not in index (%d): %s"
                     % (len(report["extra"]), ", ".join(report["extra"])))
    if report["materials_missing"]:
        lines.append("  Materials in index, flag missing locally (%d): %s"
                     % (len(report["materials_missing"]),
                        ", ".join(report["materials_missing"])))
    if report["materials_extra"]:
        lines.append("  Materials flagged locally but not in index (%d): %s"
                     % (len(report["materials_extra"]),
                        ", ".join(report["materials_extra"])))
    if not report["drift"]:
        lines.append("  Membership: clean | Materials flag: clean")
    return "\n".join(lines)


def run(universe, page_html, min_rows=MIN_EXPECTED_ROWS):
    """Pure pipeline used by main() and the tests: parse, diff, report dict."""
    constituents = parse_wiki_constituents(page_html, min_rows=min_rows)
    return compare_universe(universe, constituents)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Diff the local S&P 500 universe against Wikipedia.")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE),
                        help="path to universe.json (default: repo's copy)")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON instead of text")
    args = parser.parse_args(argv)

    try:
        with open(args.universe, encoding="utf-8") as fh:
            universe = json.load(fh)
    except (OSError, ValueError) as exc:
        print("cannot read universe file %s: %s" % (args.universe, exc),
              file=sys.stderr)
        return 2

    try:
        page_html = fetch_wiki_html()
    except FetchError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    try:
        report = run(universe, page_html)
    except ParseError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))
    return 1 if report["drift"] else 0


if __name__ == "__main__":
    sys.exit(main())
