"""Use fresh processes: BLAS initialises once, at the first numerical import."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _run(code, threads=None):
    env = os.environ.copy()
    env.pop("OPENBLAS_NUM_THREADS", None)
    if threads is not None:
        env["OPENBLAS_NUM_THREADS"] = threads
    return subprocess.check_output(
        [sys.executable, "-c", code], cwd=ROOT, env=env, text=True, timeout=60,
    ).strip()


def test_runtime_default_precedes_numerical_imports():
    result = json.loads(_run("""
import json, os, sys
import autoloud
print(json.dumps([os.environ['OPENBLAS_NUM_THREADS'],
                  'numpy' in sys.modules, 'scipy' in sys.modules]))
"""))
    assert result == ["1", False, False]


def test_runtime_respects_explicit_thread_count():
    assert _run("import autoloud, os; print(os.environ['OPENBLAS_NUM_THREADS'])",
                threads="2") == "2"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows private commit regression")
def test_app_import_private_commit_is_bounded():
    # Exercise the real entry module, including both NumPy and SciPy. A fresh
    # Windows process used ~1.3 GB here with the default native thread pools.
    private_mb = float(_run("""
import autoloud.ui.app
import psutil
print(psutil.Process().memory_info().private / 2**20)
"""))
    assert private_mb < 512, f"app import committed {private_mb:.1f} MB"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Core Audio regression")
def test_session_poll_private_commit_is_bounded():
    # Read-only integration check; skip Windows runners without an audio device.
    # 10,000 polls retained ~30 MiB with the cached session manager, even with
    # the COM string fix in place. Warm up first to exclude import/cache costs.
    result = json.loads(_run("""
import autoloud, gc, json, logging, psutil
from autoloud.backends.windows import WindowsBackend
b = WindowsBackend()
try:
    device = b.default_output_device()
    if device is None:
        print(json.dumps(None))
    else:
        errors = []
        class CaptureErrors(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.ERROR:
                    errors.append(record.getMessage())
        logging.getLogger('autoloud.backends.windows').addHandler(CaptureErrors())
        def measure():
            for _ in range(100):
                b._list_sessions(device)
            gc.collect()
            p = psutil.Process()
            before = p.memory_info().private
            for _ in range(10000):
                b._list_sessions(device)
            gc.collect()
            return (p.memory_info().private - before) / 2**20
        growth = b._com.call(measure, timeout=50)
        print(json.dumps({'growth': growth, 'errors': errors}))
finally:
    b.close()
"""))
    if result is None:
        pytest.skip("no Windows audio output device")
    assert not result['errors'], result['errors'][:3]
    assert result['growth'] < 16, f"10,000 session polls retained {result['growth']:.1f} MiB"
