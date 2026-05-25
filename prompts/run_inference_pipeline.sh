#!/bin/bash
# ============================================================================
# run_inference_pipeline.sh — 离线推理验证全自动流水线
# ============================================================================
#
# 用法:
#   bash prompts/run_inference_pipeline.sh <镜像地址或容器名> <模型名> \
#       <HARBOR_USER> <HARBOR_PASSWORD> [--model-path <路径>] [--verbose]
#
# 示例:
#   bash prompts/run_inference_pipeline.sh harbor.baai.ac.cn/flagrelease/qwen3:latest Qwen3-8B harbor_user harbor_pass
#   bash prompts/run_inference_pipeline.sh my_container Qwen3-8B harbor_user harbor_pass --verbose
#   bash prompts/run_inference_pipeline.sh harbor.baai.ac.cn/flagrelease/qwen3:latest Qwen3-8B harbor_user harbor_pass --model-path /data/models/Qwen3-8B
#
# 流程:
#   段1: 容器准备 + 环境探索 (步骤 1→2)
#   段2: 原生推理验证 + 发布 (步骤 3→4)
#   段3: FlagGems 安装 + 推理验证 + 发布 (步骤 5→6→7)
#
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ========== Docker 前置检查 ==========
if ! docker ps &>/dev/null; then
    echo "错误: Docker daemon 未运行或无权限"
    exit 1
fi

# ========== Python 依赖检查 ==========
if ! command -v python3 &>/dev/null; then
    echo "错误: python3 未安装"
    exit 1
fi

if ! python3 -c "import yaml" 2>/dev/null; then
    echo "[pre-flight] 安装 pyyaml..."
    pip3 install pyyaml -q 2>/dev/null || pip3 install pyyaml -q -i https://mirrors.aliyun.com/pypi/simple/ 2>/dev/null || \
        { echo "错误: pyyaml 安装失败"; exit 1; }
fi

# ========== 参数解析 ==========
if [ $# -lt 4 ]; then
    echo "用法: $0 <镜像地址或容器名> <模型名> <HARBOR_USER> <HARBOR_PASSWORD> [--model-path <路径>] [--verbose]"
    echo ""
    echo "示例:"
    echo "  $0 harbor.baai.ac.cn/flagrelease/qwen3:latest Qwen3-8B harbor_user harbor_pass"
    echo "  $0 my_container Qwen3-8B harbor_user harbor_pass --verbose"
    exit 1
fi

TARGET="$1"
MODEL="$2"
export HARBOR_USER="$3"
export HARBOR_PASSWORD="$4"
shift 4

IMAGE_MODE=false
MODEL_PATH=""
VERBOSE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --verbose) VERBOSE="--verbose"; shift ;;
        --model-path)
            MODEL_PATH="$2"; shift 2 ;;
        *)
            echo "警告: 未知参数 '$1'，已忽略"; shift ;;
    esac
done

# ========== 自动识别模式 ==========
if [[ "$TARGET" == *":"* ]] || [[ "$TARGET" == *"/"* ]]; then
    IMAGE_MODE=true
    IMAGE="$TARGET"
elif docker inspect --type=container "$TARGET" &>/dev/null; then
    IMAGE_MODE=false
    CONTAINER="$TARGET"
else
    IMAGE_MODE=true
    IMAGE="$TARGET"
fi

# ========== 模型路径搜索（镜像模式） ==========
MODEL_FOUND_ON_HOST=false
DL_ERROR=""
if $IMAGE_MODE && [ -z "$MODEL_PATH" ]; then
    echo "[pre-flight] 搜索宿主机模型路径: ${MODEL} ..."
    SEARCH_JSON=$(python3 "${PROJECT_ROOT}/skills/flagos-container-preparation/tools/check_model_local.py" \
        --model "${MODEL}" --output-json 2>/dev/null) || SEARCH_JSON=""

    MODEL_PATH=$(echo "$SEARCH_JSON" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('best_match') or '')
except:
    print('')
