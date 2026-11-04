"""Does the Core Audio session poll leak native memory? (Windows only)

    .venv\\Scripts\\python tools\\com_leak_check.py [N]

Runs N enumerations of the default device's audio sessions (default 20000 -
about 2.8 hours of AutoLoud's polling at 2 per second) twice and prints how
much the process's private bytes grew each time:

  old   - the instance id read through the comtypes wrapper (what AutoLoud
          did up to 0.4.2: the CoTaskMemAlloc'd string is never freed)
  new   - WindowsBackend._list_sessions as it is now

Play something in a couple of apps (a browser tab, a game) first, so there
are several sessions to enumerate. Expect "old" to grow by tens of MB and
"new" to stay within a few MB.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psutil  # noqa: E402

from autoloud.backends.windows import IAudioSessionControl2, WindowsBackend  # noqa: E402


def private_mb() -> float:
    return psutil.Process().memory_info().private / 2**20


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    be = WindowsBackend()
    dev = be.default_output_device()
    if dev is None:
        sys.exit("no output device")
    print(f"device: {dev.name}; sessions: {len(be.list_sessions(dev))}; {n} polls each")

    def old_poll():
        mgr = be._session_manager(dev)
        en = mgr.GetSessionEnumerator()
        for i in range(en.GetCount()):
            ctl = en.GetSession(i)
            if ctl:
                ctl.QueryInterface(IAudioSessionControl2).GetSessionInstanceIdentifier()

    def new_poll():
        be._list_sessions(dev)

    for label, fn in (("old", old_poll), ("new", new_poll)):
        def run():
            for _ in range(n):
                fn()
        before = private_mb()
        t0 = time.perf_counter()
        be._com.call(run, timeout=3600)
        print(f"{label}: private {before:.1f} -> {private_mb():.1f} MB "
              f"({private_mb() - before:+.1f} MB, {time.perf_counter() - t0:.0f} s)")
    be.close()


if __name__ == "__main__":
    main()
