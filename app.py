"""Nomi — a small web app to upload comics/manga into a Kavita library.

Files are stored following Kavita's expected layout:

    <Library Root>/<Series Name>/<file.cbz>

Libraries and their root paths are configured in the .env file.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me")
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "1024")) * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ext.strip().lower().lstrip(".")
    for ext in os.getenv("ALLOWED_EXTENSIONS", "cbz,cbr,cb7,cbt,zip,rar,7z,tar.gz,pdf,epub").split(",")
    if ext.strip()
}

# Kavita API (optional): if set, a library scan is triggered after each upload.
KAVITA_URL = os.getenv("KAVITA_URL", "").rstrip("/")
KAVITA_API_KEY = os.getenv("KAVITA_API_KEY", "")


def load_libraries() -> dict[str, Path]:
    """Read LIBRARY_<NAME>=<path> entries from the environment.

    Example: LIBRARY_MANGA=/data/media/manga -> {"Manga": Path("/data/media/manga")}
    """
    libraries = {}
    for key, value in os.environ.items():
        if key.startswith("LIBRARY_") and value.strip():
            name = key[len("LIBRARY_"):].replace("_", " ").strip().title()
            libraries[name] = Path(value.strip()).expanduser()
    return libraries


LIBRARIES = load_libraries()


def sanitize_series_name(name: str) -> str:
    """Make a series name safe to use as a single folder name."""
    name = unicodedata.normalize("NFKC", name).strip()
    # Strip path separators and characters invalid on common filesystems.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name


def sanitize_filename(filename: str) -> str:
    """Sanitize an uploaded filename, keeping spaces and unicode.

    Unlike werkzeug's secure_filename, this preserves characters Kavita's
    parser relies on (spaces, CJK titles) while still stripping any path
    components and filesystem-invalid characters.
    """
    filename = os.path.basename(filename.replace("\\", "/"))
    return sanitize_series_name(filename)


def allowed_file(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith("." + ext) for ext in ALLOWED_EXTENSIONS)


def existing_series(library_root: Path) -> list[str]:
    if not library_root.is_dir():
        return []
    return sorted(
        (p.name for p in library_root.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=str.casefold,
    )


def trigger_kavita_scan() -> str | None:
    """Ask Kavita to scan all libraries. Returns an error message or None."""
    if not (KAVITA_URL and KAVITA_API_KEY):
        return None
    try:
        auth = requests.post(
            f"{KAVITA_URL}/api/Plugin/authenticate",
            params={"apiKey": KAVITA_API_KEY, "pluginName": "Nomi"},
            timeout=10,
        )
        auth.raise_for_status()
        token = auth.json()["token"]
        scan = requests.post(
            f"{KAVITA_URL}/api/Library/scan-all",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        scan.raise_for_status()
        return None
    except requests.RequestException as exc:
        return f"Files saved, but the Kavita scan could not be triggered: {exc}"


@app.route("/", methods=["GET"])
def index():
    series_by_library = {name: existing_series(root) for name, root in LIBRARIES.items()}
    return render_template(
        "index.html",
        libraries=LIBRARIES,
        series_by_library=series_by_library,
        allowed_extensions=sorted(ALLOWED_EXTENSIONS),
        kavita_scan_enabled=bool(KAVITA_URL and KAVITA_API_KEY),
    )


@app.route("/upload", methods=["POST"])
def upload():
    library_name = request.form.get("library", "")
    series_name = sanitize_series_name(request.form.get("series", ""))
    files = [f for f in request.files.getlist("files") if f and f.filename]

    if library_name not in LIBRARIES:
        flash("Please choose a valid library.", "error")
        return redirect(url_for("index"))
    if not series_name:
        flash("Please enter a series name.", "error")
        return redirect(url_for("index"))
    if not files:
        flash("Please choose at least one file to upload.", "error")
        return redirect(url_for("index"))

    library_root = LIBRARIES[library_name]
    if not library_root.is_dir():
        flash(f"Library path does not exist: {library_root}", "error")
        return redirect(url_for("index"))

    series_dir = library_root / series_name
    # Guard against any path traversal that slipped through sanitization.
    if series_dir.resolve().parent != library_root.resolve():
        flash("Invalid series name.", "error")
        return redirect(url_for("index"))

    series_dir.mkdir(parents=True, exist_ok=True)

    saved, skipped = [], []
    for file in files:
        filename = sanitize_filename(file.filename)
        if not filename or not allowed_file(filename):
            skipped.append(f"{file.filename} (file type not allowed)")
            continue
        destination = series_dir / filename
        if destination.resolve().parent != series_dir.resolve():
            skipped.append(f"{file.filename} (invalid filename)")
            continue
        if destination.exists():
            skipped.append(f"{filename} (already exists)")
            continue
        file.save(destination)
        saved.append(filename)

    if saved:
        flash(
            f"Saved {len(saved)} file(s) to {library_name} / {series_name}: {', '.join(saved)}",
            "success",
        )
        scan_error = trigger_kavita_scan()
        if scan_error:
            flash(scan_error, "error")
        elif KAVITA_URL and KAVITA_API_KEY:
            flash("Kavita library scan triggered.", "success")
    for item in skipped:
        flash(f"Skipped {item}", "error")

    return redirect(url_for("index"))


if __name__ == "__main__":
    if not LIBRARIES:
        raise SystemExit(
            "No libraries configured. Add at least one LIBRARY_<NAME>=<path> entry to your .env file."
        )
    app.run(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("DEBUG", "false").lower() == "true",
    )
