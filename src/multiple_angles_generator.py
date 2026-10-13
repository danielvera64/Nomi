"""Generate multiple character angles through the bundled Flux2 workflow."""

from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path
from typing import BinaryIO

import requests
import websocket

from src.zimage_generator import ZImageGenerator


class MultipleAnglesGenerator(ZImageGenerator):
    """Upload a reference image and queue the multiple-angles workflow."""

    DEFAULT_WORKFLOW = (
        Path(__file__).resolve().parent.parent
        / "workflows"
        / "FLUX2-DEV-KLEIN_4_and_9B_1_click_multiple_character_angles-v1.0.json"
    )

    @staticmethod
    def _find_node(workflow: dict, class_type: str) -> dict:
        for node in workflow.values():
            if node.get("class_type") == class_type:
                return node
        raise ValueError(f"{class_type} node not found in workflow.")

    @staticmethod
    def _widget_value(node: dict, node_input: dict) -> object:
        named_values = node.get("widgets_values_named") or {}
        name = node_input["name"]
        if name in named_values:
            return named_values[name]

        values = node.get("widgets_values") or []
        widget_inputs = [item for item in node.get("inputs", []) if item.get("widget")]
        names = [item["name"] for item in widget_inputs]
        if name not in names:
            return None
        index = names.index(name)
        return values[index] if index < len(values) else None

    @staticmethod
    def _normalize_links(links: list) -> dict:
        normalized = {}
        for link in links:
            if isinstance(link, list):
                link = {
                    "id": link[0],
                    "origin_id": link[1],
                    "origin_slot": link[2],
                    "target_id": link[3],
                    "target_slot": link[4],
                    "type": link[5],
                }
            normalized[link["id"]] = link
        return normalized

    @classmethod
    def _flatten_workflow(
        cls,
        scope: dict,
        definitions: dict,
        prefix: str = "",
        external_inputs: dict | None = None,
    ) -> tuple[dict, dict]:
        """Convert a ComfyUI editor workflow with subgraphs into API format."""
        graph: dict = {}
        outputs: dict = {}
        processed: dict = {}
        processing: set[int] = set()
        external_inputs = external_inputs or {}
        links = cls._normalize_links(scope["links"])
        nodes = {node["id"]: node for node in scope["nodes"]}

        def resolve_origin(node_id: int, slot: int):
            if node_id == -10:
                return external_inputs[slot]
            if node_id in processed:
                return processed[node_id][slot]
            if node_id in processing:
                raise ValueError("Cycle detected in workflow.")
            process(node_id)
            return processed[node_id][slot]

        def process(node_id: int) -> None:
            if node_id in processed:
                return
            processing.add(node_id)
            node = nodes[node_id]

            if node["type"] in definitions:
                subgraph = definitions[node["type"]]
                incoming = {}
                for slot, node_input in enumerate(node.get("inputs", [])):
                    if node_input.get("link") is not None:
                        link = links[node_input["link"]]
                        incoming[slot] = resolve_origin(
                            link["origin_id"], link["origin_slot"]
                        )
                    else:
                        incoming[slot] = cls._widget_value(node, node_input)

                subgraph_nodes, subgraph_outputs = cls._flatten_workflow(
                    subgraph, definitions, f"{prefix}{node_id}.", incoming
                )
                graph.update(subgraph_nodes)
                processed[node_id] = subgraph_outputs
            else:
                api_id = f"{prefix}{node_id}"
                inputs = {}
                for node_input in node.get("inputs", []):
                    if node_input.get("link") is not None:
                        link = links[node_input["link"]]
                        inputs[node_input["name"]] = resolve_origin(
                            link["origin_id"], link["origin_slot"]
                        )
                    elif node_input.get("widget"):
                        inputs[node_input["name"]] = cls._widget_value(
                            node, node_input
                        )

                graph[api_id] = {
                    "inputs": inputs,
                    "class_type": node["type"],
                    "_meta": {"title": node.get("title", node["type"])},
                }
                processed[node_id] = {
                    slot: [api_id, slot]
                    for slot in range(len(node.get("outputs", [])))
                }

            processing.remove(node_id)

        for node in scope["nodes"]:
            process(node["id"])

        for link in links.values():
            if link["target_id"] == -20:
                outputs[link["target_slot"]] = resolve_origin(
                    link["origin_id"], link["origin_slot"]
                )

        return graph, outputs

    @classmethod
    def _to_api_workflow(cls, workflow: dict) -> dict:
        definitions = {
            subgraph["id"]: subgraph
            for subgraph in workflow.get("definitions", {}).get("subgraphs", [])
        }
        graph, _ = cls._flatten_workflow(workflow, definitions)
        return graph

    def _upload_image(self, image: str | Path | BinaryIO) -> str:
        close_file = False
        if isinstance(image, (str, Path)):
            image_path = Path(image)
            file_object = image_path.open("rb")
            filename = image_path.name
            close_file = True
        else:
            file_object = image
            filename = Path(getattr(image, "filename", "") or "image.png").name

        content_type = (
            getattr(image, "mimetype", None)
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )
        file_bytes = file_object.read()
        try:
            response = requests.post(
                f"{self.base_url}/upload/image",
                files={"image": (filename, file_bytes, content_type)},
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
        finally:
            if close_file:
                file_object.close()

        name = result.get("name")
        if not name:
            raise RuntimeError("ComfyUI did not return an uploaded image name.")
        subfolder = result.get("subfolder", "")
        return f"{subfolder}/{name}" if subfolder else name

    def generate(
        self,
        image: str | Path | BinaryIO,
        prepend: str,
        prompts: str,
        append: str,
    ) -> list[dict]:
        """Upload the reference image, fill the batcher, and return output images."""
        prepend = prepend.strip()
        prompts = prompts.strip()
        append = append.strip()
        if not prompts:
            raise ValueError("Prompt must not be empty.")

        workflow = self._to_api_workflow(self._load_workflow(self.workflow_path))
        self._find_node(workflow, "LoadImage")["inputs"]["image"] = self._upload_image(image)
        batcher = self._find_node(workflow, "SimplePromptBatcher")
        batcher["inputs"]["prepend"] = prepend
        batcher["inputs"]["prompts"] = prompts
        batcher["inputs"]["append"] = append

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
