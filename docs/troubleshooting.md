# 17. Troubleshooting

### "Binary not found" / llama-server missing

On Windows: binary must be `llama-server.exe` in `bin/`. Run `python mbg.py --check` to verify.

### Sorachio interrupts itself during playback

The Playback Gate Shield holds the threshold at `max(-15.0 dBFS, speaker_peak + 7.0 dB)`. If self-interruption still occurs, raise the minimum in `audio/capture.py`:
```python
playback_thresh = max(-13.0, self._speaker_baseline_dbfs + 7.0)
```

### "LLM server not responding"

```bash
python main.py servers status
# Check logs:
cat logs/cognitivegateway_server.log
cat logs/personalitycore_server.log
```

### "Audio device issues"

```yaml
# config/sorachio.yaml
audio:
  capture:
    device_index: 0
  playback:
    device_index: 1
```

List devices:
```bash
python -c "import sounddevice; print(sounddevice.query_devices())"
```

### High latency

1. Enable GPU: `n_gpu_layers: 99` in config
2. Rebuild with Vulkan: `python mbg.py --force --build` (auto-copies `libggml-vulkan.so` to `bin/`)
3. Reduce `max_tokens: 150` in personality_core
4. Increase batch size: `n_batch: 512` in personality_core for faster first-token latency
5. Lock RAM: ensure `cap_ipc_lock` is applied (auto by MBG on Linux)

### LLM response suddenly slow

Likely the Vulkan GPU backend `.so` files are missing from `bin/` (llama-server falls back to CPU). Run:
```bash
python mbg.py --force --build
```
This rebuilds llama-server with Vulkan and copies all backend libraries to `bin/` automatically.
