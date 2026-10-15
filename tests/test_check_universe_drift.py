"""Tests for scripts/check_universe_drift.py.

The Wikipedia fetch is hermetic here: all tests run against canned HTML
fixtures through the pure run() pipeline. Real-network verification is the
script's own job, not the suite's.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.check_universe_drift import (  # noqa: E402
    FetchError,
    ParseError,
    compare_universe,
    fetch_wiki_html,
    format_report,
    normalize_symbol,
    parse_wiki_constituents,
    run,
)

FIXTURE_PAGE = """<html><body>
<table class="wikitable">
<tr><th>Date</th><th>Added Ticker</th><th>Removed Ticker</th></tr>
<tr><td>Oct 1, 2026</td><td>ACME</td><td>DEAD</td></tr>
</table>
<table class="wikitable sortable">
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>
<tr><td><a href="/wiki/x">AAPL</a></td><td>Apple Inc.</td><td>Information Technology</td></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td></tr>
<tr><td>BF.B</td><td>Brown-Forman</td><td>Consumer Staples</td></tr>
<tr><td>LIN</td><td>Linde plc</td><td>Materials</td></tr>
<tr><td>SHW</td><td>Sherwin-Williams</td><td>Materials</td></tr>
<tr><td>MSFT</td><td>Microsoft</td><td>Information Technology</td></tr>
</table>
</body></html>"""

BIG_FIXTURE_PAGE = "<html><body><table><tr><th>Symbol</th><th>GICS Sector</th>" \
    "</tr>" + "".join(
        "<tr><td>SYM%03d</td><td>Industrials</td></tr>" % i for i in range(150)
    ) + "</table></body></html>"


def make_universe(sp500, materials):
    return {"sp500": sp500, "materials": materials}


def fixture_universe():
    return make_universe(
        ["AAPL", "BRK-B", "BF-B", "LIN", "SHW", "MSFT"],
        ["LIN", "SHW"],
    )


def test_normalize_symbol_dots_to_dashes():
    assert normalize_symbol("BRK.B") == "BRK-B"
    assert normalize_symbol("BF.B") == "BF-B"


def test_normalize_symbol_case_and_footnotes():
    assert normalize_symbol("  aapl ") == "AAPL"
    assert normalize_symbol("TGT[3]") == "TGT"


def test_parse_skips_decoy_table_and_reads_links():
    rows = parse_wiki_constituents(FIXTURE_PAGE, min_rows=0)
    by_sym = dict(rows)
    assert by_sym["AAPL"] == "Information Technology"
    assert by_sym["BRK-B"] == "Financials"
    assert by_sym["BF-B"] == "Consumer Staples"
    assert by_sym["LIN"] == "Materials"
    assert "ACME" not in by_sym  # the changes-table decoy is ignored


def test_parse_raises_without_constituent_table():
    html = "<html><body><table><tr><th>Date</th></tr></table></body></html>"
    try:
        parse_wiki_constituents(html)
    except ParseError:
        return
    raise AssertionError("expected ParseError")


def test_parse_raises_on_truncated_table():
    try:
        parse_wiki_constituents(FIXTURE_PAGE)  # only 6 rows < floor
    except ParseError:
        return
    raise AssertionError("expected ParseError")


def test_run_pipeline_shape():
    report = run(fixture_universe(), BIG_FIXTURE_PAGE)
    assert report["wiki_count"] == 150
    # local fixture is not in the big wiki list, so expect drift; the point
    # here is the pipeline shape, drift detail is covered below.


def _wiki_rows(*symbols_with_sector):
    return [(s, sec) for s, sec in symbols_with_sector]


def test_compare_clean():
    universe = fixture_universe()
    rows = [("AAPL", "Information Technology"), ("BRK.B", "Financials"),
            ("BF.B", "Consumer Staples"), ("LIN", "Materials"),
            ("SHW", "Materials"), ("MSFT", "Information Technology")]
    report = compare_universe(universe, rows)
    assert report["drift"] is False
    assert report["missing"] == [] and report["extra"] == []
    assert report["materials_missing"] == [] and report["materials_extra"] == []


def test_compare_missing_ticker():
    universe = fixture_universe()
    rows = _wiki_rows(("AAPL", "Information Technology"),
                      ("BRK.B", "Financials"),
                      ("BF.B", "Consumer Staples"),
                      ("LIN", "Materials"),
                      ("SHW", "Materials"),
                      ("MSFT", "Information Technology"),
                      ("NVDA", "Information Technology"))
    report = compare_universe(universe, rows)
    assert report["drift"] is True
    assert report["missing"] == ["NVDA"]
    assert report["extra"] == []


def test_compare_extra_ticker():
    universe = make_universe(["AAPL", "ZZZZ"], [])
    rows = _wiki_rows(("AAPL", "Information Technology"))
    report = compare_universe(universe, rows)
    assert report["drift"] is True
    assert report["extra"] == ["ZZZZ"]
    assert report["missing"] == []


def test_compare_materials_flag_drift():
    universe = make_universe(["AAPL", "LIN", "ECL"], ["LIN", "ECL"])
    rows = _wiki_rows(("AAPL", "Information Technology"),
                      ("LIN", "Materials"),
                      ("ECL", "Industrials"),   # flagged locally, not Materials
                      ("APD", "Materials"))     # Materials, flag missing
    report = compare_universe(universe, rows)
    assert report["drift"] is True
    assert report["missing"] == ["APD"]
    assert report["materials_missing"] == ["APD"]
    assert report["materials_extra"] == ["ECL"]


def test_format_report_clean_and_drift():
    clean = compare_universe(fixture_universe(), _wiki_rows(
        ("AAPL", "Information Technology"), ("BRK.B", "Financials"),
        ("BF.B", "Consumer Staples"), ("LIN", "Materials"),
        ("SHW", "Materials"), ("MSFT", "Information Technology")))
    text = format_report(clean)
    assert "clean" in text and "NVDA" not in text

    drifted = compare_universe(fixture_universe(), _wiki_rows(
        ("AAPL", "Information Technology"), ("NVDA", "Information Technology")))
    text = format_report(drifted)
    assert "NVDA" in text and "BRK-B" in text


def test_fetch_wiki_html_raises_fetch_error_on_bad_host():
    try:
        fetch_wiki_html(url="http://127.0.0.1:9/nope", timeout=2)
    except FetchError:
        return
    raise AssertionError("expected FetchError")
