"""Regression: the admin password must never be hardcoded in source.

Bug: the admin portal compared the typed password against a literal in
modules/admin_page.py, and that same literal was also written into README.md.
Both are now gone -- the password comes from ADMIN_PASSWORD in the project-root
.env (gitignored) or the real environment, and admin access is denied when it is
unset rather than falling back to a known value.

The needle below is assembled at runtime so this file does not itself contain
the string it is searching for.

Run directly:  python tests/test_admin_auth.py
Or with pytest: pytest tests/test_admin_auth.py
"""
import io
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

import modules.admin_page as ap  # noqa: E402
from modules.config import PROJECT_ROOT, load_project_env  # noqa: E402

SECRET_SUFFIXES = (".py", ".md", ".txt", ".toml", ".cfg", ".json", ".yaml", ".yml")
LEGACY_SECRET = "papua" + "123"


def _source_files():
    for dirpath, dirnames, files in os.walk(ROOT):
        dirnames[:] = [
            d for d in dirnames if d not in {"__pycache__", ".git", "data", "node_modules"}
        ]
        for f in files:
            if f.endswith(SECRET_SUFFIXES):
                yield os.path.join(dirpath, f)


def test_no_hardcoded_password_in_any_source_file():
    """Catches the legacy literal reappearing anywhere, including documentation
    and this test file itself."""
    offenders = []
    for path in _source_files():
        try:
            body = io.open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        if LEGACY_SECRET in body:
            offenders.append(os.path.relpath(path, ROOT))
    assert not offenders, f"hardcoded admin password still present: {offenders}"


def test_env_file_setup_is_present_and_gitignored():
    assert (PROJECT_ROOT / ".env").is_file(), ".env missing"
    assert (PROJECT_ROOT / ".env.example").is_file(), ".env.example missing"
    ignored = [
        line.strip()
        for line in io.open(PROJECT_ROOT / ".gitignore", encoding="utf-8").read().splitlines()
    ]
    assert ".env" in ignored, ".env must be gitignored"
    assert ".env.example" not in ignored, ".env.example must stay committable"


def test_password_resolves_from_env():
    load_project_env()
    password = ap._admin_password()
    assert password, "ADMIN_PASSWORD should resolve from .env or the environment"
    assert secrets.compare_digest("wrong-password".encode(), password.encode()) is False
    assert secrets.compare_digest("".encode(), password.encode()) is False


def test_fails_closed_when_unconfigured(monkeypatch=None):
    """No .env value and no env var must deny access, not allow it."""
    saved_env = os.environ.pop(ap.ADMIN_PASSWORD_ENV, None)
    saved_loader = ap.load_project_env
    ap.load_project_env = lambda: None
    try:
        assert ap._admin_password() is None
    finally:
        ap.load_project_env = saved_loader
        if saved_env is not None:
            os.environ[ap.ADMIN_PASSWORD_ENV] = saved_env


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("test_admin_auth: all pass")
