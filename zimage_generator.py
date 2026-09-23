"""Image generation via a ComfyUI server, using a bundled workflow.

Adapted from the influencer_generator project: the workflow JSON is loaded
from disk, the prompt is injected into the positive-prompt node, the prompt
is queued on a running ComfyUI server, and completion is tracked over the
server websocket. Output image references are returned so the caller can
build /view URLs on the ComfyUI server.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import websocket


class ZImageGenerator:
    DEFAULT_BASE_URL = "http://127.0.0.1:8188"
    DEFAULT_WORKFLOW = Path(__file__).resolve().parent / "workflows" / "ZImageTurbo-Workflow-API.json"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        workflow_path: str | Path | None = None,
        timeout: int = 300,
    ):
        base_url = (base_url or self.DEFAULT_BASE_URL).strip().rstrip("/")
        if "://" not in base_url:
            base_url = "http://" + base_url
        self.base_url = base_url
        scheme, host = base_url.split("://", 1)
        ws_scheme = "wss" if scheme.lower() == "https" else "ws"
        self.ws_url = f"{ws_scheme}://{host}"
        self.client_id = str(uuid.uuid4())
        self.timeout = timeout
        self.workflow_path = Path(workflow_path).expanduser() if workflow_path else self.DEFAULT_WORKFLOW

    @staticmethod
    def _load_workflow(path: Path) -> dict:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _find_positive_prompt_node(workflow: dict) -> dict:
        for node in workflow.values():
            if node.get("class_type") == "CLIPTextEncode":
                title = node.get("_meta", {}).get("title", "").lower()
                if "positive" in title:
                    return node
        raise ValueError("Positive prompt node not found in workflow.")

    def _queue_prompt(self, workflow: dict, prompt_id: str) -> None:
        payload = {"prompt": workflow, "client_id": self.client_id, "prompt_id": prompt_id}
        req = urllib.request.Request(
            f"{self.base_url}/prompt",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            response.read()

    def _get_history(self, prompt_id: str) -> dict:
        with urllib.request.urlopen(
            f"{self.base_url}/history/{prompt_id}", timeout=self.timeout
        ) as response:
            return json.loads(response.read())

    def _wait_for_completion(self, ws, prompt_id: str) -> None:
        while True:
            out = ws.recv()
            if not isinstance(out, str):
                continue
            message = json.loads(out)
            if message.get("type") != "executing":
                continue
            data = message.get("data", {})
            if data.get("node") is None and data.get("prompt_id") == prompt_id:
                return

    def _extract_images(self, prompt_id: str) -> list[dict]:
        history = self._get_history(prompt_id)
        outputs = history.get(prompt_id, {}).get("outputs", {})
        images: list[dict] = []
        for node_output in outputs.values():
            for image in node_output.get("images", []):
                images.append(
                    {
                        "filename": image["filename"],
                        "subfolder": image.get("subfolder", ""),
                        "type": image.get("type", "output"),
                    }
                )
        return images

    def generate(self, prompt: str) -> list[dict]:
        """Queue the workflow and return the output image references."""
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Prompt must not be empty.")

        workflow = self._load_workflow(self.workflow_path)
        self._find_positive_prompt_node(workflow)["inputs"]["text"] = prompt

        prompt_id = str(uuid.uuid4())
        ws = websocket.WebSocket()
        try:
            ws.connect(f"{self.ws_url}/ws?clientId={self.client_id}")
            ws.settimeout(self.timeout)
            self._queue_prompt(workflow, prompt_id)
            self._wait_for_completion(ws, prompt_id)
        finally:
            ws.close()

        return self._extract_images(prompt_id)

    @staticmethod
    def view_url(base_url: str, image: dict) -> str:
        """Absolute /view URL on the ComfyUI server for an output image."""
        query = urllib.parse.urlencode(
            {
                "filename": image["filename"],
                "subfolder": image["subfolder"],
                "type": image["type"],
            }
        )
        return f"{base_url}/view?{query}"
