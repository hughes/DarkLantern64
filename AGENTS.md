# Agent guidance

- Start substantive development with `python tools/project_status.py`; see [capabilities](docs/project-status.md). Setup problems: [doctor/bootstrap](docs/bootstrap.md).
- Gameplay and streaming behavior: [Native Play/shared coordinator](docs/streaming-coordinator.md). Regions, gates, visibility, lighting or capacity: [editor tools](docs/streaming-editor.md).
- Streaming bugs: [runtime inspector](docs/runtime-inspector.md), using matching ROM/map identities.
- Build with `python tools/build.py`; run Python tests through `python tools/run_tests.py`. Use the [acceptance pipeline](docs/validation-pipeline.md) for full validation and focused checks during iteration.
- Require source-matching evidence; keep exact known failures visible and report unavailable local data. Host preview does not certify ROM timing or hardware.
- Serialize GPU/Ares checks and preserve unrelated sessions.