" 2>/dev/null) || MODEL_PATH=""

    if [ -n "$MODEL_PATH" ]; then
        echo "  ✓ 找到: ${MODEL_PATH}"
        MODEL_FOUND_ON_HOST=true
    else
        MODEL_SHORT=$(echo "${MODEL}" | sed 's|.*/||')
        MODEL_PATH="/data/models/${MODEL_SHORT}"
        echo "  宿主机未找到模型，尝试下载: ${MODEL} → ${MODEL_PATH} ..."
        DL_RESULT=$(python3 "${PROJECT_ROOT}/skills/flagos-container-preparation/tools/download_model.py" \
            --model "${MODEL}" --source auto --local-dir "${MODEL_PATH}" --json 2>&1) || true
        DL_SUCCESS=$(echo "$DL_RESULT" | python3 -c "
import sys, json
text = sys.stdin.read()
try:
    last_brace = text.rfind('}')
    if last_brace == -1:
        print('False')
    else:
        start = text.rfind('{', 0, last_brace)
        while start >= 0:
            try:
                data = json.loads(text[start:last_brace+1])
                print(data.get('success', False))
                break
            except json.JSONDecodeError:
                start = text.rfind('{', 0, start)
        else:
            print('False')
except Exception:
    print('False')
" 2>/dev/null) || DL_SUCCESS="False"
        if [ "$DL_SUCCESS" = "True" ]; then
            echo "  ✓ 模型下载完成"
            MODEL_FOUND_ON_HOST=true
        else
            DL_ERROR="宿主机下载失败: $(echo "$DL_RESULT" | grep -v '^{' | grep -v '^\s' | tail -3)"
            echo "  ⚠ ${DL_ERROR}，将由 agent 兜底修复"
        fi
    fi
elif [ -n "$MODEL_PATH" ]; then
    if [ -d "$MODEL_PATH" ]; then
        MODEL_FOUND_ON_HOST=true
        echo "[pre-flight] 使用指定模型路径: ${MODEL_PATH}"
    else
        echo "错误: 指定的模型路径不存在: ${MODEL_PATH}"
        exit 1
    fi
else
    # 已有容器模式且未指定模型路径：搜索宿主机
    echo "[pre-flight] 已有容器模式，搜索宿主机模型路径: ${MODEL} ..."
    SEARCH_JSON=$(python3 "${PROJECT_ROOT}/skills/flagos-container-preparation/tools/check_model_local.py" \
        --model "${MODEL}" --output-json 2>/dev/null) || SEARCH_JSON=""
    MODEL_PATH=$(echo "$SEARCH_JSON" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('best_match') or '')
except:
    print('')
" 2>/dev/null) || MODEL_PATH=""
    if [ -n "$MODEL_PATH" ]; then
        echo "  ✓ 找到: ${MODEL_PATH}"
        MODEL_FOUND_ON_HOST=true
    else
        echo "  宿主机未找到模型，在容器内下载..."
        bash "${PROJECT_ROOT}/skills/flagos-container-preparation/tools/setup_workspace.sh" \
            "${CONTAINER}" "${MODEL}" || { echo "  ⚠ 工作空间部署失败"; }
        # 验证下载脚本已部署到容器
        if ! docker exec "${CONTAINER}" test -f /Offline_inference_workspace/scripts/download_model.py; then
            echo "  ⚠ download_model.py 未部署到容器，尝试手动复制..."
            docker cp "${PROJECT_ROOT}/skills/flagos-container-preparation/tools/download_model.py" \
                "${CONTAINER}:/Offline_inference_workspace/scripts/download_model.py" || \
                { echo "  ✗ 复制 download_model.py 失败"; DL_ERROR="download_model.py 部署失败"; }
        fi
        MODEL_SHORT=$(echo "${MODEL}" | sed 's|.*/||')
        MODEL_PATH="/data/models/${MODEL_SHORT}"
        if [ -z "$DL_ERROR" ]; then
            ESCAPED_MODEL=$(printf '%s' "${MODEL}" | sed "s/'/'\\\\''/g")
            ESCAPED_PATH=$(printf '%s' "${MODEL_PATH}" | sed "s/'/'\\\\''/g")
            DL_RESULT=$(docker exec "${CONTAINER}" bash -c "python3 /Offline_inference_workspace/scripts/download_model.py \
                --model '${ESCAPED_MODEL}' --source auto --local-dir '${ESCAPED_PATH}' --json" 2>&1) || true
            DL_SUCCESS=$(echo "$DL_RESULT" | python3 -c "
import sys, json
text = sys.stdin.read()
try:
    last_brace = text.rfind('}')
    if last_brace == -1:
        print('False')
    else:
        start = text.rfind('{', 0, last_brace)
        while start >= 0:
            try:
                data = json.loads(text[start:last_brace+1])
                print(data.get('success', False))
                break
            except json.JSONDecodeError:
                start = text.rfind('{', 0, start)
        else:
            print('False')
except Exception:
    print('False')
" 2>/dev/null) || DL_SUCCESS="False"
            if [ "$DL_SUCCESS" = "True" ]; then
                echo "  ✓ 容器内模型下载完成: ${MODEL_PATH}"
            else
                DL_ERROR="容器内下载失败: $(echo "$DL_RESULT" | grep -v '^{' | grep -v '^\s' | tail -3)"
                echo "  ⚠ ${DL_ERROR}，将由 agent 兜底修复"
            fi
        else
            echo "  ⚠ ${DL_ERROR}，将由 agent 兜底修复"
        fi
    fi
fi

# ========== Banner ==========
echo "============================================================"
echo "  离线推理验证自动化流水线"
echo "============================================================"
if $IMAGE_MODE; then
    echo "  目标: ${IMAGE} (镜像)"
else
    echo "  目标: ${CONTAINER} (容器)"
fi
echo "  模型: ${MODEL}"
if [ -n "$MODEL_PATH" ]; then
    echo "  模型路径: ${MODEL_PATH}"
fi
echo "  流程: 原生推理验证 → 发布 → FlagGems 推理验证 → 发布"
echo "============================================================"
echo ""

# ========== 日志目录 ==========
MODEL_SAFE="${MODEL//\//_}"
MODEL_SHORT="${MODEL##*/}"
WORKSPACE_BASE="/data/Offline_inference_workspace/${MODEL_SAFE}"
LOG_DIR="${WORKSPACE_BASE}/logs"
mkdir -p "${WORKSPACE_BASE}" "${LOG_DIR}"
PIPELINE_LOG="${LOG_DIR}/pipeline.log"
FULL_LOG="${LOG_DIR}/full.log"

# 清理因模型名含 / 导致的错误嵌套目录
if echo "${MODEL}" | grep -q '/'; then
    MODEL_ORG="${MODEL%%/*}"
    WRONG_DIR="/data/Offline_inference_workspace/${MODEL_ORG}"
    if [ -d "${WRONG_DIR}" ] && [ "${WRONG_DIR}" != "${WORKSPACE_BASE}" ]; then
        if [ -z "$(find "${WRONG_DIR}" -type f 2>/dev/null)" ]; then
            echo "[cleanup] 清理错误目录: ${WRONG_DIR}"
            rm -rf "${WRONG_DIR}"
        fi
    fi
fi

# ========== 辅助函数 ==========
read_context() {
    local model="$1"
    local model_safe="${model//\//_}"
    local ctx_file="/data/Offline_inference_workspace/${model_safe}/config/context_snapshot.yaml"
    python3 -c "
import yaml
with open('${ctx_file}') as f:
    ctx = yaml.safe_load(f)
ctr = ctx.get('container',{}).get('name','')
status = ctx.get('container',{}).get('status','')
print(f'{ctr}|{status}')
"
}

sync_context() {
    local container="$1"
    local model="$2"
    # 清理模型名中的路径分隔符，避免目录结构异常
    local model_safe="${model//\//_}"
    local ctx_file="/data/Offline_inference_workspace/${model_safe}/config/context_snapshot.yaml"
    mkdir -p "$(dirname "${ctx_file}")"
    local mount_mode
    mount_mode=$(docker exec "${container}" cat /Offline_inference_workspace/.mount_mode 2>/dev/null || echo "internal")
    local synced=false
    # mounted 模式优先尝试直接复制（更快），失败则回退 docker cp
    if [ "$mount_mode" = "mounted" ] || [ "$mount_mode" = "symlink" ]; then
        local host_ctx="/data/Offline_inference_workspace/${model_safe}/shared/context.yaml"
        if [ -f "${host_ctx}" ]; then
            cp "${host_ctx}" "${ctx_file}.tmp" && mv "${ctx_file}.tmp" "${ctx_file}" && synced=true
        fi
    fi
    # 主路径：docker cp（可靠），使用原子 mv 避免读取到不完整文件
    if [ "$synced" = "false" ]; then
        docker cp "${container}:/Offline_inference_workspace/shared/context.yaml" "${ctx_file}.tmp" && \
            mv "${ctx_file}.tmp" "${ctx_file}" || \
            { rm -f "${ctx_file}.tmp"; echo "⚠ sync_context 失败: 无法从容器 ${container} 同步 context.yaml"; return 1; }
    fi
}

check_native_inference_ok() {
    local model="$1"
    local container="$2"
    local model_safe="${model//\//_}"
    local ctx_file="/data/Offline_inference_workspace/${model_safe}/config/context_snapshot.yaml"

    # 硬性检查：验证关键产出文件是否存在于容器中
    local has_script=false
    local has_readme=false
    docker exec "${container}" test -f /root/run_inference.py && has_script=true
    docker exec "${container}" test -f /root/README.md && has_readme=true

    # 读取 workflow 字段
    local workflow_status=$(python3 -c "
import yaml
try:
    with open('${ctx_file}') as f:
        ctx = yaml.safe_load(f)
    ok = ctx.get('workflow',{}).get('native_inference_ok', False)
    terminated = ctx.get('workflow',{}).get('terminated', False)
    if terminated:
        print('TERMINATED')
    elif ok:
        print('OK')
    else:
        print('FAILED')
except:
    print('ERROR')
" 2>/dev/null)

    # 综合判断：workflow 字段 + 硬性检查
    if [ "$workflow_status" = "TERMINATED" ]; then
        echo "TERMINATED"
    elif [ "$workflow_status" = "OK" ]; then
        # workflow 显示成功，验证关键文件
        if [ "$has_script" = "true" ] && [ "$has_readme" = "true" ]; then
            echo "OK"
        else
            echo "⚠ workflow.native_inference_ok=true 但关键文件缺失 (script=$has_script, readme=$has_readme)" >&2
            echo "FAILED"
        fi
    elif [ "$workflow_status" = "FAILED" ]; then
        # workflow 显示失败，但如果关键文件存在，可能是状态未更新
        if [ "$has_script" = "true" ] && [ "$has_readme" = "true" ]; then
            echo "⚠ workflow.native_inference_ok=false 但关键文件已存在，可能状态未更新，视为成功" >&2
            echo "OK"
        else
            echo "FAILED"
        fi
    else
        echo "ERROR"
    fi
}

# ========== 全流程计时 ==========
PIPELINE_START_TS=$(date +%s)

# ========== 段1: 容器准备 + 环境探索 (步骤 1→2) ==========
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  段1/3  容器准备 + 环境探索  (步骤 1→2)                     ║"
echo "╚══════════════════════════════════════════════════════════════╝"
SEG1_START_TS=$(date +%s)

# 构造段1 Prompt
if $IMAGE_MODE; then
    STEP1_DESC="镜像: ${IMAGE}，模型名: ${MODEL}，模型路径: ${MODEL_PATH}，宿主机工作目录: /data/Offline_inference_workspace/${MODEL_SAFE}"
else
    STEP1_DESC="容器: ${CONTAINER}，模型名: ${MODEL}，模型路径: ${MODEL_PATH}，宿主机工作目录: /data/Offline_inference_workspace/${MODEL_SAFE}"
fi

PREFLIGHT_STATUS=""
if [ -n "$DL_ERROR" ]; then
    PREFLIGHT_STATUS="
[pre-flight 异常] 模型自动下载失败，需要你诊断修复：
错误: ${DL_ERROR}
模型: ${MODEL}
目标路径: ${MODEL_PATH}
请参考 skills/flagos-container-preparation/SKILL.md 中的故障排查流程。"
fi

PROMPT_SEG1="${STEP1_DESC}

请执行离线推理验证流水线的段1（步骤1-2）：

**步骤1 — 容器准备**
阅读 skills/flagos-container-preparation/SKILL.md，按其流程执行。
$(if $IMAGE_MODE; then echo "入口类型: 镜像模式，需要 docker run 创建容器"; else echo "入口类型: 已有容器模式"; fi)
docker run 时的工作空间挂载必须原样使用（已计算好，禁止修改此路径）: -v /data/Offline_inference_workspace/${MODEL_SAFE}:/Offline_inference_workspace
注意: 上面的路径中模型名的 / 已替换为 _，这是正确的，不要改回含 / 的形式。
${PREFLIGHT_STATUS}

**步骤2 — 环境探索**
阅读 skills/flagos-env-exploration/SKILL.md，按其流程执行。
探测容器内推理框架、模型格式、关键依赖。

**规则**：
- 每步开始前输出 [步骤X] <名称> — 开始
- 每步完成后输出 [步骤X] <名称> — 完成
- 通过 docker exec 操作容器内文件
- 使用 update_context.py 更新 context.yaml
- 每步完成后同步 context_snapshot.yaml
- Harbor 凭证已通过环境变量 HARBOR_USER / HARBOR_PASSWORD 提供（如需 docker login）
"

claude -p "${PROMPT_SEG1}" \
    --permission-mode auto \
    --max-turns 80 \
    --output-format stream-json --verbose \
    2>&1 | python3 "${PROJECT_ROOT}/tools/extract_stream_text.py" \
        --jsonl-out "${LOG_DIR}/seg1_stream.jsonl" | tee -a "${FULL_LOG}" || true
CLAUDE_EXIT=${PIPESTATUS[0]:-0}
if [ "$CLAUDE_EXIT" -ne 0 ]; then
    echo "⚠ 段1 claude 退出码: ${CLAUDE_EXIT}，检查产出是否完整..."
fi

# 段1 完成检查
SEG1_END_TS=$(date +%s)
SEG1_ELAPSED=$(( SEG1_END_TS - SEG1_START_TS ))
SEG1_MIN=$(( SEG1_ELAPSED / 60 ))
SEG1_SEC=$(( SEG1_ELAPSED % 60 ))
echo ""
echo "┌──────────────────────────────────────────────────────────────┐"
echo "│  段1 完成 — 耗时 ${SEG1_MIN}m ${SEG1_SEC}s                                     │"
echo "└──────────────────────────────────────────────────────────────┘"

# 段1 后清理：agent 可能误创建含 / 的嵌套目录
if echo "${MODEL}" | grep -q '/'; then
    MODEL_ORG="${MODEL%%/*}"
    WRONG_DIR="/data/Offline_inference_workspace/${MODEL_ORG}"
    if [ -d "${WRONG_DIR}" ] && [ "${WRONG_DIR}" != "${WORKSPACE_BASE}" ]; then
        if [ -z "$(find "${WRONG_DIR}" -type f 2>/dev/null)" ]; then
            echo "[cleanup] 段1后清理错误目录: ${WRONG_DIR}"
            rm -rf "${WRONG_DIR}"
        fi
    fi
fi

# 从 context 获取容器名
if $IMAGE_MODE; then
    # 先通过容器命名规则获取容器名
    CONTAINER=$(docker ps --filter "name=${MODEL_SHORT}_offline_infer" --format '{{.Names}}' | head -1)
    if [ -z "$CONTAINER" ]; then
        echo "错误: 段1未产出运行中的容器 (期望: ${MODEL_SHORT}_offline_infer)，终止"
        exit 1
    fi
    # 同步 context 到 config/context_snapshot.yaml
    sync_context "${CONTAINER}" "${MODEL}"
    CTX_FILE="/data/Offline_inference_workspace/${MODEL_SAFE}/config/context_snapshot.yaml"
    if [ ! -f "${CTX_FILE}" ]; then
        echo "错误: sync_context 后仍找不到 context_snapshot.yaml，终止"
        exit 1
    fi
fi

# 验证容器运行中
if ! docker inspect --type=container "${CONTAINER}" &>/dev/null; then
    echo "错误: 容器 ${CONTAINER} 不存在，终止"
    exit 1
fi

# 同步 context
sync_context "${CONTAINER}" "${MODEL}"
echo "  ✓ 容器: ${CONTAINER}"

# ========== 段2: 原生推理验证 + 发布 (步骤 3→4) ==========
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  段2/3  原生推理验证 + 发布  (步骤 3→4)                     ║"
echo "╚══════════════════════════════════════════════════════════════╝"
SEG2_START_TS=$(date +%s)

PROMPT_SEG2="容器: ${CONTAINER}，模型名: ${MODEL}

请执行离线推理验证流水线的段2（步骤3-4）：

**步骤3 — 原生推理验证**
阅读 skills/flagos-inference-verify/SKILL.md，按其流程执行（native 模式）。
先读取容器内 /Offline_inference_workspace/shared/context.yaml 获取环境探索结果。
核心任务：
1. 查询模型官方推荐测试数据，构造测试输入写入 /root/test_input.txt
2. 编写推理脚本 /root/run_inference.py（argparse 参数化），脚本最顶部（所有其他 import 之前）必须包含：
   try:
       import flag_gems
       flag_gems.enable(record=True, once=True, unused=[], path="/root/gems.txt")
   except ImportError:
       pass
   这是唯一允许的 FlagGems 启用方式，禁止使用环境变量（如 FLAG_GEMS_RECORD_LOG）替代。
3. 执行推理验证，输出保存到 /root/
4. 验证通过后编写 /root/README.md（参照 /mnt/data/test/README.md 格式，含真实输出结果）
所有产出统一放在容器 /root/ 目录下。
如果推理失败：排查问题 → 尝试修复（最多3次）→ 仍失败则记录原因并终止。

**步骤4 — 原生镜像发布**（仅步骤3成功时执行）
阅读 skills/flagos-release/SKILL.md，按其流程执行。
将当前容器 commit 为镜像并上传（/root/ 下的产出文件随镜像打包）。

**规则**：
- 每步开始前输出 [步骤X] <名称> — 开始
- 每步完成后输出 [步骤X] <名称> — 完成
- 使用 update_context.py 更新 context.yaml
- 步骤3失败时设置 workflow.terminated=true 并终止，不执行步骤4
"

claude -p "${PROMPT_SEG2}" \
    --permission-mode auto \
    --max-turns 100 \
    --output-format stream-json --verbose \
    2>&1 | python3 "${PROJECT_ROOT}/tools/extract_stream_text.py" \
        --jsonl-out "${LOG_DIR}/seg2_stream.jsonl" | tee -a "${FULL_LOG}" || true
CLAUDE_EXIT=${PIPESTATUS[0]:-0}
if [ "$CLAUDE_EXIT" -ne 0 ]; then
    echo "⚠ 段2 claude 退出码: ${CLAUDE_EXIT}，检查产出是否完整..."
fi

SEG2_END_TS=$(date +%s)
SEG2_ELAPSED=$(( SEG2_END_TS - SEG2_START_TS ))
SEG2_MIN=$(( SEG2_ELAPSED / 60 ))
SEG2_SEC=$(( SEG2_ELAPSED % 60 ))
echo ""
echo "┌──────────────────────────────────────────────────────────────┐"
echo "│  段2 完成 — 耗时 ${SEG2_MIN}m ${SEG2_SEC}s                                     │"
echo "└──────────────────────────────────────────────────────────────┘"

# 段2→段3 检查：原生推理是否成功
sync_context "${CONTAINER}" "${MODEL}"
NATIVE_STATUS=$(check_native_inference_ok "${MODEL}" "${CONTAINER}")

if [ "${NATIVE_STATUS}" = "TERMINATED" ]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║  ✗ 原生推理验证失败且无法修复，流程终止                       ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo "详情见: /data/Offline_inference_workspace/${MODEL_SAFE}/config/context_snapshot.yaml"
    exit 1
elif [ "${NATIVE_STATUS}" != "OK" ]; then
    echo ""
    echo "⚠ 原生推理状态异常 (${NATIVE_STATUS})，终止流程"
    exit 1
fi

echo "  ✓ 原生推理验证通过，继续 FlagGems 阶段"

# ========== 段3: FlagGems 安装 + 推理验证 + 发布 (步骤 5→6→7) ==========
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  段3/3  FlagGems 推理验证 + 发布  (步骤 5→6→7)              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
SEG3_START_TS=$(date +%s)

PROMPT_SEG3="容器: ${CONTAINER}，模型名: ${MODEL}

请执行离线推理验证流水线的段3（步骤5-6-7）：

**步骤5 — FlagGems/FlagTree 安装**
阅读 skills/flagos-component-install/SKILL.md，按其流程执行。
先安装 FlagTree，再安装 FlagGems。

**步骤6 — FlagGems 推理验证**
阅读 skills/flagos-inference-verify/SKILL.md，按其流程执行（flaggems 模式）。
直接运行 /root/run_inference.py（不加任何环境变量），脚本顶部的 flag_gems.enable(record=True, once=True, unused=[], path="/root/gems.txt") 会自动激活 FlagGems。
禁止通过环境变量方式启用 FlagGems，必须依赖脚本内的 flag_gems.enable() 调用。
验证输出正确性（包括与原生结果的数值对比，max abs diff < 0.01）。
验证完成后必须：
1. 确认 /root/gems.txt 已生成（证明 flag_gems.enable 正常工作）
2. cat /root/gems.txt 打印被替换的算子列表
3. 更新 /root/README.md，追加 FlagGems 验证结果和完整算子列表
注意：数值对比是步骤6验证的一部分，不是独立步骤。

**步骤7 — FlagGems 镜像发布**（仅步骤6成功时执行）
阅读 skills/flagos-release/SKILL.md，按其流程执行。
具体动作：执行 python3 skills/flagos-release/tools/main.py --from-context，将当前容器 commit 为新镜像并 push 到 Harbor。
必须产出：Harbor 镜像 URL（格式 harbor.baai.ac.cn/...）。
如果步骤6失败，发布时在镜像 tag 中追加 '-flaggems-failed' 后缀，并在 README 中注明 FlagGems 验证未通过。

**规则**：
- 每步开始前输出 [步骤X] <名称> — 开始
- 每步完成后输出 [步骤X] <名称> — 完成
- 使用 update_context.py 更新 context.yaml
- 步骤6失败时记录原因，仍尝试步骤7（发布当前状态镜像，但需标注 FlagGems 验证状态）
- 步骤7是镜像发布（docker commit + push），不是结果对比。结果对比属于步骤6的验证环节。
- 步骤7完成的标志：输出了 Harbor 镜像 URL
- 全部完成后设置 workflow.all_done=true
"

claude -p "${PROMPT_SEG3}" \
    --permission-mode auto \
    --max-turns 120 \
    --output-format stream-json --verbose \
    2>&1 | python3 "${PROJECT_ROOT}/tools/extract_stream_text.py" \
        --jsonl-out "${LOG_DIR}/seg3_stream.jsonl" | tee -a "${FULL_LOG}" || true
CLAUDE_EXIT=${PIPESTATUS[0]:-0}
if [ "$CLAUDE_EXIT" -ne 0 ]; then
    echo "⚠ 段3 claude 退出码: ${CLAUDE_EXIT}，检查产出是否完整..."
fi

SEG3_END_TS=$(date +%s)
SEG3_ELAPSED=$(( SEG3_END_TS - SEG3_START_TS ))
SEG3_MIN=$(( SEG3_ELAPSED / 60 ))
SEG3_SEC=$(( SEG3_ELAPSED % 60 ))
echo ""
echo "┌──────────────────────────────────────────────────────────────┐"
echo "│  段3 完成 — 耗时 ${SEG3_MIN}m ${SEG3_SEC}s                                     │"
echo "└──────────────────────────────────────────────────────────────┘"

# ========== 汇总 ==========
sync_context "${CONTAINER}" "${MODEL}"
PIPELINE_END_TS=$(date +%s)
PIPELINE_ELAPSED=$(( PIPELINE_END_TS - PIPELINE_START_TS ))
PIPELINE_MIN=$(( PIPELINE_ELAPSED / 60 ))
PIPELINE_SEC=$(( PIPELINE_ELAPSED % 60 ))

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  全流程完成 — 耗时汇总                                       ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf "║  段1  容器准备+环境探索          %6s                     ║\n" "${SEG1_MIN}m${SEG1_SEC}s"
printf "║  段2  原生推理验证+发布          %6s                     ║\n" "${SEG2_MIN}m${SEG2_SEC}s"
printf "║  段3  FlagGems推理验证+发布      %6s                     ║\n" "${SEG3_MIN}m${SEG3_SEC}s"
echo "║──────────────────────────────────────────────────────────────║"
printf "║  总计                            %6s                     ║\n" "${PIPELINE_MIN}m${PIPELINE_SEC}s"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "日志: ${FULL_LOG}"
echo "状态: /data/Offline_inference_workspace/${MODEL_SAFE}/config/context_snapshot.yaml"
