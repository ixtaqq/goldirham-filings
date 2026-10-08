from hypothesis import given
from hypothesis import strategies as st

from goldirham_filings.chunk import MAX_CHARS, chunk_section

words = st.text(alphabet="abcdefghij .;|$0123456789", min_size=1, max_size=40)
lines = st.lists(words, min_size=1, max_size=200).map(" ".join)
section_texts = st.lists(lines, min_size=0, max_size=60).map("\n".join)


@given(section_texts)
def test_chunks_round_trip_to_source_offsets(text: str) -> None:
    for chunk in chunk_section(text):
        assert text[chunk.start_char : chunk.end_char] == chunk.text


@given(section_texts)
def test_chunks_respect_max_size_and_order(text: str) -> None:
    chunks = chunk_section(text)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    assert all(a.start_char < b.start_char for a, b in zip(chunks, chunks[1:], strict=False))


@given(section_texts)
def test_every_non_space_character_is_covered(text: str) -> None:
    covered = [False] * len(text)
    for chunk in chunk_section(text):
        for i in range(chunk.start_char, chunk.end_char):
            covered[i] = True
    assert all(covered[i] for i, ch in enumerate(text) if not ch.isspace())


def test_short_section_is_one_chunk() -> None:
    text = "Item 6. [Reserved]\n"
    chunks = chunk_section(text)
    assert [(c.start_char, c.end_char, c.text) for c in chunks] == [(0, 18, "Item 6. [Reserved]")]


def test_long_section_overlaps_by_one_short_paragraph() -> None:
    paragraphs = [f"Paragraph {i} " + "x" * 290 for i in range(20)]
    text = "\n".join(paragraphs)
    chunks = chunk_section(text)
    assert len(chunks) == 4
    assert chunks[0].text.split("\n")[-1] == chunks[1].text.split("\n")[0] == paragraphs[5]


def test_table_rows_mark_has_table() -> None:
    chunks = chunk_section("Revenue | $ 130,497 | $ 60,922\nNarrative text.")
    assert [c.has_table for c in chunks] == [True]


def test_overlap_never_produces_a_chunk_contained_in_the_previous_one() -> None:
    text = "\n".join(["a" * 1300, "b" * 400, "c" * 3000])
    spans = [(c.start_char, c.end_char) for c in chunk_section(text)]
    assert spans == [(0, 1701), (1702, 4702)]


@given(section_texts)
def test_no_chunk_is_contained_in_its_predecessor(text: str) -> None:
    chunks = chunk_section(text)
    assert all(b.end_char > a.end_char for a, b in zip(chunks, chunks[1:], strict=False))
    assert (chunks == []) == (text.strip() == "")
