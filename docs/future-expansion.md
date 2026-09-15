# 18. Future Robotics Expansion

Sorachio-STS is architected as the **brain** of a future companion robot.

### Planned Expansion Modules

| Module | Description | Status |
|--------|-------------|--------|
| `sensors/camera.py` | OpenCV face detection, emotion recognition | Planned |
| `sensors/imu.py` | Accelerometer/gyroscope | Planned |
| `actuators/servo.py` | Facial expression servos | Planned |
| `actuators/led.py` | LED ring for emotional state | Planned |
| `memory/vector_store.py` | ChromaDB + sentence-transformers semantic vector memory (offline) | ✅ Live |
| `memory/emotion_tracker.py` | Rolling mood trend detection + LTM emotion summaries | ✅ Live |
| `cognition/vision_gate.py` | Visual cognitive gateway | Planned |
| `core/ros2_bridge.py` | ROS2 topic publisher/subscriber | Planned |
| `agents/task_agent.py` | Goal-oriented sub-agent | Planned |

### Multi-Agent Architecture (Vision)

```
Sorachio Core Brain
+-- Cognitive Gateway (LLM #1) -- fast JSON routing
+-- Personality Core (LLM #2) -- conversation + vision (mmproj ready)
+-- Vision Agent -- camera + face recognition
+-- Task Agent -- goal planning + execution
+-- Memory Agent -- LTM consolidation + reflection
```
