# 14. Memory Architecture

### Short-Term Memory (STM)

- In-memory rolling deque, last 20 messages
- Content: role, content, emotion, topic, importance, timestamp
- Cleared on session end

### Long-Term Memory (LTM) & Vector Store

- **JSON Persistent Store**: (`data/memory/ltm.json`), up to 500 entries. Stores key facts, importance scoring, and recency metadata.
- **ChromaDB Vector Store**: (`data/memory/chroma`). Uses `sentence-transformers` (`all-MiniLM-L6-v2`) for semantic embedding search.
- **Hybrid Retrieval**: Combines keyword matching with semantic vector similarity for high-precision memory recall across sessions.

### Emotion Persistence & Mood Tracking

- `EmotionTracker` records rolling emotional patterns from Cognitive Gateway decisions.
- Tracks mood trends over time and signals personality adaptation to LLM #2.
- Periodically summarizes emotional state and saves summaries into LTM.
