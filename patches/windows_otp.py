"""Pluggable Cibus OTP reader for Windows.

Replaces the Telegram-based ``ask_telegram()`` helper from upstream
``AdirTuval/cibus-daily-buy`` and the macOS ``read_otp_from_messages``
from ``Acohengadol/cibus-auto-buy``.

Selected via the ``CIBUS_OTP_SOURCE`` environment variable. Backends:

    prompt      (default) Interactive console prompt. Always works, but
                requires a human at the keyboard at OTP time.

    file        Watches a file (default ``%USERPROFILE%\\cibus_otp.txt``)
                for fresh content. Any external automation can drop the
                OTP there — Power Automate Desktop, Microsoft Power
                Automate cloud flow, IFTTT/Tasker on the phone, etc.
                Recommended for fully unattended runs.

    phone_link  Reads from the Microsoft Phone Link app's local SQLite
                store at ``%LOCALAPPDATA%\\Packages\\Microsoft.YourPhone_*
                \\LocalState\\``. Android phones only, fragile across
                Phone Link updates. Best-effort.

Environment variables:

    CIBUS_OTP_SOURCE       prompt | file | phone_link  (default: prompt)
    CIBUS_OTP_FILE         path used by ``file`` backend
                           (default: %USERPROFILE%\\cibus_otp.txt)
    CIBUS_OTP_TIMEOUT      seconds to wait (default: 180)
    CIBUS_OTP_REGEX        optional override regex with a single capture
                           group for the digit string.
"""
from __future__ import annotations

import glob
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

try:  # the upstream package exposes a logger
    from cibus_daily_buy.config import log  # type: ignore
except Exception:  # pragma: no cover - allow standalone CLI use
    import logging

    log = logging.getLogger("windows_otp")
    if not log.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


DEFAULT_PATTERNS = [
    re.compile(r"verification code is:?\s*(\d{4,8})", re.I),
    re.compile(r"cibus[^0-9]{0,40}(\d{4,8})", re.I),
    re.compile(r"pluxee[^0-9]{0,40}(\d{4,8})", re.I),
    re.compile(r"\b(\d{6})\b"),
]


def _patterns() -> list[re.Pattern]:
    custom = os.environ.get("CIBUS_OTP_REGEX")
    if custom:
        return [re.compile(custom, re.I)] + DEFAULT_PATTERNS
    return DEFAULT_PATTERNS


def _extract(text: str) -> Optional[str]:
    for pat in _patterns():
        m = pat.search(text or "")
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Backend: prompt
# ---------------------------------------------------------------------------
def _read_prompt(timeout: int) -> str:
    log.info(f"[otp:prompt] waiting up to {timeout}s for user to paste OTP...")
    if not sys.stdin or not sys.stdin.isatty():
        raise RuntimeError(
            "OTP_SOURCE=prompt requires an interactive terminal. "
            "Use CIBUS_OTP_SOURCE=file for unattended runs."
        )
    # Best-effort timeout: tell the user and block on input().
    print(f"[CIBUS] Enter OTP from SMS (timeout {timeout}s): ", end="", flush=True)
    otp = sys.stdin.readline().strip()
    otp = re.sub(r"\D", "", otp)
    if not otp:
        raise TimeoutError("Empty OTP entered")
    return otp


# ---------------------------------------------------------------------------
# Backend: file
# ---------------------------------------------------------------------------
def _otp_file_path() -> Path:
    raw = os.environ.get("CIBUS_OTP_FILE") or str(
        Path(os.environ.get("USERPROFILE", str(Path.home()))) / "cibus_otp.txt"
    )
    return Path(raw)


def _read_file(timeout: int, poll: float = 2.0) -> str:
    path = _otp_file_path()
    log.info(f"[otp:file] watching {path} (timeout={timeout}s)")
    # Treat any file modified before "now" as stale to avoid replay.
    start = time.time()
    deadline = start + timeout
    initial_mtime = path.stat().st_mtime if path.exists() else 0.0
    while time.time() < deadline:
        if path.exists():
            mtime = path.stat().st_mtime
            if mtime > initial_mtime and mtime >= start - 5:
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except Exception as exc:
                    log.warning(f"[otp:file] cannot read {path}: {exc}")
                    time.sleep(poll)
                    continue
                otp = _extract(text) or re.sub(r"\D", "", text).strip()
                if otp and 4 <= len(otp) <= 8:
                    log.info(f"[otp:file] received: {otp}")
                    try:
                        path.unlink()  # consume so the next run waits again
                    except OSError:
                        path.write_text("", encoding="utf-8")
                    return otp
        time.sleep(poll)
    raise TimeoutError(f"No OTP appeared in {path} within {timeout}s")


