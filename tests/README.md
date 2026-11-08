# Automated Tests

This folder contains the project automated test suite and fictional screenplay fixtures.

Run from the repository root (not from inside `tests/`):

**Windows**
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

**macOS / Linux**
```bash
.venv/bin/python -m unittest discover -s tests -q
```

The suite contains 74 test methods across eight test modules. Tests use fictional screenplay fixtures, simulated/fake model clients, and a local test HTTP server. They do not download or run the real Qwen model and do not establish editorial or audience quality.
