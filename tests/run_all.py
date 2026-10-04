"""Runs every test in this folder, each in its own process.

    .venv312\\Scripts\\python.exe tests\\run_all.py            # everything offline
    .venv312\\Scripts\\python.exe tests\\run_all.py --network  # plus live-site checks
    .venv312\\Scripts\\python.exe tests\\run_all.py queue      # names containing "queue"

One process per file, not one shared interpreter, for two reasons. Several of
these build a whole QApplication and main window, and a second window in the
same process inherits whatever the first left behind. And some failures only
show up as the process's exit code -- Qt aborting on a thread destroyed while
running never reaches Python, so no in-process assertion can see it.

test_net_*.py files talk to real sites and are skipped unless --network is
given: they are slower, and they fail for reasons that have nothing to do
with this code (the site is down, YouTube changed something).
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT_S = 240


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    network = "--network" in argv
    names = [a for a in argv if not a.startswith("--")]

    tests = sorted(f for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    if not network:
        tests = [t for t in tests if not t.startswith("test_net_")]
    if names:
        tests = [t for t in tests if any(n in t for n in names)]

    failed = []
    started = time.monotonic()
    for name in tests:
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, name)],
                cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=TIMEOUT_S)
            ok = proc.returncode == 0
            output = (proc.stdout or "") + (proc.stderr or "")
            code = proc.returncode
        except subprocess.TimeoutExpired as e:
            ok, code = False, "timeout"
            output = ((e.stdout or "") if isinstance(e.stdout, str) else "") + "\n[timed out]"
        elapsed = time.monotonic() - t0
        print("%s  %-40s %5.1fs" % ("PASS" if ok else "FAIL", name, elapsed), flush=True)
        if not ok:
            failed.append(name)
            tail = "\n".join(output.strip().splitlines()[-15:])
            print("      exit %s\n      " % code + tail.replace("\n", "\n      "), flush=True)

    total = time.monotonic() - started
    print("\n%d passed, %d failed  (%.0fs)" % (len(tests) - len(failed), len(failed), total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
