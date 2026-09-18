# 10. Cognitive Gateway Explained

**LLM #1** is a fast routing and filtering brain — it **never generates conversation**, only makes structured JSON decisions in <500ms.

### Input / Output

```
Input: "Hey Sorachio, I've been really stressed about my exams."

Output JSON:
{
    "respond": true,
    "topic": "exams",
    "emotion": "anxious",
    "store_memory": true,
    "importance": 0.8,
    "memory_queries": ["exams", "stress"]
}
```

The pipeline then injects `detected_language` before passing to Context Manager.

### Status UI

```
  >>> STATUS   ◕ happy      ✓ respond      ⚡ medium       ○ memory       topic: greeting
```
