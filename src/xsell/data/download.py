"""Download one competition file with the Kaggle API.

Credentials are never read, printed or stored here. We only check that one of
the sources the Kaggle client understands exists, so a missing token gives a
clear message instead of the Kaggle client's interactive help text.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from xsell.errors import DownloadError
from xsell.logging_utils import get_logger

logger = get_logger("xsell.data.download")

RULES_URL = "https://www.kaggle.com/competitions/{competition}/rules"
SETUP_HELP = (
    "Kaggle credentials not found. Create an API token at https://www.kaggle.com/settings "
    "(API section) and configure it as the Kaggle documentation describes, for example "
    "~/.kaggle/kaggle.json, ~/.kaggle/access_token, or the KAGGLE_API_TOKEN / "
    "KAGGLE_USERNAME + KAGGLE_KEY environment variables. Then accept the competition rules at {rules}."
)


def find_kaggle_credentials(env: Mapping[str, str] | None = None, home: Path | None = None) -> str | None:
    """Describe where Kaggle credentials would come from, or None. Never returns the secret."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    if env.get("KAGGLE_API_TOKEN"):
        return "KAGGLE_API_TOKEN environment variable"
    if env.get("KAGGLE_USERNAME") and env.get("KAGGLE_KEY"):
        return "KAGGLE_USERNAME and KAGGLE_KEY environment variables"

    kaggle_dir = home / ".kaggle"
    candidates = [kaggle_dir / name for name in ("access_token", "access_token.txt", "credentials.json")]
    if env.get("KAGGLE_CONFIG_DIR"):
        candidates.append(Path(env["KAGGLE_CONFIG_DIR"]) / "kaggle.json")
    candidates.append(kaggle_dir / "kaggle.json")
    xdg_config = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else home / ".config"
    candidates.append(xdg_config / "kaggle" / "kaggle.json")
    for path in candidates:
        if path.is_file():
            return f"file {path}"
    return None


def _default_api() -> Any:
    # Importing kaggle authenticates immediately, so only import after the credential check.
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    try:
        api.authenticate()
    except SystemExit as error:  # the Kaggle client calls exit(1) when authentication fails
        raise DownloadError("Kaggle authentication failed; check that your API token is valid") from error
    return api


def explain_http_error(status: int | None, competition: str, file_name: str) -> str:
    rules = RULES_URL.format(competition=competition)
    if status == 401:
        return "Kaggle rejected the credentials (HTTP 401). Create a new API token and configure it again."
    if status == 403:
        return (
            f"Kaggle refused the download (HTTP 403). Accept the competition rules at {rules} "
            "while logged in as the account that owns the API token, then retry."
        )
    if status == 404:
        return f"Kaggle could not find {file_name!r} in competition {competition!r} (HTTP 404)."
    return f"Kaggle download failed (HTTP {status})."


def download_competition_file(
    competition: str,
    file_name: str,
    dest_dir: Path,
    api_factory: Callable[[], Any] | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Download one competition file into dest_dir and return its path."""
    source = find_kaggle_credentials(env, home)
    if source is None:
        raise DownloadError(SETUP_HELP.format(rules=RULES_URL.format(competition=competition)))
    logger.info("using Kaggle credentials from %s", source)

    import requests

    dest_dir.mkdir(parents=True, exist_ok=True)
    api = (api_factory or _default_api)()
    logger.info("downloading %s from competition %s into %s", file_name, competition, dest_dir)
    try:
        api.competition_download_file(competition, file_name, path=str(dest_dir), quiet=False)
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else None
        raise DownloadError(explain_http_error(status, competition, file_name)) from error
    except requests.RequestException as error:
        raise DownloadError(f"network error while downloading from Kaggle: {error}") from error

    target = dest_dir / file_name
    if not target.is_file():
        found = sorted(path.name for path in dest_dir.iterdir())
        raise DownloadError(f"download finished but {target} is missing; {dest_dir} contains {found}")
    logger.info("downloaded %s (%.1f MB)", target.name, target.stat().st_size / 1e6)
    return target
