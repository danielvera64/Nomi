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
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

import base64
import json
from src.nim_client import NIMClient
import tempfile
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_core.messages import HumanMessage

from src.kavita_uploader import KavitaUploader
from src.multiple_angles_generator import MultipleAnglesGenerator
from src.zimage_generator import ZImageGenerator

load_dotenv()

app = Flask(__name__, template_folder='src/templates')
app.secret_key = os.getenv("SECRET_KEY", "change-me")
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "1024")) * 1024 * 1024


# Kavita API (optional): if set, a library scan is triggered after each upload.

# ComfyUI (media content generator): base URL of a running ComfyUI server,
# and an optional path to the workflow JSON (defaults to the bundled one).
COMFYUI_URL = os.getenv("COMFYUI_URL", "").rstrip("/")
COMFYUI_WORKFLOW = os.getenv("COMFYUI_WORKFLOW", "").strip()
# NVIDIA NIM (image-to-prompt) configuration
NIM_VISION_MODEL = os.getenv("NIM_VISION_MODEL", "meta/llama-3.2-11b-vision-instruct")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
# Local folder where the ComfyUI server saves generated images, used for the
# download button.
COMFYUI_OUTPUT_DIR = Path(
    os.getenv("COMFYUI_OUTPUT_DIR", "~/ComfyUI/output")
).expanduser()
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}









# Emoji shown on the home page for apps we know by name; anything else gets a
# generic one. Purely cosmetic — an unknown app still works.
APP_ICONS = {
    "Kavita": "📖",
    "Immich": "📷",
    "Jellyfin": "🎬",
    "Plex": "🎬",
    "Navidrome": "🎵",
    "Nextcloud": "☁️",
    "Home Assistant": "🏠",
    "Audiobookshelf": "🎧",
}


def load_apps() -> dict[str, str]:
    """Read APP_<NAME>=<url> entries from the environment.

    Example: APP_IMMICH=http://nas:2283 -> {"Immich": "http://nas:2283"}
    A bare host is assumed to be http://, and an entry carrying any other
    scheme is dropped so a stray value cannot become a javascript: link.
    """
    apps = {}
    for key, value in os.environ.items():
        if not key.startswith("APP_") or not value.strip():
            continue
        url = value.strip()
        scheme = re.match(r"([A-Za-z][A-Za-z0-9+.\-]*)://", url)
        if scheme:
            if scheme.group(1).lower() not in ("http", "https"):
                continue
        elif re.match(r"[A-Za-z][A-Za-z0-9+.\-]*:(?!\d)", url):
            # 'javascript:', 'data:', 'file:' … — never a link target. The
            # lookahead keeps a bare 'host:port' out of this branch.
            continue
        else:
            url = "http://" + url
        name = key[len("APP_") :].replace("_", " ").strip().title()
        apps[name] = url
    return dict(sorted(apps.items(), key=lambda item: item[0].casefold()))


EXTERNAL_APPS = load_apps()


def _safe_output_path(relative_path: str) -> Path | None:
    """Resolve a path inside COMFYUI_OUTPUT_DIR, rejecting traversal and symlinks outside it."""
    if not COMFYUI_OUTPUT_DIR.is_dir():
        return None
    root = COMFYUI_OUTPUT_DIR.resolve()
    try:
        candidate = (root / relative_path).resolve() if relative_path else root
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


@app.route("/", methods=["GET"])
def index():
    """Launcher: the uploader plus links to other self-hosted apps."""
    return render_template(
        "home.html", external_apps=EXTERNAL_APPS, app_icons=APP_ICONS
    )


@app.route("/uploader", methods=["GET"])
def uploader():
        uploader = KavitaUploader()
        series_by_library = uploader.get_series_by_library()
        return render_template(
            "uploader.html",
            libraries=uploader.libraries,
            series_by_library=series_by_library,
            allowed_extensions=sorted(uploader.allowed_extensions),
            kavita_scan_enabled=uploader.kavita_scan_enabled,
        )


