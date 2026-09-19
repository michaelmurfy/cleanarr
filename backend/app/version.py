from __future__ import annotations

import os

from .config import env_value

__version__ = "1.0.0"


def current_version() -> str:
    """The running version. A build can stamp CLEANARR_VERSION to override it."""
    return (os.environ.get("CLEANARR_VERSION") or env_value("CLEANARR_VERSION") or __version__).strip() or __version__
