#!/usr/bin/env python3
"""
download_model.py — 模型下载工具（支持 ModelScope / HuggingFace）

用法:
  python3 download_model.py --model "Qwen/Qwen2.5-0.5B" --source auto --local-dir /data/models/Qwen2.5-0.5B --json
  python3 download_model.py --model "Qwen/Qwen2.5-0.5B" --source modelscope --local-dir /data/models/Qwen2.5-0.5B
  python3 download_model.py --model "Qwen/Qwen2.5-0.5B" --source huggingface --local-dir /data/models/Qwen2.5-0.5B --mirror https://hf-mirror.com
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_HF_MIRROR = "https://hf-mirror.com"


def run_cmd(cmd, env=None, timeout=7200):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        process = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=merged_env
        )
        output_lines = []
        try:
            for line in process.stdout:
                line = line.rstrip('\n')
                print(line)
                output_lines.append(line)
                if len(output_lines) > 500:
                    output_lines = output_lines[-200:]
        except Exception:
            pass
        process.wait(timeout=timeout)
        output = '\n'.join(output_lines)
        return process.returncode, output, ""
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return -1, "", "download timed out"
    except Exception as e:
        return -1, "", str(e)


def check_cli(name):
    code, out, _ = run_cmd(f"which {name} 2>/dev/null")
    return code == 0


def ensure_cli(package, cli_name):
    if check_cli(cli_name):
        return True
    print(f"  [{cli_name}] 未找到，尝试安装 {package}...")
    install_cmd = f"pip install {package} -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -q"
    code, _, _ = run_cmd(install_cmd, timeout=300)
    if code == 0 and check_cli(cli_name):
        print(f"  [{cli_name}] 安装成功")
        return True
    print(f"  [{cli_name}] 阿里云镜像安装失败，回退到默认 PyPI...")
    fallback_cmd = f"pip install {package} -q"
    code, _, _ = run_cmd(fallback_cmd, timeout=300)
    if code == 0 and check_cli(cli_name):
        print(f"  [{cli_name}] 安装成功（默认 PyPI）")
        return True
    print(f"  [{cli_name}] 安装失败")
    return False


def get_dir_size(path):
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total


def format_size(size_bytes):
    if size_bytes >= 1 << 30:
        return f"{size_bytes / (1 << 30):.2f} GB"
    elif size_bytes >= 1 << 20:
        return f"{size_bytes / (1 << 20):.2f} MB"
    else:
        return f"{size_bytes / (1 << 10):.2f} KB"


def download_from_modelscope(model_id, local_dir, token=None):
    if not ensure_cli("modelscope", "modelscope"):
        return False, "modelscope CLI not found and auto-install failed"

    os.makedirs(local_dir, exist_ok=True)

    env = {}
    if token:
        env["MODELSCOPE_API_TOKEN"] = token
    elif os.environ.get("MODELSCOPE_TOKEN"):
        env["MODELSCOPE_API_TOKEN"] = os.environ["MODELSCOPE_TOKEN"]

    cmd = f"modelscope download --model {model_id} --local_dir {local_dir}"
    print(f"[ModelScope] 下载: {model_id} → {local_dir}")
    code, stdout, stderr = run_cmd(cmd, env=env)

    if code == 0:
        return True, stdout
    return False, stderr or stdout


def download_from_huggingface(model_id, local_dir, token=None, mirror=None):
    # 优先使用新版 hf CLI，回退到 huggingface-cli，都没有则自动安装
    if check_cli("hf"):
        cli_cmd = "hf"
    elif check_cli("huggingface-cli"):
        cli_cmd = "huggingface-cli"
    elif ensure_cli("huggingface_hub", "hf"):
        cli_cmd = "hf"
    elif check_cli("huggingface-cli"):
        cli_cmd = "huggingface-cli"
    else:
        return False, "hf / huggingface-cli not found and auto-install failed"

    os.makedirs(local_dir, exist_ok=True)

    env = {}
    hf_endpoint = mirror or os.environ.get("HF_ENDPOINT", DEFAULT_HF_MIRROR)
    env["HF_ENDPOINT"] = hf_endpoint

    if token:
        env["HF_TOKEN"] = token
    elif os.environ.get("HF_TOKEN"):
        env["HF_TOKEN"] = os.environ["HF_TOKEN"]

    cmd = f"{cli_cmd} download {model_id} --local-dir {local_dir}"
    print(f"[HuggingFace] 下载: {model_id} → {local_dir} (endpoint: {hf_endpoint})")
    code, stdout, stderr = run_cmd(cmd, env=env)

    if code == 0:
        return True, stdout
    return False, stderr or stdout


def main():
    parser = argparse.ArgumentParser(description="模型下载工具（ModelScope / HuggingFace）")
    parser.add_argument("--model", required=True, help="模型 ID（如 Qwen/Qwen2.5-0.5B）")
    parser.add_argument("--source", choices=["modelscope", "huggingface", "auto"], default="auto",
                        help="下载源（默认 auto：优先 ModelScope，失败回退 HuggingFace）")
    parser.add_argument("--local-dir", required=True, help="下载目标路径")
    parser.add_argument("--token", default=None, help="认证 token（也可通过环境变量 MODELSCOPE_TOKEN / HF_TOKEN）")
    parser.add_argument("--mirror", default=None, help="HuggingFace 镜像地址（默认 https://hf-mirror.com）")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    result = {
        "model": args.model,
        "local_dir": args.local_dir,
        "source": None,
        "success": False,
        "size": "",
        "error": "",
    }

    if args.source == "modelscope":
        ok, msg = download_from_modelscope(args.model, args.local_dir, token=args.token)
        result["source"] = "modelscope"
        if ok:
            result["success"] = True
        else:
            result["error"] = msg

    elif args.source == "huggingface":
        ok, msg = download_from_huggingface(args.model, args.local_dir, token=args.token, mirror=args.mirror)
        result["source"] = "huggingface"
        if ok:
            result["success"] = True
        else:
            result["error"] = msg

    else:  # auto
        ok, msg = download_from_modelscope(args.model, args.local_dir, token=args.token)
        result["source"] = "modelscope"
        if ok:
            result["success"] = True
        else:
            print(f"[ModelScope] 失败: {msg}")
            print("[auto] 回退到 HuggingFace...")
            ok, msg = download_from_huggingface(args.model, args.local_dir, token=args.token, mirror=args.mirror)
            result["source"] = "huggingface"
            if ok:
                result["success"] = True
            else:
                result["error"] = msg

    if result["success"] and os.path.isdir(args.local_dir):
        size = get_dir_size(args.local_dir)
        result["size"] = format_size(size)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["success"]:
            print(f"✓ 下载完成: {args.model} → {args.local_dir} ({result['size']}) [来源: {result['source']}]")
        else:
            print(f"✗ 下载失败: {args.model} — {result['error']}")
            sys.exit(1)


if __name__ == "__main__":
    main()
