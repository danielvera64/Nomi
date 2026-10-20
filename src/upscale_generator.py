"""Upscale an uploaded image with the bundled SeedVR2 workflow."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import BinaryIO

import websocket

from src.multiple_angles_generator import MultipleAnglesGenerator


class UpscaleGenerator(MultipleAnglesGenerator):
    """Upload an image and queue the SeedVR2 upscale workflow."""

    DEFAULT_WORKFLOW = Path(__file__).resolve().parent.parent / "workflows" / "seedvr2_upscale.json"

    def generate(self, image: str | Path | BinaryIO, seed: int | None = None) -> list[dict]:
        """Upload the image, queue the workflow, and return upscaled image references."""
        workflow = self._to_api_workflow(self._load_workflow(self.workflow_path))
        self._find_node(workflow, "LoadImage")["inputs"]["image"] = self._upload_image(image)
        self._set_seed(workflow, seed)

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
