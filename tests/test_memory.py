"""
Tests for Memory System (STM + LTM).
"""
import tempfile

import pytest

from memory.long_term import LongTermMemory
from memory.short_term import ShortTermMemory


@pytest.mark.asyncio
async def test_stm_add_and_retrieve():
    stm = ShortTermMemory(max_messages=10)
    await stm.add("user", "Hello there", emotion="happy")
    await stm.add("assistant", "Hi! How are you?", emotion="neutral")

    entries = await stm.get_recent()
    assert len(entries) == 2
    assert entries[0].role == "user"
    assert entries[1].role == "assistant"


@pytest.mark.asyncio
async def test_stm_rolling_window():
    stm = ShortTermMemory(max_messages=3)
    for i in range(5):
        await stm.add("user", f"Message {i}")

    entries = await stm.get_recent()
    assert len(entries) == 3
    assert entries[-1].content == "Message 4"


@pytest.mark.asyncio
async def test_stm_emotion_context():
    stm = ShortTermMemory()
    await stm.add("user", "I am stressed", emotion="anxious")
    emotion = await stm.get_emotion_context()
    assert emotion == "anxious"


@pytest.mark.asyncio
async def test_ltm_store_and_retrieve():
    with tempfile.TemporaryDirectory() as tmpdir:
        ltm = LongTermMemory(
            storage_path=f"{tmpdir}/ltm.json",
            importance_threshold=0.5,
        )
        await ltm.initialize()

        entry = await ltm.store(
            content="User loves hiking in the mountains",
            topic="hobbies",
            importance=0.8,
            keywords=["hiking", "mountains", "outdoors"],
        )
        assert entry is not None
        assert entry.importance == 0.8

        results = await ltm.retrieve(queries=["hiking"])
        assert len(results) >= 1
        assert "hiking" in results[0].content


@pytest.mark.asyncio
async def test_ltm_importance_threshold():
    with tempfile.TemporaryDirectory() as tmpdir:
        ltm = LongTermMemory(
            storage_path=f"{tmpdir}/ltm.json",
            importance_threshold=0.6,
        )
        await ltm.initialize()

        # Low importance — should NOT be stored
        entry = await ltm.store(
            content="User said okay",
            importance=0.3,
        )
        assert entry is None

        # High importance — should be stored
        entry = await ltm.store(
            content="User's name is Alex",
            importance=0.9,
        )
        assert entry is not None


@pytest.mark.asyncio
async def test_ltm_persistence():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = f"{tmpdir}/ltm.json"

        ltm1 = LongTermMemory(storage_path=path, importance_threshold=0.1)
        await ltm1.initialize()
        await ltm1.store("Persistent memory test", importance=0.8)

        # Create new instance, should load saved data
        ltm2 = LongTermMemory(storage_path=path, importance_threshold=0.1)
        await ltm2.initialize()
        assert len(ltm2._entries) == 1
        assert ltm2._entries[0].content == "Persistent memory test"
