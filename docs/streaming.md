# 13. Streaming Pipeline Explained

Sorachio begins **speaking before it finishes thinking**:

```
LLM #2: "Hello " -> "there! " -> "I " -> "can " -> "hear " -> "you." -> ...
                                                                |
Chunk Assembler:    ["Hello there!"]         ["I can hear you."]
                          |                          |
TTS Synthesis:      audio1 ready         audio2 synthesizing...
                          |
Audio Queue:        [audio1] -> speaker
                               (while playing) [audio2] -> queued -> next
```

**First audio** is typically heard within **0.5–1.5 seconds** of LLM starting.
