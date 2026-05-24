"""Standalone debug CLI for the Windows OTP backends.

Usage examples (PowerShell):

    # default (prompt)
    py -3 read_otp.py

    # watch a file for 90s
    $env:CIBUS_OTP_SOURCE='file'; py -3 read_otp.py --timeout 90

    # probe Phone Link DBs
    $env:CIBUS_OTP_SOURCE='phone_link'; py -3 read_otp.py --timeout 30
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
HERE = Path(__file__).resolve().parent
PATCH_DIR = HERE.parent / "patches"
if PATCH_DIR.exists():
    sys.path.insert(0, str(PATCH_DIR))

from windows_otp import read_otp  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--source", help="prompt | file | phone_link")
    p.add_argument("--timeout", type=int, default=int(os.environ.get("CIBUS_OTP_TIMEOUT", "60")))
    args = p.parse_args()
    if args.source:
        os.environ["CIBUS_OTP_SOURCE"] = args.source
    print(read_otp(timeout=args.timeout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
