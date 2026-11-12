"""Properties of passage chunking for the fake embedder."""

import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from msj.config import EmbeddingSettings, Settings
from msj.embedding import EmbeddingService
from msj.storage import Store


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    directory = tmp_path_factory.mktemp("chunks")
    return Store(Settings(data_dir=directory, embedding=EmbeddingSettings(provider="fake")))


@st.composite
def _chunk_case(draw: st.DrawFn) -> tuple[str, list[str], int, int]:
    size = draw(st.integers(1, 12))
    overlap = draw(st.integers(0, size - 1))
    tokens = draw(
        st.lists(
            st.text(st.characters(whitelist_categories=("Ll", "Nd")), min_size=1, max_size=5),
            max_size=60,
        )
    )
    parts = [tokens[0]] if tokens else []
    for token in tokens[1:]:
        parts.append(draw(st.sampled_from([" ", "  ", "\n", "\t"])) + token)
    return "".join(parts), tokens, size, overlap


def _chunks(store: Store, text: str, size: int, overlap: int) -> list[str]:
    settings = Settings(
        data_dir=store.settings.data_dir,
        embedding=EmbeddingSettings(provider="fake", chunk_tokens=size, chunk_overlap=overlap),
    )
    return EmbeddingService(store, settings)._chunks(text)


@given(_chunk_case())
def test_chunks_cover_every_token_within_size_and_overlap(
    store: Store, case: tuple[str, list[str], int, int]
) -> None:
    text, tokens, size, overlap = case
    chunks = [chunk.split() for chunk in _chunks(store, text, size, overlap)]
    assert bool(chunks) == bool(tokens)
    assert all(0 < len(chunk) <= size for chunk in chunks)
    for previous, current in itertools.pairwise(chunks):
        if len(current) <= overlap:
            continue  # redundant trailing chunk, covered by the containment property
        assert len(previous) == size
        tail = previous[size - overlap :]
        assert current[: len(tail)] == tail[: len(current)]
    stitched = chunks[0] if chunks else []
    for chunk in chunks[1:]:
        stitched = stitched + chunk[overlap:]
    assert stitched == tokens


@given(_chunk_case())
def test_no_chunk_is_contained_in_its_predecessor(
    store: Store, case: tuple[str, list[str], int, int]
) -> None:
    # Regression: a trailing window that started within the previous window's overlap
    # repeated only already-covered tokens (e.g. "0 0" with size 3, overlap 2).
    text, _, size, overlap = case
    chunks = [chunk.split() for chunk in _chunks(store, text, size, overlap)]
    for current in chunks[1:]:
        assert len(current) > overlap