@app.route("/media", methods=["GET", "POST"])
def media_generator():
    """Media content generator: generate AI images via the ComfyUI server."""
    images: list[dict] = []
    prompt = ""
    uploaded_image_b64 = None
    active_tab = request.form.get("active_tab", "image-to-prompt")
    if active_tab not in {"image-to-prompt", "prompt-to-image", "multiple-angles"}:
        active_tab = "image-to-prompt"
    # Load models from JSON
    models_data = {}
    MODELS_JSON_PATH = os.path.join(os.path.dirname(__file__), 'static', 'prompt_to_images_prompts.json')
    if not os.path.exists(MODELS_JSON_PATH):
        flash(f"Configuration error: {MODELS_JSON_PATH} not found. Please create it from prompt_to_images_prompts.json.example", "error")
    else:
        with open(MODELS_JSON_PATH, "r") as f:
            models_data = json.load(f)
    # Load image-to-prompt prompts from JSON
    image_to_prompt_data = {}
    IMAGE_TO_PROMPT_JSON_PATH = os.path.join(os.path.dirname(__file__), 'static', 'image_to_prompt_prompts.json')
    if not os.path.exists(IMAGE_TO_PROMPT_JSON_PATH):
        flash(f"Configuration error: {IMAGE_TO_PROMPT_JSON_PATH} not found. Please create it from image_to_prompt_prompts.json.example", "error")
    else:
        with open(IMAGE_TO_PROMPT_JSON_PATH, "r") as f:
            image_to_prompt_data = json.load(f)
    if request.method == "POST":
        if request.form.get("generate_prompt"):
            image_file = request.files.get("image_file")
            if image_file and image_file.filename:
                # Save uploaded image to a temporary file
                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                        image_file.save(tmp.name)
                        tmp_path = tmp.name
                    try:
                        nim_client = NIMClient()
                        template_name = request.form.get("image_to_prompt_template", "").strip()
                        prompt_template = image_to_prompt_data.get(template_name)
                        if template_name and not prompt_template:
                            flash("Please choose a valid prompt template.", "error")
                        else:
                            prompt = nim_client.generate_prompt(tmp_path, prompt_template=prompt_template)
                    except Exception as exc:
                        flash(f"Failed to generate prompt from image: {exc}", "error")
                        prompt = ""
                    with open(tmp_path, "rb") as f:
                        uploaded_image_b64 = base64.b64encode(f.read()).decode("utf-8")
                except Exception as exc:
                    flash(f"Error processing image: {exc}", "error")
                finally:
                    if tmp_path:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
            else:
                flash("Please upload an image to generate a prompt.", "error")
        elif request.form.get("generate_image"):
            prompt = request.form.get("prompt", "").strip()
            if not prompt:
                flash("Please enter a prompt to generate an image.", "error")
            elif not COMFYUI_URL:
                flash(
                    "ComfyUI is not configured — set COMFYUI_URL in your .env file.",
                    "error",
                )
            else:
                model_name = request.form.get("model", "").strip()
                if not model_name:
                    flash("Please select a model before generating an image.", "error")
                else:
                    # Prepend model description if available
                    model_info = models_data.get(model_name, {})
                    description = model_info.get("description", "")
                    if description:
                        prompt = f"{description} {prompt}"
                    generator = ZImageGenerator(
                        base_url=COMFYUI_URL, workflow_path=COMFYUI_WORKFLOW or None
                    )
                    try:
                        results = generator.generate(prompt)
                        images = [
                            {
                                **image,
                                "path": "/".join(
                                    part
                                    for part in (image.get("subfolder", ""), image["filename"])
                                    if part
                                ),
                            }
                            for image in results
                        ]
                        if not images:
                            flash(
                                "Generation finished but no images were returned.", "error"
                            )
                    except Exception as exc:
                        flash(f"Image generation failed: {exc}", "error")
        elif request.form.get("generate_angles"):
            if not COMFYUI_URL:
                flash(
                    "ComfyUI is not configured — set COMFYUI_URL in your .env file.",
                    "error",
                )
            else:
                image_file = request.files.get("angles_image")
                prepend = request.form.get("angles_prepend", "").strip()
                angle_prompt = request.form.get("angles_prompt", "").strip()
                append = request.form.get("angles_append", "").strip()
                if not image_file or not image_file.filename:
                    flash("Please upload an image to generate multiple angles.", "error")
                elif not angle_prompt:
                    flash("Please enter a prompt to generate multiple angles.", "error")
                else:
                    generator = MultipleAnglesGenerator(base_url=COMFYUI_URL)
                    try:
                        results = generator.generate(
                            image_file,
                            prepend=prepend,
                            prompts=angle_prompt,
                            append=append,
                        )
                        images = [
                            {
                                **image,
                                "path": "/".join(
                                    part
                                    for part in (image.get("subfolder", ""), image["filename"])
                                    if part
                                ),
                            }
                            for image in results
                        ]
                        if not images:
                            flash("Generation finished but no images were returned.", "error")
                    except Exception as exc:
                        flash(f"Multiple-angle generation failed: {exc}", "error")
        # If neither button pressed, do nothing (just redisplay)
    return render_template(
        "media.html",
        images=images,
        prompt=prompt,
        uploaded_image_b64=uploaded_image_b64,
        configured=bool(COMFYUI_URL),
        nim_configured=bool(NVIDIA_API_KEY),
        image_to_prompt_prompts=image_to_prompt_data,
        models=models_data,
        active_tab=active_tab,
    )


