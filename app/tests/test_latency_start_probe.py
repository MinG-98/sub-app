"""start_probe must launch the probe script with an interpreter that
actually has the app's dependencies installed.

Found while auditing #6/#3: the fallback (when SUB_APP_PYTHON isn't set)
was a guessed path, ROOT/"venv/bin/python", which silently doesn't exist
if the documented setup was followed — README.md's local-run instructions
and the systemd example both use ".venv", not "venv". Every latency probe
triggered from the dashboard on such a deploy failed to even start, with
no error surfaced beyond the probe staying at "never_run".

The interpreter currently running this FastAPI process is guaranteed to
have every dependency installed, regardless of what the venv is named, so
that's the default now instead of a guess.
"""

import importlib
import sys


def test_start_probe_defaults_to_the_running_interpreter(tmp_path, monkeypatch):
    monkeypatch.setenv("SUB_APP_LATENCY_LOCK", str(tmp_path / "lock"))
    monkeypatch.delenv("SUB_APP_PYTHON", raising=False)
    import app.latency as latency

    importlib.reload(latency)

    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv

    monkeypatch.setattr(latency.subprocess, "Popen", FakePopen)

    result = latency.start_probe()

    assert result == {"ok": True, "started": True, "status": "running"}
    assert captured["argv"][0] == sys.executable


def test_start_probe_honors_explicit_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SUB_APP_LATENCY_LOCK", str(tmp_path / "lock"))
    monkeypatch.setenv("SUB_APP_PYTHON", "/opt/sub-app/.venv/bin/python")
    import app.latency as latency

    importlib.reload(latency)

    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv

    monkeypatch.setattr(latency.subprocess, "Popen", FakePopen)

    latency.start_probe()

    assert captured["argv"][0] == "/opt/sub-app/.venv/bin/python"
