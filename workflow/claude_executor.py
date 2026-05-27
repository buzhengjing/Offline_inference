"""Claude CLI execution wrapper."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional


class ClaudeExecutor:
    """Wraps `claude -p` invocations with timeout, logging, and stream capture."""

    def __init__(self, project_root: Path, extract_script: Optional[Path] = None):
        self.project_root = project_root
        self.extract_script = extract_script or (project_root / "tools" / "extract_stream_text.py")

    def run(
        self,
        prompt: str,
        max_turns: int,
        timeout_seconds: float,
        log_path: Path,
        jsonl_path: Path,
        env_extra: Optional[dict[str, str]] = None,
        verbose: bool = False,
    ) -> int:
        """Run claude -p with streaming output capture.

        Returns the claude process exit code.
        """
        log_path.parent.mkdir(parents=True, exist_ok=True)
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)

        claude_cmd = [
            "claude", "-p", prompt,
            "--permission-mode", "auto",
            "--max-turns", str(max_turns),
            "--output-format", "stream-json",
            "--verbose",
        ]

        extract_cmd = [
            "python3", str(self.extract_script),
            "--jsonl-out", str(jsonl_path),
        ]

        with open(log_path, "a", encoding="utf-8") as log_f:
            claude_proc = subprocess.Popen(
                claude_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(self.project_root),
            )

            extract_proc = subprocess.Popen(
                extract_cmd,
                stdin=claude_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(self.project_root),
            )

            # Allow claude_proc to receive SIGPIPE if extract_proc exits
            claude_proc.stdout.close()

            try:
                stdout_data, _ = extract_proc.communicate(timeout=timeout_seconds)
                if stdout_data:
                    text = stdout_data.decode("utf-8", errors="replace")
                    log_f.write(text)
                    print(text, end="", flush=True)
            except subprocess.TimeoutExpired:
                extract_proc.kill()
                claude_proc.kill()
                extract_proc.wait()
                claude_proc.wait()
                raise

            claude_proc.wait()
            return claude_proc.returncode or 0
