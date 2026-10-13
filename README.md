# Nomi

Nomi is a small Flask app for uploading comics, manga, and books into a
self-hosted [Kavita](https://www.kavitareader.com/) library, generating media
with ComfyUI, and browsing the generated output.

The home page is a launcher for:

- the Kavita uploader
- the media content generator
- the gallery
- links to your other self-hosted apps

## Uploader

Files are stored in the layout Kavita expects:

```
<Library Root>/
└── <Series Name>/
    ├── Series Name #1.cbz
    └── Series Name #2.cbz
```

The app creates the series folder if it does not exist, or adds files to it if
it does. Existing series are suggested when choosing a series. The UI is
mobile-friendly, so you can upload from a phone.

Current filename behavior:

- If a chapter number is provided, files are named `<Series> #<chapter>.<ext>`.
- If no chapter number is provided, a single file is named `<Series>.<ext>`.
- If several files are uploaded without a chapter number, their original names
  are kept to avoid collisions.
- The optional year field is currently informational only and is not used in
  filenames.
- Automatic volume/chapter detection from the original filename is not
  performed in the current build.

Files that already exist in the target folder are skipped, never overwritten.

## Media generator

The media generator has three modes:

- **Image → Prompt** — upload an image and use an NVIDIA NIM vision model to
  generate a text prompt.
- **Prompt → Image** — enter a prompt and generate an image through a
  configured ComfyUI server.
- **Multiple angles** — upload a reference image, provide optional prepend and
  append text, enter a prompt, and generate several character angles with the
  bundled Flux2 workflow.

Prompt-to-image uses the selected entry’s `description` as a prompt prefix;
the configured ComfyUI workflow still controls the actual generation model.
Generated images are previewed and downloaded from the local
`COMFYUI_OUTPUT_DIR`, not from ComfyUI’s HTTP `/view` endpoint.
The selected media-generator tab stays active after generation and validation
errors.

If ComfyUI runs on another machine, make sure its output directory is mounted
or shared with Nomi at the path set in `COMFYUI_OUTPUT_DIR`.

The multiple-angles mode requires the ComfyUI
`comfyui-simple-prompt-batcher` custom node and the Flux2 Klein model files
named in `workflows/FLUX2-DEV-KLEIN_4_and_9B_1_click_multiple_character_angles-v1.0.json`.

## Gallery

The gallery browses `COMFYUI_OUTPUT_DIR` and supports nested folders.

- Only image files are shown: `.png`, `.jpg`, `.jpeg`, `.webp`, and `.gif`.
- Images are sorted newest to oldest by file modification time.
- Folders are sorted alphabetically.
- Each image has a preview and a download link.
- Path traversal and symlinks pointing outside the output folder are blocked.

## Setup

```bash
cd Nomi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp static/image_to_prompt_prompts.json.example static/image_to_prompt_prompts.json
cp static/prompt_to_images_prompts.json.example static/prompt_to_images_prompts.json
python app.py
```

Open http://localhost:5000 in your browser.

## Configuration (.env)

| Variable | Description |
| --- | --- |
| `LIBRARY_<NAME>` | One per library: root path of that Kavita library (e.g. `LIBRARY_MANGA=/data/manga`, `LIBRARY_BOOKS=/data/books`). Add as many as you need. |
| `APP_<NAME>` | One per external app: its URL (e.g. `APP_KAVITA=http://nas:5001`, `APP_IMMICH=http://nas:2283`). Each becomes a button on the home page that opens in a new tab. A bare host gets `http://`; non-http(s) values are ignored. |
| `HOST` / `PORT` | Where the app listens (defaults: `0.0.0.0` / `5000`). |
| `DEBUG` | Whether Flask debug mode is enabled (default: `false`). |
| `SECRET_KEY` | Flask session secret — set to any random string. |
| `MAX_UPLOAD_MB` | Max upload size per request in MB (default: `1024`). |
| `ALLOWED_EXTENSIONS` | Comma-separated list of accepted extensions. |
| `KAVITA_URL` / `KAVITA_API_KEY` | Optional. If both are set, the app triggers a Kavita library scan after each upload (API key: Kavita → Settings → Account → API Key). |
| `COMFYUI_URL` | Optional. Base URL of a running ComfyUI server (e.g. `http://nas:8188`). Enables the media generator. |
| `COMFYUI_WORKFLOW` | Optional. Path to a ComfyUI API workflow JSON (defaults to the bundled `workflows/ZImageTurbo-Workflow-API.json`). |
| `COMFYUI_OUTPUT_DIR` | Optional. Local folder where ComfyUI saves generated images; used by the media generator and gallery (default: `~/ComfyUI/output`). |
| `NVIDIA_API_KEY` | Optional. NVIDIA API key for accessing NIM models. Required for image-to-prompt. |
| `NIM_VISION_MODEL` | Optional. Vision model to use for generating prompts from images (default: `meta/llama-3.2-11b-vision-instruct`). |

Without the Kavita API settings, files are still stored correctly and Kavita
will pick them up on its next scheduled scan, or on a manual scan.

## Prompt configuration files

The media generator reads two local JSON files:

- `static/image_to_prompt_prompts.json`
  - Maps a display name to the prompt sent to the NIM vision model.
  - Example: `{"Descriptive": "Describe this image in detail..."}`
- `static/prompt_to_images_prompts.json`
  - Maps a display name to an object with a `description` that is prepended to
    the user’s prompt before ComfyUI generation.
  - Example: `{"Stable Diffusion XL": {"description": "..."}}`

The `.example` files are templates. The actual JSON files are ignored by Git,
so copy them during setup and edit them to match your preferred prompts.

## Notes

- App links are used as given, so point them at addresses reachable from the
  device you browse from — `localhost` will not resolve from your phone.
- Series names are sanitized and path traversal is blocked.
- The app has no authentication. Keep it on your LAN or put it behind a
  reverse proxy with authentication if you expose it.
- The gallery and media generator expose image files under
  `COMFYUI_OUTPUT_DIR`, so keep that directory limited to content you are
  comfortable serving from Nomi.

## License

[MIT](LICENSE)
