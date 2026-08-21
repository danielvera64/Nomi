# Nomi

Nomi is a small Flask web app for uploading comics and manga into a self-hosted
[Kavita](https://www.kavitareader.com/) library. Files are stored in the layout
Kavita expects:

```
<Library Root>/
└── <Series Name>/
    ├── Series Name v01.cbz
    └── Series Name v02.cbz
```

The app creates the series folder if it doesn't exist, or adds files to it if
it does. Existing series are suggested as you type so you don't create
duplicate folders.

## Setup

```bash
cd nomi-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env with your library paths
python app.py
```

Open http://localhost:5000 in your browser.

## Configuration (.env)

| Variable | Description |
| --- | --- |
| `LIBRARY_<NAME>` | One per library: root path of that Kavita library (e.g. `LIBRARY_MANGA=/data/manga`). Add as many as you need. |
| `HOST` / `PORT` | Where the app listens (defaults: `0.0.0.0` / `5000`). |
| `SECRET_KEY` | Flask session secret — set to any random string. |
| `MAX_UPLOAD_MB` | Max upload size per request in MB (default `1024`). |
| `ALLOWED_EXTENSIONS` | Comma-separated list of accepted extensions. |
| `KAVITA_URL` / `KAVITA_API_KEY` | Optional. If both are set, the app triggers a Kavita library scan after each upload (API key: Kavita → Settings → Account → API Key). |

Without the Kavita API settings, files are still stored correctly and Kavita
will pick them up on its next scheduled scan (or a manual scan).

## Notes

- Series names are sanitized (path separators and invalid filesystem
  characters removed) and path traversal is blocked.
- Files that already exist in the target folder are skipped, never overwritten.
- The app has no authentication — keep it on your LAN or put it behind a
  reverse proxy with auth if you expose it.
