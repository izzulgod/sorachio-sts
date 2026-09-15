# 7. Model Setup

### Swapping LLM Models (Drop & Go)

1. **Download** a GGUF model from [Hugging Face](https://huggingface.co/models?library=gguf)
2. **Drop** into `models/llm1/` or `models/llm2/`
3. **Restart** — auto-detected!

```bash
cp ~/Downloads/Qwen3.5-2B-Q8_0.gguf models/llm2/
cp ~/Downloads/mmproj-Qwen3.5-2B-BF16.gguf models/llm2/  # optional vision
python main.py run
```

### Auto-Detection Logic

| Feature | How it works |
|---------|-------------|
| LLM model file | Largest `.gguf` in the directory (excluding mmproj) |
| Vision projector | Any `mmproj*.gguf` in the same directory |
| Context size | Read from GGUF metadata (`--ctx-size 0`) |
| Chat template | Read from GGUF metadata (`--jinja`) |

### STT Model Options

| Model | Size | Accuracy |
|-------|------|----------|
| tiny | ~75 MB | Low |
| base | ~148 MB | Medium |
| **small** | **~484 MB** | **High (default)** |
| medium | ~1.5 GB | Highest |

Change in `config/sorachio.yaml` → `stt.model_size`.
