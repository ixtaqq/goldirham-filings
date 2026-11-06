from hypothesis import given
from hypothesis import strategies as st

from goldirham_filings.parse import (
    UNKNOWN_SECTION,
    decode_html,
    html_to_text,
    missing_required,
    section_for_form,
    split_sections,
)

TEN_K = """
<html><body>
<div style="display:none"><ix:header>hidden xbrl facts</ix:header></div>
<div>Table of Contents</div>
<table>
<tr><td>Item 1.</td><td>Business</td><td>4</td></tr>
<tr><td>Item 1A.</td><td>Risk Factors</td><td>12</td></tr>
<tr><td>Item 6.</td><td>[Reserved]</td><td>30</td></tr>
<tr><td>Item 7.</td><td>Management's Discussion and Analysis</td><td>31</td></tr>
</table>
<div><span>Item 1. Business</span></div>
<p>We design accelerated computing platforms.</p>
<div>Table of Contents</div>
<p>12</p>
<div>Item 1A. Risk Factors</div>
<p>Export controls could&#160;harm our business.</p>
<p>{filler}</p>
<ul><li>&#8226; Competition is intense.</li></ul>
<div>Item 6. [Reserved]</div>
<div>Item 7. Management&#8217;s Discussion and Analysis</div>
<table><tr><td>Revenue</td><td>$</td><td>130,497</td></tr></table>
<p>Revenue grew.</p>
<p>{filler}</p>
</body></html>
""".replace("{filler}", "Filler sentence. " * 70)
FILLER = ("Filler sentence. " * 70).strip()

TEN_Q = """
<html><body>
<p>PART I. FINANCIAL INFORMATION</p>
<p>Item 1. Financial Statements</p><p>Balance sheet.</p>
<p>Item 2. Management's Discussion and Analysis</p><p>Quarterly results improved.</p>
<p>{filler}</p>
<p>PART II. OTHER INFORMATION</p>
<p>Item 1. Legal Proceedings</p><p>None.</p>
<p>Item 1A. Risk Factors</p><p>No material changes.</p>
</body></html>
""".replace("{filler}", "Filler sentence. " * 70)


def test_html_to_text_keeps_blocks_and_table_cells_and_drops_hidden_xbrl() -> None:
    text = html_to_text(TEN_K)
    assert "hidden xbrl facts" not in text
    assert "Revenue | $ | 130,497" in text
    assert "Export controls could harm our business." in text
    assert "Item 7. Management’s Discussion and Analysis" in text.split("\n")


def test_ten_k_sections_skip_table_of_contents() -> None:
    sections = split_sections(html_to_text(TEN_K), "10-K")
    assert [(s.item, s.text.split("\n")[1:]) for s in sections] == [
        ("1", ["We design accelerated computing platforms.", "Table of Contents", ""]),
        (
            "1A",
            ["Export controls could harm our business.", FILLER, "• Competition is intense.", ""],
        ),
        ("6", [""]),
        ("7", ["Revenue | $ | 130,497", "Revenue grew.", FILLER]),
    ]
    assert missing_required(sections, "10-K") == []


def test_ten_q_items_are_keyed_by_part() -> None:
    sections = split_sections(html_to_text(TEN_Q), "10-Q")
    assert [s.item for s in sections] == ["I-1", "I-2", "II-1", "II-1A"]
    assert sections[1].text.split("\n") == [
        "Item 2. Management's Discussion and Analysis",
        "Quarterly results improved.",
        FILLER,
        "PART II. OTHER INFORMATION",
        "",
    ]


def test_no_headings_falls_back_to_one_unknown_section() -> None:
    sections = split_sections("Just prose.\nMore prose.", "10-K")
    assert [(s.item, s.text) for s in sections] == [(UNKNOWN_SECTION, "Just prose.\nMore prose.")]
    assert missing_required(sections, "10-K") == ["1A", "7"]


def test_stub_required_section_falls_back_to_whole_document() -> None:
    text = (
        "Item 1. Business\nWe make chips.\nItem 1A. Risk Factors\nPages 31-46\nItem 7. MD&A\nSee 50"
    )
    sections = split_sections(text, "10-K")
    assert [(s.item, s.text) for s in sections] == [(UNKNOWN_SECTION, text)]


def test_decode_html_falls_back_to_cp1252() -> None:
    assert decode_html("café ’".encode("cp1252")) == "café ’"
    assert decode_html(b"plain &#8226;") == "plain &#8226;"


PROSE = "Demand for accelerated computing continued to grow across our markets. " * 20


@given(
    st.lists(
        st.sampled_from(
            [
                "Item 1. Business",
                "Item 1A. Risk Factors",
                "Item 7. MD&A",
                PROSE,
                "more | table | row",
                "PART II",
            ]  # fmt: skip
        ),
        max_size=40,
    )
)
def test_sections_are_contiguous_slices_of_the_text(lines: list[str]) -> None:
    body = ["Item 1. Business", PROSE, "Item 1A. Risk Factors", PROSE, "Item 7. MD&A", PROSE]
    text = "\n".join(body + lines)
    sections = split_sections(text, "10-K")
    joined = "".join(s.text for s in sections)
    assert sections and text.endswith(joined)
    assert {"1A", "7"} <= {s.item for s in sections}
    assert all(s.text.startswith("Item ") for s in sections)


def test_sections_map_across_forms() -> None:
    assert section_for_form("1A", "10-Q") == "II-1A"
    assert section_for_form("ii-1a", "10-K") == "1A"
    assert section_for_form("7", "10-Q") == "I-2"
    assert section_for_form("I-2", "10-K") == "7"
    assert section_for_form("7", "10-K") == "7"
    assert section_for_form("1C", "10-Q") is None
    assert section_for_form("II-6", "10-K") is None
