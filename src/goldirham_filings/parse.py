"""Filing HTML to clean text, split into Item sections. Pure functions over strings."""

import re
from collections import Counter
from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser, LexborNode

from goldirham_filings.edgar import Form

PARSER_VERSION = "4"

BLOCK_TAGS = frozenset(
    "address article blockquote body br center dd div dl dt h1 h2 h3 h4 h5 h6 hr li ol p pre "
    "section table tbody thead tfoot tr ul".split()
)
CELL_TAGS = frozenset({"td", "th"})
SKIP_TAGS = frozenset({"script", "style", "head", "title", "ix:header", "noscript"})
CELL_SEP = "\x1f"
HIDDEN_STYLE = re.compile(r"display\s*:\s*none", re.I)

SECTION_TITLES: dict[Form, dict[str, str]] = {
    "10-K": {
        "1": "Business",
        "1A": "Risk Factors",
        "1B": "Unresolved Staff Comments",
        "1C": "Cybersecurity",
        "2": "Properties",
        "3": "Legal Proceedings",
        "4": "Mine Safety Disclosures",
        "5": "Market for Registrant's Common Equity and Related Stockholder Matters",
        "6": "Reserved",
        "7": "Management's Discussion and Analysis",
        "7A": "Quantitative and Qualitative Disclosures About Market Risk",
        "8": "Financial Statements and Supplementary Data",
        "9": "Changes in and Disagreements with Accountants",
        "9A": "Controls and Procedures",
        "9B": "Other Information",
        "9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
        "10": "Directors, Executive Officers and Corporate Governance",
        "11": "Executive Compensation",
        "12": "Security Ownership",
        "13": "Certain Relationships and Related Transactions",
        "14": "Principal Accountant Fees and Services",
        "15": "Exhibits and Financial Statement Schedules",
        "16": "Form 10-K Summary",
    },
    "10-Q": {
        "I-1": "Financial Statements",
        "I-2": "Management's Discussion and Analysis",
        "I-3": "Quantitative and Qualitative Disclosures About Market Risk",
        "I-4": "Controls and Procedures",
        "II-1": "Legal Proceedings",
        "II-1A": "Risk Factors",
        "II-2": "Unregistered Sales of Equity Securities and Use of Proceeds",
        "II-3": "Defaults Upon Senior Securities",
        "II-4": "Mine Safety Disclosures",
        "II-5": "Other Information",
        "II-6": "Exhibits",
    },
}
REQUIRED_SECTIONS: dict[Form, tuple[str, ...]] = {"10-K": ("1A", "7"), "10-Q": ("I-2",)}
# Body heading lines of filers that organize a 10-Q around a cross-reference index (Intel),
# and the Items they stand for. The body shortens some table-of-contents entries (MD&A) and
# opens the financial statements with page banners. Intel's 10-K has no risk-factors heading
# in its text (the index points to page numbers), so 10-Ks keep the whole-document fallback.
OWN_HEADINGS: dict[str, tuple[tuple[str, str], ...]] = {
    "10-Q": (
        (r"Financial Statements \| Consolidated Condensed Statements of .+", "I-1"),
        (r"Management's Discussion and Analysis(?: \(MD&A\))?", "I-2"),
        (r"Quantitative and Qualitative Disclosures About Market Risk", "I-3"),
        (r"Controls and Procedures", "I-4"),
        (r"Risk Factors", "II-1A"),
        (r"Issuer Purchases of Equity Securities", "II-2"),
        (r"Exhibits", "II-6"),
    ),
}
# 10-K items and the 10-Q items that cover the same ground, so a 10-K can be compared with a
# later 10-Q section by section.
EQUIVALENT_SECTIONS = {
    "1A": "II-1A", "3": "II-1", "5": "II-2", "7": "I-2", "7A": "I-3", "8": "I-1", "9A": "I-4",
    "9B": "II-5",
}  # fmt: skip


