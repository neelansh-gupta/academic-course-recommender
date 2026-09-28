"""Degree, dual-degree, and minor names for the profile form.

Names come from the Pilani campus lists printed in the bulletin, not from
truncated catalogue headers.
"""

from __future__ import annotations

from pathlib import Path

from bitsrec.dashboard.programmes import pilani_programmes
from bitsrec.paths import raw_dir


def profile_options(db_path: str | Path | None = None) -> dict:
    del db_path
    parsed = pilani_programmes(raw_dir() / "bulletin.pdf")
    return {
        "degrees": list(parsed["degrees"]),
        "dual_degrees": list(parsed["dual_degrees"]),
        "minors": list(parsed["minors"]),
    }
