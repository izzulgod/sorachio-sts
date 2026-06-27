"""
Tests for Chunk Assembler.
"""
import asyncio

import pytest

from utils.chunk_assembler import ChunkAssembler, split_into_chunks


def test_split_simple():
    chunks = split_into_chunks("Hello there. How are you? I am fine.")
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c.split()) >= 1


def test_split_preserves_content():
    text = "This is a test sentence. And another one here."
    chunks = split_into_chunks(text)
    rejoined = " ".join(chunks)
    # All words should be preserved
    for word in text.replace(".", "").replace("?", "").split():
        assert word in rejoined


def test_min_words_respected():
    # Short fragments should be merged with next chunk
    text = "Hi. How are you doing today?"
    chunks = split_into_chunks(text, min_words=3)
    for c in chunks:
        assert len(c.split()) >= 2  # Allow slight under-count at end


@pytest.mark.asyncio
async def test_async_chunker():
    assembler = ChunkAssembler(min_words=3, max_words=20)

    async def token_gen():
        tokens = ["Hello ", "there. ", "How ", "are ", "you ", "doing ", "today? ", "Great!"]
        for t in tokens:
            yield t
            await asyncio.sleep(0.001)

    chunks = []
    async for chunk in assembler.process(token_gen()):
        chunks.append(chunk)

    assert len(chunks) >= 1
    full_text = " ".join(chunks)
    assert "Hello" in full_text
    assert "Great" in full_text


@pytest.mark.asyncio
async def test_chunker_reset():
    assembler = ChunkAssembler()
    assembler._buffer = "leftover"
    assembler.reset()
    assert assembler._buffer == ""