def section_for_form(section: str, form: Form) -> str | None:
    """The id of the same section in a filing of this form ('1A' in a 10-Q is 'II-1A'), or
    None when that form has no such section."""
    section = section.upper()
    if section in SECTION_TITLES[form]:
        return section
    to_10q = EQUIVALENT_SECTIONS.get(section)
    if form == "10-Q" and to_10q:
        return to_10q
    to_10k = {q: k for k, q in EQUIVALENT_SECTIONS.items()}.get(section)
    return to_10k if form == "10-K" else None


UNKNOWN_SECTION = "unknown"
MIN_REQUIRED_CHARS = 1000

ITEM_LINE = re.compile(r"^item\s*(\d{1,2}[a-c]?)\s*(?:[.:\-–—|]|\s|$)", re.I)
PART_LINE = re.compile(r"^part\s+(i{1,2}|iv|iii)\b", re.I)
PAGE_NUMBER = re.compile(r"^(page\s*)?\d{1,3}$|^[ivx]{1,5}$", re.I)
MAX_HEADING_CHARS = 160


@dataclass(frozen=True)
class Section:
    item: str
    title: str
    text: str


@dataclass(frozen=True)
class FiscalFocus:
    """The filing's own fiscal year and period ('FY', 'Q1'..'Q3') from its cover-page DEI
    tags. Fiscal years differ from calendar years (NVIDIA's fiscal 2027 began in Jan 2026)."""

    year: int | None
    period: str | None


def _dei(html: str, name: str) -> str | None:
    match = re.search(
        rf'name="dei:{name}"[^>]*>(.*?)</ix:nonNumeric>', html, re.IGNORECASE | re.DOTALL
    )
    return re.sub(r"<[^>]+>", "", match.group(1)).strip() if match else None


def fiscal_focus(html: str) -> FiscalFocus:
    year = _dei(html, "DocumentFiscalYearFocus")
    period = _dei(html, "DocumentFiscalPeriodFocus")
    return FiscalFocus(int(year) if year and year.isdigit() else None, period or None)


