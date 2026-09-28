"""Used by install_prerequisites.bat to test a Python candidate.

    <python> scripts\\probe_python.py <out-file>

Writes the interpreter's full path to <out-file> ONLY if it really runs, is
version 3.10 - 3.12 and can create virtual environments. The launcher reads
the file instead of trusting exit codes, because the new Windows "Python
install manager" (py.exe) can print "No runtime installed that matches 3.12"
and still exit with code 0.
"""
import sys

ok = (3, 10) <= sys.version_info[:2] <= (3, 12)
if ok:
    try:
        import ensurepip  # noqa: F401
        import venv  # noqa: F401
    except ImportError:
        ok = False
# explicit CRLF: cmd's "set /p VAR=<file" then reads exactly one clean line
with open(sys.argv[1], "w", encoding="utf-8", newline="") as f:
    f.write((sys.executable + "\r\n") if ok and sys.executable else "")