# ---------------------------------------------------------------------------
# Backend: phone_link  (Microsoft Phone Link / Your Phone, Android only)
# ---------------------------------------------------------------------------
PHONE_LINK_PKG_GLOB = "Microsoft.YourPhone_*"


def _phone_link_db_candidates() -> Iterable[Path]:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages"
    for pkg in base.glob(PHONE_LINK_PKG_GLOB):
        local_state = pkg / "LocalState"
        if not local_state.exists():
            continue
        for db in local_state.rglob("*.db"):
            yield db
        for db in local_state.rglob("*.sqlite"):
            yield db


def _read_phone_link(timeout: int, since_seconds: int = 120, poll: float = 3.0) -> str:
    log.info(f"[otp:phone_link] scanning Phone Link DBs (timeout={timeout}s)")
    deadline = time.time() + timeout
    cutoff = time.time() - since_seconds
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        for db in _phone_link_db_candidates():
            try:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            except sqlite3.OperationalError as exc:
                last_err = exc
                continue
            try:
                tables = [
                    r[0]
                    for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                ]
                for tbl in tables:
                    if not re.search(r"(message|sms|conv)", tbl, re.I):
                        continue
                    try:
                        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{tbl}")').fetchall()]
                    except sqlite3.OperationalError:
                        continue
                    text_col = next(
                        (c for c in cols if re.search(r"(body|text|content|message)", c, re.I)),
                        None,
                    )
                    if not text_col:
                        continue
                    try:
                        rows = con.execute(
                            f'SELECT "{text_col}" FROM "{tbl}" ORDER BY ROWID DESC LIMIT 20'
                        ).fetchall()
                    except sqlite3.OperationalError:
                        continue
                    for (text,) in rows:
                        otp = _extract(str(text or ""))
                        if otp:
                            log.info(f"[otp:phone_link] received: {otp} (from {db.name}::{tbl})")
                            con.close()
                            return otp
            finally:
                con.close()
        # also accept the cutoff so the log shows we tried recent rows
        _ = cutoff
        time.sleep(poll)
    msg = f"No OTP found via Phone Link within {timeout}s"
    if last_err:
        msg += f" (last db error: {last_err})"
    raise TimeoutError(msg)


# ---------------------------------------------------------------------------
# Public entry point — drop-in for ask_telegram() / read_otp_from_messages()
# ---------------------------------------------------------------------------
def read_otp(timeout: Optional[int] = None) -> str:
    """Block until an OTP is available, then return it as a digit string."""
    src = (os.environ.get("CIBUS_OTP_SOURCE") or "prompt").strip().lower()
    if timeout is None:
        timeout = int(os.environ.get("CIBUS_OTP_TIMEOUT", "180"))
    if src == "prompt":
        return _read_prompt(timeout)
    if src == "file":
        return _read_file(timeout)
    if src == "phone_link":
        return _read_phone_link(timeout)
    raise ValueError(
        f"Unknown CIBUS_OTP_SOURCE={src!r}; expected prompt | file | phone_link"
    )


# Back-compat alias so the upstream login.py patch can call the same name as
# the macOS version did.
read_otp_from_messages = read_otp


if __name__ == "__main__":  # pragma: no cover - manual CLI
    import argparse

    parser = argparse.ArgumentParser(description="Cibus OTP reader (Windows)")
    parser.add_argument("--source", help="Override CIBUS_OTP_SOURCE for this run")
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("CIBUS_OTP_TIMEOUT", "180")))
    args = parser.parse_args()
    if args.source:
        os.environ["CIBUS_OTP_SOURCE"] = args.source
    print(read_otp(timeout=args.timeout))
