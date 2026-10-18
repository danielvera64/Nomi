"""NVIDIA NIM client for image-to-prompt generation."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_core.messages import HumanMessage

DEFAULT_NIM_VISION_MODELS = (
    "meta/llama-3.2-11b-vision-instruct,meta/llama-3.2-90b-vision-instruct"
)


def get_vision_models() -> list[str]:
    """Read the comma-separated NIM_VISION_MODEL configuration."""
    configured = os.getenv("NIM_VISION_MODEL") or DEFAULT_NIM_VISION_MODELS
    return [
        model.strip()
        for model in configured.split(",")
        if model.strip()
    ]


class NIMClient:
    """Encapsulates interaction with NVIDIA NIM vision model."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
    ):
        self.model = model or get_vision_models()[0]
        self.api_key = (api_key or os.getenv("NVIDIA_API_KEY", "")).strip()
        if not self.api_key:
            raise RuntimeError("NVIDIA_API_KEY not set")

    def generate_prompt(
        self, image_path: str | Path, prompt_template: str | None = None
    ) -> str:
        """Generate a text prompt from an image using the NIM vision model."""
        llm = ChatNVIDIA(model=self.model)
        with open(image_path, "rb") as img_file:
            base64_image = base64.b64encode(img_file.read()).decode("utf-8")

        # Use provided template or default
        if prompt_template:
            prompt_text = prompt_template
        else:
            prompt_text = """
                 Create a prompt to replicate this image.
                 Omit words like \"photo of\" or \"image of\" or \"create an image of\".
                 Not longer than 2 paragraphs. Omit titles
                 """

        result = llm.invoke(
            [
                HumanMessage(
                    content=[
                        {
                            "type": "text",
                            "text": prompt_text,
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}"
                            },
                        },
                    ]
                )
            ]
        )
        return result.content.strip()