def decode_html(raw: bytes) -> str:
    """EDGAR documents are mostly ASCII with entities; older ones can be Windows-1252."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def html_to_text(html: str) -> str:
    """Block-aware text: one line per block, table cells joined with ' | '."""
    tree = LexborHTMLParser(html)
    root = tree.body or tree.root
    if root is None:
        return ""
    pieces: list[str] = []
    _walk(root, pieces)
    lines = (_clean_line(line) for line in "".join(pieces).split("\n"))
    return _drop_page_furniture([line for line in lines if line])


def _walk(node: LexborNode, out: list[str]) -> None:
    for child in node.iter(include_text=True):
        tag = child.tag
        if tag == "-text":
            out.append(child.text_content or "")
        elif tag in SKIP_TAGS or HIDDEN_STYLE.search(child.attributes.get("style") or ""):
            continue
        elif tag in CELL_TAGS:
            out.append(CELL_SEP)
            _walk(child, out)
        elif tag in BLOCK_TAGS:
            out.append("\n")
            _walk(child, out)
            out.append("\n")
        else:
            _walk(child, out)


def _clean_line(line: str) -> str:
    cells = (" ".join(cell.split()) for cell in line.split(CELL_SEP))
    return " | ".join(cell for cell in cells if cell)


def _drop_page_furniture(lines: list[str]) -> str:
    """Remove page numbers and short lines repeated on many pages (running headers/footers)."""
    counts = Counter(line for line in lines if len(line) < 100)
    repeated = {line for line, n in counts.items() if n >= 8 and not line.startswith(("$", "("))}
    return "\n".join(line for line in lines if not PAGE_NUMBER.match(line) and line not in repeated)


@dataclass(frozen=True)
class _Heading:
    key: str
    start: int


def split_sections(text: str, form: Form) -> list[Section]:
    """Split clean text into Item sections.

    Every Item heading appears at least twice: in the table of contents and in the body.
    The body starts at the first heading of the first item (Item 1, or Part I Item 1) that
    owns real text; table-of-contents entries own almost none. After that, each item keeps
    the candidate owning the most text. Text before the body is the cover page and table
    of contents and is dropped.

    When a required section is absent or only a stub (some filers, such as Intel, use a
    cross-reference index instead of Item headings), the filer's own headings are tried
    (`split_by_own_headings`); if those also miss a required section, the whole document
    becomes one section with item 'unknown' so no text is lost.
    """
    titles = SECTION_TITLES[form]
    candidates = _heading_candidates(text, form)
    body_start = _body_start(candidates, next(iter(titles)), len(text))
    chosen = _longest_per_item(candidates, titles, len(text), body_start)
    sections = []
    for i, heading in enumerate(chosen):
        end = chosen[i + 1].start if i + 1 < len(chosen) else len(text)
        sections.append(Section(heading.key, titles[heading.key], text[heading.start : end]))
    if missing_required(sections, form):
        own = split_by_own_headings(text, form)
        if own and not missing_required(own, form):
            return own
        return [Section(UNKNOWN_SECTION, "Whole document", text)]
    return sections


def split_by_own_headings(text: str, form: Form) -> list[Section] | None:
    """Sections from a filer's own headings, for documents organized around a
    cross-reference index (Intel). Its table of contents lists the headings and ends with
    the index entry; in the body each heading is a line of its own. Text before the first
    mapped heading (forward-looking statements, availability) is dropped like a cover page.
    None when the form has no heading map or the table of contents is not found."""
    headings = OWN_HEADINGS.get(form)
    toc_end = re.search(rf"^Form {form} Cross-Reference Index$", text, re.M)
    if not headings or toc_end is None:
        return None
    starts: list[tuple[int, str]] = []
    for line, item in headings:
        found = re.search(rf"^{line}$", text[toc_end.end() :], re.M)
        if found:
            starts.append((toc_end.end() + found.start(), item))
    starts.sort()
    titles = SECTION_TITLES[form]
    return [
        Section(item, titles[item], text[start : starts[i + 1][0] if i + 1 < len(starts) else None])
        for i, (start, item) in enumerate(starts)
    ]


def _spans(candidates: list[_Heading], text_len: int) -> list[int]:
    ends = [h.start for h in candidates[1:]] + [text_len] if candidates else []
    return [end - h.start for h, end in zip(candidates, ends, strict=True)]


def _body_start(candidates: list[_Heading], first_item: str, text_len: int) -> int:
    firsts = [
        (h.start, span)
        for h, span in zip(candidates, _spans(candidates, text_len), strict=True)
        if h.key == first_item
    ]
    real = [start for start, span in firsts if span >= MIN_REQUIRED_CHARS]
    if real:
        return real[0]
    return max(firsts, key=lambda f: f[1])[0] if firsts else 0


def _longest_per_item(
    candidates: list[_Heading], titles: dict[str, str], text_len: int, body_start: int
) -> list[_Heading]:
    """Each item's candidate owning the most text, ignoring candidates before the body,
    so short items such as 'Item 6. [Reserved]' do not pick their TOC entry."""
    best: dict[str, tuple[int, _Heading]] = {}
    for heading, span in zip(candidates, _spans(candidates, text_len), strict=True):
        if heading.start < body_start or heading.key not in titles:
            continue
        if span > best.get(heading.key, (-1, heading))[0]:
            best[heading.key] = (span, heading)
    return sorted((h for _, h in best.values()), key=lambda h: h.start)


def _heading_candidates(text: str, form: Form) -> list[_Heading]:
    found: list[_Heading] = []
    part = "I"
    offset = 0
    for line in text.split("\n"):
        if len(line) <= MAX_HEADING_CHARS:
            first_cell = line.split(" | ")[0]
            part_match = PART_LINE.match(first_cell)
            if part_match:
                part = part_match.group(1).upper()
            item_match = ITEM_LINE.match(first_cell) or ITEM_LINE.match(line.replace(" | ", " "))
            if item_match:
                item = item_match.group(1).upper()
                key = f"{part}-{item}" if form == "10-Q" else item
                found.append(_Heading(key, offset))
        offset += len(line) + 1
    return found


def missing_required(sections: list[Section], form: Form) -> list[str]:
    present = {s.item for s in sections if len(s.text) >= MIN_REQUIRED_CHARS}
    return [item for item in REQUIRED_SECTIONS[form] if item not in present]
