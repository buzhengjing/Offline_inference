#!/usr/bin/env python3
"""从 claude --output-format stream-json 流中提取可读文本并保存完整 JSONL。

用法:
    claude -p "..." --output-format stream-json --verbose 2>&1 | \
        python3 tools/extract_stream_text.py --jsonl-out logs/seg1_stream.jsonl

行为:
    - stdin: 读取 stream-json 流（每行一个 JSON 对象）
    - stdout: 输出人类可读的文本（assistant text blocks + tool_use 摘要）
    - --jsonl-out: 完整 JSON 流写入文件（含 tool calls、results 等）
"""

import argparse
import json
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="Extract readable text from claude stream-json")
    parser.add_argument("--jsonl-out", required=True, help="Path to write full JSONL stream")
    return parser.parse_args()


def format_tool_use(tool_use):
    name = tool_use.get("name", "unknown")
    inp = tool_use.get("input", {})
    if name == "Bash":
        cmd = inp.get("command", "")
        return f"  [tool] Bash: {cmd[:120]}"
    elif name in ("Read", "Write", "Edit"):
        path = inp.get("file_path", "")
        return f"  [tool] {name}: {path}"
    elif name == "WebSearch":
        query = inp.get("query", "")
        return f"  [tool] WebSearch: {query[:80]}"
    else:
        return f"  [tool] {name}"


def main():
    args = parse_args()

    with open(args.jsonl_out, "w", buffering=1) as jsonl_f:
        for line in sys.stdin:
            line = line.rstrip("\n")
            if not line:
                continue

            jsonl_f.write(line + "\n")

            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "assistant":
                message = msg.get("message", {})
                content = message.get("content", [])
                for block in content:
                    block_type = block.get("type")
                    if block_type == "text":
                        text = block.get("text", "")
                        if text.strip():
                            print(text, flush=True)
                    elif block_type == "tool_use":
                        summary = format_tool_use(block)
                        print(summary, flush=True)

            elif msg_type == "result":
                subtype = msg.get("subtype", "")
                duration = msg.get("duration_ms", 0)
                turns = msg.get("num_turns", 0)
                cost = msg.get("total_cost_usd", 0)
                print(f"\n--- [result: {subtype}] turns={turns} duration={duration/1000:.1f}s cost=${cost:.4f} ---", flush=True)


if __name__ == "__main__":
    main()
