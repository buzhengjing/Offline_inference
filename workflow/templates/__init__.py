"""Inference script template rendering engine."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent


def get_template_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_inference_script(
    template_name: str,
    model_path: str,
    input_file: str = "/root/test_input.txt",
    output_file: str = "/root/output_native.txt",
    overrides: Optional[dict] = None,
    extra_imports: Optional[list[str]] = None,
    special_handling: Optional[str] = None,
) -> str:
    """Render an inference script from a template.

    Args:
        template_name: One of causal_lm, vlm, embedding, reranker, llama_cpp, custom
        model_path: Path to model directory inside container
        input_file: Path to test input file
        output_file: Path to save output
        overrides: Template parameter overrides from LLM
        extra_imports: Additional import statements
        special_handling: Extra code snippet for special cases
    """
    env = get_template_env()
    template = env.get_template(f"{template_name}.py.j2")

    context = {
        "model_path": model_path,
        "input_file": input_file,
        "output_file": output_file,
        "extra_imports": extra_imports or [],
        "special_handling": special_handling or "",
        **(overrides or {}),
    }

    return template.render(**context)


AVAILABLE_TEMPLATES = [
    "causal_lm",
    "vlm",
    "embedding",
    "reranker",
    "llama_cpp",
    "custom",
]
