"""NVIDIA NIM client for image-to-prompt generation."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_core.messages import HumanMessage


class NIMClient:
    """Encapsulates interaction with NVIDIA NIM vision model."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
    ):
        self.model = model or os.getenv(
            "NIM_VISION_MODEL", "meta/llama-3.2-11b-vision-instruct"
        )
        self.api_key = (api_key or os.getenv("NVIDIA_API_KEY", "")).strip()
        if not self.api_key:
            raise RuntimeError("NVIDIA_API_KEY not set")

    def generate_prompt(self, image_path: str | Path) -> str:
        """Generate a text prompt from an image using the NIM vision model."""
        llm = ChatNVIDIA(model=self.model)
        with open(image_path, "rb") as img_file:
            base64_image = base64.b64encode(img_file.read()).decode("utf-8")
        result = llm.invoke(
            [
                HumanMessage(
                    content=[
                        {
                            "type": "text",
                            "text": """
                 Create a prompt to replicate this image. 
                 Describe the scene, objects, style, and layout but do not mention the medium or that it is an image/photo.
                 Describe the person age and gender but don't include any information about the how the person looks.
                 Omit words like \"photo of\" or \"image of\" or \"create an image of\".
                 Not longer than 2 paragraphs. Omit titles
                 """,
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{base64_image}"},
                        },
                    ]
                )
            ]
        )
        return result.content.strip()
