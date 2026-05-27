#!/bin/bash
# setup_workspace.sh — 容器工作空间部署（离线推理精简版）
#
# 用法: bash setup_workspace.sh <容器名> <模型名>
#
# 部署内容:
#   - /Offline_inference_workspace/shared/context.yaml (从模板初始化)
#   - /Offline_inference_workspace/scripts/ (工具脚本)
#   - /Offline_inference_workspace/results/ (输出目录)
#   - /Offline_inference_workspace/logs/ (日志目录)

set -euo pipefail

if [ $# -lt 2 ]; then
    echo "用法: $0 <容器名> <模型名>"
    exit 1
fi

CONTAINER="$1"
MODEL="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

echo "[setup_workspace] 容器: ${CONTAINER}, 模型: ${MODEL}"

# 清理因模型名含 / 导致的错误嵌套目录
# 例如模型 thaonguyen217/farm_molecular_representation 可能误创建 /data/Offline_inference_workspace/thaonguyen217/
if echo "${MODEL}" | grep -q '/'; then
    MODEL_ORG="${MODEL%%/*}"
    WRONG_DIR="/data/Offline_inference_workspace/${MODEL_ORG}"
    if [ -d "${WRONG_DIR}" ]; then
        # 只清理空目录树（递归检查是否为空）
        if [ -z "$(find "${WRONG_DIR}" -type f 2>/dev/null)" ]; then
            echo "  ⚠ 检测到错误目录 ${WRONG_DIR}（模型名含 / 导致），清理中..."
            rm -rf "${WRONG_DIR}"
        fi
    fi
fi

# 验证容器存在且运行中
if ! docker inspect --type=container "${CONTAINER}" &>/dev/null; then
    echo "错误: 容器 ${CONTAINER} 不存在"
    exit 1
fi

CONTAINER_STATUS=$(docker inspect -f '{{.State.Status}}' "${CONTAINER}")
if [ "${CONTAINER_STATUS}" != "running" ]; then
    echo "容器未运行，尝试启动..."
    docker start "${CONTAINER}"
    sleep 2
fi

# 创建目录结构
docker exec "${CONTAINER}" bash -c "
    mkdir -p /Offline_inference_workspace/shared \
             /Offline_inference_workspace/scripts \
             /Offline_inference_workspace/results \
             /Offline_inference_workspace/logs \
             /Offline_inference_workspace/test_data
"

# 部署 context.yaml（仅当不存在时初始化）
HAS_CTX=$(docker exec "${CONTAINER}" bash -c "[ -f /Offline_inference_workspace/shared/context.yaml ] && echo yes || echo no")
if [ "${HAS_CTX}" = "no" ]; then
    docker cp "${PROJECT_ROOT}/shared/context.template.yaml" \
        "${CONTAINER}:/Offline_inference_workspace/shared/context.yaml"
    echo "  ✓ context.yaml 已初始化"
else
    echo "  ⓘ context.yaml 已存在，跳过初始化"
fi

# 部署 update_context.py
docker cp "${PROJECT_ROOT}/shared/update_context.py" \
    "${CONTAINER}:/Offline_inference_workspace/scripts/update_context.py"

# 部署组件安装工具
if [ -d "${PROJECT_ROOT}/skills/flagos-component-install/tools" ]; then
    docker cp "${PROJECT_ROOT}/skills/flagos-component-install/tools/install_component.py" \
        "${CONTAINER}:/Offline_inference_workspace/scripts/install_component.py" 2>/dev/null || true
    docker cp "${PROJECT_ROOT}/skills/flagos-component-install/tools/install_flagtree.sh" \
        "${CONTAINER}:/Offline_inference_workspace/scripts/install_flagtree.sh" 2>/dev/null || true
fi

# 部署模型下载工具
if [ -f "${PROJECT_ROOT}/skills/flagos-container-preparation/tools/download_model.py" ]; then
    docker cp "${PROJECT_ROOT}/skills/flagos-container-preparation/tools/download_model.py" \
        "${CONTAINER}:/Offline_inference_workspace/scripts/download_model.py" 2>/dev/null || true
fi

# 配置全局 pip 镜像源（加速容器内所有 pip install）
echo "  配置 pip 镜像源..."
docker exec "${CONTAINER}" bash -c "
    mkdir -p /root/.config/pip
    cat > /root/.config/pip/pip.conf <<'PIPEOF'
[global]
index-url = https://mirrors.aliyun.com/pypi/simple/
trusted-host = mirrors.aliyun.com
timeout = 120
PIPEOF
"
echo "  ✓ pip 镜像源已配置 (mirrors.aliyun.com)"

# 预装模型下载依赖 + 脚本运行时依赖
echo "  安装依赖..."
DEP_OUTPUT=$(docker exec "${CONTAINER}" bash -c "
    PATH=/opt/conda/bin:\$PATH
    pip install modelscope huggingface_hub sqlalchemy -q 2>&1 || \
    echo 'INSTALL_FAILED'
" 2>&1)
if echo "$DEP_OUTPUT" | grep -q "INSTALL_FAILED"; then
    echo "  ⚠ 依赖安装失败，详情: $(echo "$DEP_OUTPUT" | tail -3)"
else
    echo "  ✓ 依赖安装完成 (modelscope, huggingface_hub, sqlalchemy)"
fi

# 写入挂载模式标记
docker exec "${CONTAINER}" bash -c "echo 'mounted' > /Offline_inference_workspace/.mount_mode"

# 写入模型名到环境文件
docker exec "${CONTAINER}" bash -c "echo 'MODEL_NAME=${MODEL}' > /Offline_inference_workspace/.env"

echo "  ✓ 工作空间部署完成"
echo "  目录结构:"
docker exec "${CONTAINER}" bash -c "find /Offline_inference_workspace -maxdepth 2 -type d | sort"
