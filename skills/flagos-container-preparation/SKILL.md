---
name: flagos-container-preparation
description: 离线推理容器准备，支持镜像/容器两种入口，精简版（仅离线推理场景）
version: 1.0.0
triggers:
  - container preparation
  - 容器准备
  - prepare container
depends_on: []
next_skill: flagos-env-exploration
provides:
  - container.name
  - container.status
  - model.local_path
  - model.container_path
  - gpu.vendor
  - gpu.count
  - image.name
---

# 容器准备 Skill（离线推理精简版）

支持两种入口，自动识别用户输入类型。容器就绪后通过 `setup_workspace.sh` 部署工具脚本。

---

# 用户输入

| 入口 | 用户提供 | 系统做什么 |
|------|---------|-----------|
| **已有容器** | 容器名称 + 模型名 | 跳过创建，搜索模型权重 |
| **已有镜像** | 镜像地址 + 模型名 | docker run 创建容器，挂载模型 |

---

# 工作流程

## 步骤 1 — 模型权重搜索

```bash
python3 skills/flagos-container-preparation/tools/check_model_local.py \
    --model "<模型名>" --output-json
```

搜索路径：/data, /mnt, /nfs, /share, /models, /home
匹配策略：精确匹配 > 包含匹配 > config.json 存在性检查

- **找到** → 记录 `model.local_path`，docker run 时挂载
- **未找到** → 使用 `download_model.py` 下载模型

### 模型下载（未找到时）

```bash
python3 skills/flagos-container-preparation/tools/download_model.py \
    --model "<org/model_name>" --source auto --local-dir /data/models/<model_name> --json
```

- `--source auto`：优先 ModelScope，失败回退 HuggingFace
- `--source modelscope`：仅从 ModelScope 下载
- `--source huggingface`：仅从 HuggingFace 下载（默认使用 hf-mirror.com 镜像）
- Token 通过环境变量 `MODELSCOPE_TOKEN` / `HF_TOKEN` 或 `--token` 参数传入

容器内执行（已部署到容器后）：
```bash
docker exec ${CONTAINER} bash -c "python3 /Offline_inference_workspace/scripts/download_model.py \
    --model '<org/model_name>' --source auto --local-dir /data/models/<model_name> --json"
```

## 步骤 2 — GPU 检测

```bash
# 按优先级检测
nvidia-smi 2>/dev/null && echo "nvidia"
npu-smi info 2>/dev/null && echo "ascend"
mx-smi 2>/dev/null && echo "metax"
mthreads-gmi 2>/dev/null && echo "mthreads"
ixsmi 2>/dev/null && echo "iluvatar"
```

## 步骤 3 — 创建容器（镜像模式）

`MODEL_SAFE` = 模型名中的 `/` 替换为 `_`（如 `thaonguyen217/farm` → `thaonguyen217_farm`）。
如果调用方已在 prompt 中提供"宿主机工作目录"路径，直接使用该路径。

> **重要**: 如果调用方 prompt 中已提供完整的 `-v` 挂载路径（如 `-v /data/Offline_inference_workspace/xxx:/Offline_inference_workspace`），**必须原样使用，不要自行重新计算**。路径中的 `_` 是正确的替换结果，不要改回含 `/` 的形式，否则会在宿主机创建错误的嵌套目录。

### NVIDIA 模板
```bash
docker run -itd \
    --name=${CONTAINER_NAME} \
    --gpus=all \
    --network=host \
    -v ${MODEL_PATH}:${MODEL_PATH} \
    -v /data/Offline_inference_workspace/${MODEL_SAFE}:/Offline_inference_workspace \
    ${IMAGE}
```

### 昇腾模板
```bash
docker run -itd \
    --name=${CONTAINER_NAME} \
    --network=host \
    --device=/dev/davinci_manager \
    --device=/dev/devmm_svm \
    --device=/dev/hisi_hdc \
    -v /usr/local/dcmi:/usr/local/dcmi \
    -v /usr/local/Ascend/driver:/usr/local/Ascend/driver \
    -v ${MODEL_PATH}:${MODEL_PATH} \
    -v /data/Offline_inference_workspace/${MODEL_SAFE}:/Offline_inference_workspace \
    ${IMAGE}
```

容器命名规则：
- 默认：`<model_short_name>_offline_infer`
- 冲突时追加时间戳：`<model_short_name>_offline_infer_MMDD_HHMM`

## 步骤 4 — 部署工作空间

```bash
bash skills/flagos-container-preparation/tools/setup_workspace.sh ${CONTAINER} ${MODEL}
```

部署内容：
- `shared/context.template.yaml` → 容器内 `/Offline_inference_workspace/shared/context.yaml`
- `shared/update_context.py` → 容器内 `/Offline_inference_workspace/scripts/update_context.py`
- `skills/flagos-component-install/tools/` → 容器内 `/Offline_inference_workspace/scripts/`

## 步骤 5 — 写入 context.yaml

```bash
docker exec ${CONTAINER} bash -c "PATH=/opt/conda/bin:\$PATH python3 /Offline_inference_workspace/scripts/update_context.py \
    --set container.name=${CONTAINER} \
    --set container.status=running \
    --set model.name=${MODEL} \
    --set model.container_path=${MODEL_PATH} \
    --set gpu.vendor=${GPU_VENDOR} \
    --set gpu.count=${GPU_COUNT} \
    --set image.name=${IMAGE} \
    --ledger-update 01_container_preparation --ledger-status success \
    --json"
```

---

# 完成条件

- 容器处于 running 状态
- 模型路径已确认（容器内可访问）
- /Offline_inference_workspace 工作空间已部署
- context.yaml 已初始化并写入容器/模型/GPU 信息
- workflow_ledger 步骤 01 状态为 success
