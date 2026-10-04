"""§5 Immutable History: fail if committed history text was rewritten.

The doctrine may only grow at its end; a file under docs/proposals/ never
changes or disappears once committed. Compares the working tree to <base-ref>.

    python tools/check_append_only.py <base-ref>
"""

import subprocess
import sys

APPEND_ONLY = ["docs/CYBER_NINJA_v5.2_HYBRID.md"]
IMMUTABLE_DIRS = ["docs/proposals/"]


def git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True)


def at(ref, path):
    r = git("show", f"{ref}:{path}")
    return r.stdout if r.returncode == 0 else None


def read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def violations(base):
    if git("cat-file", "-e", f"{base}^{{commit}}").returncode:
        # A base that cannot be found (e.g. after a force-push) is not evidence of a clean history.
        return [f"base ref {base!r} not found"]
    out = []
    for path in APPEND_ONLY:
        old = at(base, path)
        if old is None:
            continue
        new = read(path)
        if new is None:
            out.append(f"{path}: deleted")
        elif not new.startswith(old):
            out.append(f"{path}: existing text changed (only appending at the end is allowed)")
    listed = git("ls-tree", "-r", "--name-only", base, "--", *IMMUTABLE_DIRS).stdout.decode().split("\n")
    for path in filter(None, listed):
        new = read(path)
        if new is None:
            out.append(f"{path}: deleted")
        elif new != at(base, path):
            out.append(f"{path}: changed after being committed")
    return out


def main(argv):
    if len(argv) != 1:
        print(__doc__)
        return 2
    found = violations(argv[0])
    for v in found:
        print("§5 VIOLATION:", v)
    if not found:
        print(f"append-only history: PASS (base {argv[0]})")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
