#!/usr/bin/env python3
"""
check_model_local.py — 宿主机模型权重搜索工具（精简版）

在常见路径中搜索模型目录，返回最佳匹配路径。

用法:
  python3 check_model_local.py --model "Qwen3-8B" --output-json
  python3 check_model_local.py --model "Qwen3-8B" --no-download --output-json
"""

import argparse
import json
import os
import sys

SEARCH_ROOTS = ["/data", "/mnt", "/nfs", "/share", "/models", "/home"]
MAX_DEPTH = 5


def find_model_dirs(model_name, search_roots):
    model_lower = model_name.lower().replace("-", "").replace("_", "")
    candidates = []

    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            if depth >= MAX_DEPTH:
                dirnames.clear()
                continue

            dirname = os.path.basename(dirpath)
            dirname_lower = dirname.lower().replace("-", "").replace("_", "")

            # 精确匹配
            if dirname_lower == model_lower:
                if _is_model_dir(dirpath, filenames):
                    candidates.append({"path": dirpath, "score": 100, "match": "exact"})
                continue

            # 包含匹配
            if model_lower in dirname_lower or dirname_lower in model_lower:
                if _is_model_dir(dirpath, filenames):
                    candidates.append({"path": dirpath, "score": 50, "match": "partial"})

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def _is_model_dir(dirpath, filenames=None):
    if filenames is None:
        filenames = os.listdir(dirpath) if os.path.isdir(dirpath) else []
    model_indicators = [
        "config.json", "tokenizer.json", "tokenizer_config.json",
        "model.safetensors.index.json", "pytorch_model.bin.index.json",
    ]
    has_indicator = any(f in filenames for f in model_indicators)
    has_weights = any(
        f.endswith((".safetensors", ".bin", ".gguf", ".pt"))
        for f in filenames
    )
    return has_indicator or has_weights


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="模型名称")
    parser.add_argument("--output-json", action="store_true", help="JSON 输出")
    parser.add_argument("--no-download", action="store_true", help="不下载，仅搜索")
    parser.add_argument("--search-roots", nargs="*", default=None, help="自定义搜索路径")
    args = parser.parse_args()

    roots = args.search_roots if args.search_roots else SEARCH_ROOTS
    candidates = find_model_dirs(args.model, roots)

    result = {
        "model": args.model,
        "best_match": candidates[0]["path"] if candidates else "",
        "candidates": candidates[:5],
        "search_roots": roots,
    }

    if args.output_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if candidates:
            print(f"✓ 找到模型: {candidates[0]['path']} ({candidates[0]['match']})")
        else:
            print(f"✗ 未找到模型: {args.model}")
            sys.exit(1)


if __name__ == "__main__":
    main()
