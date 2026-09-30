"""Kavita uploader functionality."""

from __future__ import annotations

import os
import re
from pathlib import Path

import requests


class KavitaUploader:
    """Encapsulates all logic for the Kavita uploader."""

    def __init__(self) -> None:
        self.libraries = self._load_libraries()
        self.allowed_extensions = self._load_allowed_extensions()
        self.kavita_url = os.getenv("KAVITA_URL", "").rstrip("/")
        self.kavita_api_key = os.getenv("KAVITA_API_KEY", "")

    # -----------------------------------------------------------------
    # Library and extension loading
    # -----------------------------------------------------------------
    @staticmethod
    def _load_libraries() -> dict[str, Path]:
        """Read LIBRARY_<NAME>=<path> entries from the environment.

        Example: LIBRARY_MANGA=/data/manga -> {"Manga": Path("/data/manga")}
        """
        libraries = {}
        for key, value in os.environ.items():
            if key.startswith("LIBRARY_") and value.strip():
                name = key[len("LIBRARY_") :].replace("_", " ").strip().title()
                libraries[name] = Path(value.strip()).expanduser()
        return libraries

    @staticmethod
    def _load_allowed_extensions() -> set[str]:
        """Parse ALLOWED_EXTENSIONS environment variable into a set."""
        exts = os.getenv(
            "ALLOWED_EXTENSIONS", "cbz,cbr,cb7,cbt,zip,rar,7z,tar.gz,pdf,epub"
        )
        return {
            ext.strip().lower().lstrip(".")
            for ext in exts.split(",")
            if ext.strip()
        }

    # -----------------------------------------------------------------
    # Filename helpers
    # -----------------------------------------------------------------
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """Remove path separators and other unsafe characters."""
        # Replace path separators and null byte with underscore.
        for sep in ("/", "\\", "\x00"):
            filename = filename.replace(sep, "_")
        # Remove other problematic characters: keep alphanumeric, space, ., -, _
        # but we'll be conservative: allow alphanumeric, space, ., -, _
        # and any Unicode letter/number? We'll keep the original implementation.
        # The original just removed path separators; we'll keep that.
        return filename

    @staticmethod
    def sanitize_series_name(name: str) -> str:
        """Remove path separators from a series name."""
        for sep in ("/", "\\"):
            name = name.replace(sep, "")
        return name

    @staticmethod
    def pad_number(num: str, width: int) -> str:
        """Zero-pad the integer part of a number string ('7' -> '007', '12.5' -> '012.5')."""
        whole, _, frac = num.partition(".")
        padded = whole.zfill(width)
        return f"{padded}.{frac}" if frac else padded

    def existing_series(self, library_root: Path) -> list[str]:
        """Return list of existing series names in a library root."""
        if not library_root.is_dir():
            return []
        return sorted(
            (
                p.name
                for p in library_root.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            ),
            key=str.casefold,
        )

    def allowed_file(self, filename: str) -> bool:
        """Check if a filename has an allowed extension."""
        lower = filename.lower()
        return any(lower.endswith("." + ext) for ext in self.allowed_extensions)

    def kavita_filename(
        self, series: str, original: str, year: str | None, chapter: str | None
    ) -> tuple[str, bool]:
        """Build a filename as "series #chapter.ext" if chapter is provided, else "series.ext".

        Returns (new_filename, numbered) — numbered is True when chapter is provided.
        """
        original = self.sanitize_filename(original)
        _, _, ext = original.rpartition(".")
        if not ext:
            # No extension, keep original
            return original, False

        series = self.sanitize_series_name(series)
        if chapter and chapter.strip():
            # Use series and chapter
            chapter = chapter.strip()
            new_filename = f"{series} #{chapter}.{ext}"
            return new_filename, True
        else:
            # No chapter, just series
            new_filename = f"{series}.{ext}"
            return new_filename, False

    # -----------------------------------------------------------------
    # Regex constants (compiled once per instance)
    # -----------------------------------------------------------------
    _NUMBER = r"(\d+(?:\.\d+)?)"
    _START = r"(?<![A-Za-z0-9])"
    _GAP = r"[\s_\-]*"

    VOLUME_RE = re.compile(
        rf"{_START}(?:v|vol|volume|tomo)\.?{_GAP}{_NUMBER}", re.IGNORECASE
    )
    CHAPTER_RE = re.compile(
        rf"(?:{_START}(?:c|ch|chap|chapter)\.?{_GAP}|#){_NUMBER}", re.IGNORECASE
    )
    TRAILING_NUMBER_RE = re.compile(rf"(?:^|[\s_\-.]){_NUMBER}\s*$")

    # -----------------------------------------------------------------
    # Derived data
    # -----------------------------------------------------------------
    def get_series_by_library(self) -> dict[str, list[str]]:
        """Map library name to list of existing series."""
        return {
            name: self.existing_series(root) for name, root in self.libraries.items()
        }

    @property
    def kavita_scan_enabled(self) -> bool:
        """Whether Kavita URL and API key are both set."""
        return bool(self.kavita_url and self.kavita_api_key)

    def trigger_kavita_scan(self) -> str | None:
        """Trigger a Kavita library scan if configured.

        Returns an error message string on failure, or None on success/no-op.
        """
        if not (self.kavita_url and self.kavita_api_key):
            return None
        # Build the rescan URL. Remove any trailing slash from the base URL
        # to avoid double slashes, and then append the endpoint. Kavita's
        # API expects the path ``/api/libraries/rescan``.
        url = f"{self.kavita_url.rstrip('/')}/api/libraries/rescan"
        try:
            response = requests.post(
                url, headers={"X-Api-Key": self.kavita_api_key}, timeout=30
            )
            if response.status_code == 200:
                return None
            return f"Kavita scan failed: {response.status_code} {response.reason}"
        except requests.RequestException as exc:
            return f"Kavita scan request failed: {exc}"

