"""Split one section's text into overlapping chunks that never leave the section.

Sizes are in characters; at roughly four characters per token, TARGET_CHARS is about 500
tokens and MAX_CHARS about 800. Invariant: chunk.text == section_text[start_char:end_char].
"""

import re
from dataclasses import dataclass

TARGET_CHARS = 2000
MIN_CHARS = 1600
MAX_CHARS = 3200
OVERLAP_MAX_CHARS = 500
TABLE_CELL_SEP = " | "


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    start_char: int
    end_char: int
    text: str

    @property
    def has_table(self) -> bool:
        return TABLE_CELL_SEP in self.text


def chunk_section(text: str) -> list[Chunk]:
    units = _units(text)
    chunks: list[Chunk] = []
    i = 0
    while i < len(units):
        j = i
        while j + 1 < len(units):
            size = units[j + 1][1] - units[i][0]
            current = units[j][1] - units[i][0]
            if size > MAX_CHARS or (size > TARGET_CHARS and current >= MIN_CHARS):
                break
            j += 1
        start, end = units[i][0], units[j][1]
        chunks.append(Chunk(len(chunks), start, end, text[start:end]))
        if j + 1 >= len(units):
            break
        last_len = units[j][1] - units[j][0]
        overlap_fits = units[j + 1][1] - units[j][0] <= MAX_CHARS
        i = j if j > i and last_len <= OVERLAP_MAX_CHARS and overlap_fits else j + 1
    return chunks


def _units(text: str) -> list[tuple[int, int]]:
    """Line spans with surrounding whitespace trimmed; lines over MAX_CHARS are split."""
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"[^\n]+", text):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.extend(_split_long(text, start, end))
    return spans


def _split_long(text: str, start: int, end: int) -> list[tuple[int, int]]:
    pieces: list[tuple[int, int]] = []
    while end - start > MAX_CHARS:
        window = text[start : start + MAX_CHARS]
        cut = max(window.rfind(". "), window.rfind("; "))
        if cut < MAX_CHARS // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = MAX_CHARS - 1
        pieces.append((start, start + cut + 1))
        start += cut + 1
        while start < end and text[start].isspace():
            start += 1
    if start < end:
        pieces.append((start, end))
    return [(s, e) for s, e in (_trim(text, s, e) for s, e in pieces) if s < e]


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end
