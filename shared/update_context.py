#!/usr/bin/env python3
"""
update_context.py — context.yaml 结构化更新工具

支持嵌套字段设置、数组追加、workflow_ledger 步骤状态更新。

用法:
  python3 update_context.py --set container.name=xxx --set gpu.count=8
  python3 update_context.py --json-set 'service={"port":8001}'
  python3 update_context.py --ledger-update 01_container_preparation --ledger-status success --ledger-notes "容器就绪"
  python3 update_context.py --append key_dependencies='{"name":"vllm","version":"0.4.0"}'
"""

import argparse
import json
import os
import sys
import tempfile
import datetime

try:
    import yaml
except ImportError:
    print("[ERROR] pyyaml 未安装，请执行: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

DEFAULT_CONTEXT = "/Offline_inference_workspace/shared/context.yaml"


def parse_value(val_str):
    if val_str.lower() == "true":
        return True
    if val_str.lower() == "false":
        return False
    if val_str.lower() in ("null", "none", "~"):
        return None
    try:
        return int(val_str)
    except ValueError:
        pass
    try:
        return float(val_str)
    except ValueError:
        pass
    return val_str


def set_nested(d, key_path, value):
    keys = key_path.split(".")
    for k in keys[:-1]:
        if k not in d or not isinstance(d[k], dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


def append_nested(d, key_path, value):
    keys = key_path.split(".")
    for k in keys[:-1]:
        if k not in d or not isinstance(d[k], dict):
            d[k] = {}
        d = d[k]
    last = keys[-1]
    if last not in d or not isinstance(d[last], list):
        d[last] = []
    d[last].append(value)


def update_ledger(ctx, step_id, status, notes=None, fail_reason=None):
    ledger = ctx.get("workflow_ledger", {}).get("steps", [])
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    for entry in ledger:
        if isinstance(entry, dict) and entry.get("step") == step_id:
            if status in ("running", "in_progress") and not entry.get("started_at"):
                entry["started_at"] = now
            if status in ("success", "failed", "skipped"):
                entry["finished_at"] = now
            entry["status"] = status
            if notes:
                entry["notes"] = notes
            if fail_reason:
                entry["fail_reason"] = fail_reason
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="context.yaml 更新工具")
    parser.add_argument("--context", default=DEFAULT_CONTEXT, help="context.yaml 路径")
    parser.add_argument("--set", action="append", dest="sets", help="key.path=value")
    parser.add_argument("--json-set", action="append", dest="json_sets", help="key={json}")
    parser.add_argument("--append", action="append", dest="appends", help="key.path=value")
    parser.add_argument("--ledger-update", dest="ledger_step", help="步骤 ID")
    parser.add_argument("--ledger-status", dest="ledger_status", help="状态")
    parser.add_argument("--ledger-notes", dest="ledger_notes", help="备注")
    parser.add_argument("--ledger-fail-reason", dest="ledger_fail_reason", help="失败原因")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    try:
        with open(args.context) as f:
            ctx = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"[ERROR] 文件不存在: {args.context}", file=sys.stderr)
        sys.exit(1)

    changes = []

    if args.sets:
        for item in args.sets:
            if "=" not in item:
                print(f"[ERROR] --set 格式错误: {item}", file=sys.stderr)
                sys.exit(1)
            key, val = item.split("=", 1)
            parsed = parse_value(val)
            set_nested(ctx, key, parsed)
            changes.append({"op": "set", "key": key, "value": parsed})

    if args.json_sets:
        for item in args.json_sets:
            if "=" not in item:
                print(f"[ERROR] --json-set 格式错误: {item}", file=sys.stderr)
                sys.exit(1)
            key, val = item.split("=", 1)
            try:
                parsed = json.loads(val)
            except json.JSONDecodeError as e:
                print(f"[ERROR] JSON 解析失败: {e}", file=sys.stderr)
                sys.exit(1)
            set_nested(ctx, key, parsed)
            changes.append({"op": "json_set", "key": key})

    if args.appends:
        for item in args.appends:
            if "=" not in item:
                print(f"[ERROR] --append 格式错误: {item}", file=sys.stderr)
                sys.exit(1)
            key, val = item.split("=", 1)
            try:
                parsed = json.loads(val)
            except json.JSONDecodeError:
                parsed = parse_value(val)
            append_nested(ctx, key, parsed)
            changes.append({"op": "append", "key": key})

    if args.ledger_step:
        if not args.ledger_status:
            print("[ERROR] --ledger-update 需要 --ledger-status", file=sys.stderr)
            sys.exit(1)
        found = update_ledger(ctx, args.ledger_step, args.ledger_status,
                              notes=args.ledger_notes,
                              fail_reason=args.ledger_fail_reason)
        if found:
            changes.append({"op": "ledger", "step": args.ledger_step, "status": args.ledger_status})
        else:
            print(f"[WARN] workflow_ledger 中未找到步骤: {args.ledger_step}", file=sys.stderr)

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    set_nested(ctx, "metadata.updated_at", now)

    ctx_dir = os.path.dirname(os.path.abspath(args.context))
    try:
        fd = tempfile.NamedTemporaryFile(
            mode='w', dir=ctx_dir, suffix='.tmp', delete=False
        )
        yaml.dump(ctx, fd, default_flow_style=False, allow_unicode=True, sort_keys=False)
        fd.flush()
        os.fsync(fd.fileno())
        fd.close()
        os.replace(fd.name, args.context)
    except Exception:
        try:
            os.unlink(fd.name)
        except OSError:
            pass
        raise

    if args.json:
        print(json.dumps({"success": True, "changes": changes, "updated_at": now}, ensure_ascii=False))
    else:
        print(f"✓ context.yaml 已更新 ({len(changes)} 项变更)")


if __name__ == "__main__":
    main()
