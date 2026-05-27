"""LLM Node base class — wraps Claude calls with structured output enforcement."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Type

from pydantic import BaseModel

from .node import BaseNode, NodeResult, NodeType
from .retry import RetryPolicy

if TYPE_CHECKING:
    from logging import Logger

    from .state import WorkflowState


class LLMNode(BaseNode):
    """Base for nodes that require Claude LLM reasoning with structured output."""

    output_schema: Type[BaseModel]

    def __init__(
        self,
        node_id: str,
        output_schema: Type[BaseModel],
        timeout: float = 600,
        retry_policy: RetryPolicy | None = None,
        max_turns: int = 5,
    ):
        super().__init__(
            node_id=node_id,
            node_type=NodeType.LLM,
            timeout=timeout,
            retry_policy=retry_policy or RetryPolicy(max_retries=1),
            has_side_effects=False,
        )
        self.output_schema = output_schema
        self.max_turns = max_turns

    def build_prompt(self, state: WorkflowState) -> str:
        """Build the prompt for Claude. Must be implemented by subclasses."""
        raise NotImplementedError

    def execute(self, state: WorkflowState, logger: Logger, ctx=None) -> NodeResult:
        prompt = self.build_prompt(state)
        schema_json = json.dumps(
            self.output_schema.model_json_schema(), indent=2, ensure_ascii=False
        )
        full_prompt = self._wrap_prompt_with_schema(prompt, schema_json)

        response_text = self._call_claude(full_prompt, state, logger)

        try:
            parsed = self.output_schema.model_validate_json(response_text)
            return NodeResult(success=True, data=parsed.model_dump())
        except Exception as e:
            # Try to extract JSON from response
            extracted = self._extract_json(response_text)
            if extracted:
                try:
                    parsed = self.output_schema.model_validate_json(extracted)
                    return NodeResult(success=True, data=parsed.model_dump())
                except Exception:
                    pass
            return NodeResult(success=False, error=f"Failed to parse LLM output: {e}")

    def _wrap_prompt_with_schema(self, prompt: str, schema_json: str) -> str:
        return f"""{prompt}

---
你必须以 JSON 格式输出，严格遵循以下 schema：

```json
{schema_json}
```

只输出 JSON，不要输出其他内容。"""

    def _call_claude(self, prompt: str, state: WorkflowState, logger: Logger) -> str:
        project_root = Path(state.project_root)
        cmd = [
            "claude", "-p", prompt,
            "--permission-mode", "auto",
            "--max-turns", str(self.max_turns),
            "--output-format", "text",
        ]

        env = os.environ.copy()
        env["HARBOR_USER"] = state.harbor_user
        env["HARBOR_PASSWORD"] = state.harbor_password

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(project_root),
                env=env,
            )
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"Claude call timed out after {self.timeout}s for node {self.node_id}")

    def _extract_json(self, text: str) -> str | None:
        """Try to extract JSON object from text that may contain markdown fences."""
        # Try to find JSON between ```json ... ``` or { ... }
        import re
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return match.group(0)
        return None