@app.route("/gallery")
def gallery():
    """Browse ComfyUI's local output folder."""
    relative_path = request.args.get("path", "").strip("/")
    if not COMFYUI_OUTPUT_DIR.is_dir():
        return render_template(
            "gallery.html",
            output_dir_exists=False,
            folders=[],
            files=[],
            breadcrumbs=[],
        )
    current = _safe_output_path(relative_path)
    if current is None or not current.is_dir():
        abort(404)
    relative_path = current.relative_to(COMFYUI_OUTPUT_DIR.resolve()).as_posix()

    folders, files = [], []
    for item in current.iterdir():
        if item.name.startswith("."):
            continue
        safe_item = _safe_output_path(item.relative_to(COMFYUI_OUTPUT_DIR).as_posix())
        if safe_item is None:
            continue
        relative = item.relative_to(COMFYUI_OUTPUT_DIR).as_posix()
        if item.is_dir():
            folders.append({"name": item.name, "path": relative})
        elif item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS:
            files.append((item.stat().st_mtime_ns, {
                "name": item.name,
                "path": relative,
            }))

    folders.sort(key=lambda item: item["name"].casefold())
    files.sort(key=lambda item: item[0], reverse=True)
    files = [image for _, image in files]

    parts = [part for part in relative_path.split("/") if part]
    breadcrumbs = [
        {"name": part, "path": "/".join(parts[: index + 1])}
        for index, part in enumerate(parts)
    ]
    return render_template(
        "gallery.html",
        output_dir_exists=COMFYUI_OUTPUT_DIR.is_dir(),
        folders=folders,
        files=files,
        breadcrumbs=breadcrumbs,
    )


@app.route("/gallery/file")
def gallery_file():
    """Show or download a file from ComfyUI's local output folder."""
    relative_path = request.args.get("path", "").strip("/")
    current = _safe_output_path(relative_path)
    if (
        current is None
        or not current.is_file()
        or current.suffix.lower() not in IMAGE_EXTENSIONS
    ):
        abort(404)
    relative_path = current.relative_to(COMFYUI_OUTPUT_DIR.resolve()).as_posix()
    return send_from_directory(
        COMFYUI_OUTPUT_DIR,
        relative_path,
        as_attachment=request.args.get("download") == "1",
    )


@app.route("/upload", methods=["POST"])
def upload():
        uploader = KavitaUploader()
        library_name = request.form.get("library", "")
        series_name = uploader.sanitize_series_name(request.form.get("series", ""))
        year = request.form.get("year", "").strip()
        chapter = request.form.get("chapter", "").strip()
        files = [f for f in request.files.getlist("files") if f and f.filename]

        if year and not re.fullmatch(r"\d{4}", year):
            flash("Year must be a 4-digit number.", "error")
            return redirect(url_for("uploader"))
        if library_name not in uploader.libraries:
            flash("Please choose a valid library.", "error")
            return redirect(url_for("uploader"))
        if not files:
            flash("Please choose at least one file to upload.", "error")
            return redirect(url_for("uploader"))
        library_root = uploader.libraries[library_name]
        if not library_root.is_dir():
            flash(f"Library path does not exist: {library_root}", "error")
            return redirect(url_for("uploader"))
        series_dir = library_root / series_name
        # Guard against any path traversal that slipped through sanitization.
        if series_dir.resolve().parent != library_root.resolve():
            flash("Invalid series name.", "error")
            return redirect(url_for("uploader"))
        is_new_series = not series_dir.exists()
        if not is_new_series:
            year = ""  # the year only applies to brand-new series
        series_dir.mkdir(parents=True, exist_ok=True)

        saved, skipped = [], []
        for file in files:
            original = uploader.sanitize_filename(file.filename)
            if not original or not uploader.allowed_file(original):
                skipped.append(f"{file.filename} (file type not allowed)")
                continue
            filename, numbered = uploader.kavita_filename(series_name, original, year, chapter)
            if not numbered and len(files) > 1:
                # No chapter and several files — shared name would collide, so keep original.
                filename = original
            destination = series_dir / filename
            if destination.exists():
                skipped.append(f"{filename} (already exists)")
                continue
            file.save(destination)
            saved.append(filename if filename == original else f"{original} → {filename}")

        if saved:
            flash("Saved: " + ", ".join(saved), "success")
        if skipped:
            flash("Skipped: " + ", ".join(skipped), "warning")
        # Trigger Kavita scan if configured
        scan_error = uploader.trigger_kavita_scan()
        if scan_error:
            flash(scan_error, "error")
        return redirect(url_for("uploader"))
if __name__ == "__main__":
    uploader = KavitaUploader()
    if not uploader.libraries:
        raise SystemExit(
            "No libraries configured. Add at least one LIBRARY_<NAME>=<path> entry to your .env file."
        )
    app.run(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("DEBUG", "false").lower() == "true",
    )
