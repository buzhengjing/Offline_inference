---
name: flagos-env-exploration
description: 探索容器内推理环境，识别推理框架、模型格式、模型类型、关键依赖
version: 2.0.0
triggers:
  - env exploration
  - 环境探索
  - explore environment
depends_on:
  - flagos-container-preparation
next_skill: flagos-inference-verify
provides:
  - env_exploration.inference_framework
  - env_exploration.framework_version
  - env_exploration.python_version
  - env_exploration.cuda_version
  - env_exploration.model_format
  - env_exploration.model_type
  - env_exploration.inference_mode
  - env_exploration.custom_script_path
  - env_exploration.inference_entrypoint
---

# 环境探索 Skill

探测容器内的推理环境，为后续推理验证提供必要信息。重点识别离线模型的推理方式。

---

# 工作流程

## 步骤 1 — 基础环境检测

```bash
# Python 版本
docker exec ${CONTAINER} bash -c "python3 --version"

# CUDA 版本
docker exec ${CONTAINER} bash -c "nvcc --version 2>/dev/null || cat /usr/local/cuda/version.txt 2>/dev/null"

# pip 包列表（关键包）
docker exec ${CONTAINER} bash -c "pip list 2>/dev/null | grep -iE 'torch|transformers|triton|flag|llama.cpp|llama_cpp'"
```

## 步骤 2 — 模型格式检测

```bash
# 检查模型目录内容
docker exec ${CONTAINER} bash -c "ls ${MODEL_PATH}/ | head -30"

# 判断格式
docker exec ${CONTAINER} bash -c "
    if ls ${MODEL_PATH}/*.gguf 1>/dev/null 2>&1; then echo 'gguf'
    elif ls ${MODEL_PATH}/*.safetensors 1>/dev/null 2>&1; then echo 'safetensors'
    elif ls ${MODEL_PATH}/*.bin 1>/dev/null 2>&1; then echo 'bin'
    else echo 'unknown'
    fi
"
```

## 步骤 3 — 模型类型检测

根据 config.json 中的 architectures 和 model_type 字段判断模型类型：

```bash
docker exec ${CONTAINER} bash -c "python3 -c '
import json, os, sys

model_path = \"${MODEL_PATH}\"
config_file = os.path.join(model_path, \"config.json\")
if not os.path.exists(config_file):
    print(\"causal_lm\")
    sys.exit(0)

with open(config_file) as f:
    config = json.load(f)

architectures = config.get(\"architectures\", [])
model_type = config.get(\"model_type\", \"\")
arch_str = \" \".join(architectures).lower()

# Embedding / Reranker 检测
if any(k in arch_str for k in [\"embedding\", \"forsequenceclassification\", \"reranker\"]):
    if \"rerank\" in arch_str or \"forsequenceclassification\" in arch_str:
        print(\"reranker\")
    else:
        print(\"embedding\")
elif \"bert\" in arch_str and \"causal\" not in arch_str:
    print(\"embedding\")
# VLM 检测
elif any(k in arch_str for k in [\"vl\", \"vision\", \"visual\", \"multimodal\", \"image\"]) or \
     any(k in model_type.lower() for k in [\"vl\", \"vision\", \"visual\"]):
    print(\"vlm\")
# 默认文本生成
else:
    print(\"causal_lm\")
'"
```

可能的值：`causal_lm`（默认）、`vlm`、`embedding`、`reranker`

## 步骤 4 — 推理方式检测

按优先级检测推理方式，确定 `inference_mode` 和 `inference_framework`：

### 4.1 检查模型自带推理脚本

```bash
docker exec ${CONTAINER} bash -c "
    SCRIPTS=\$(find ${MODEL_PATH} -maxdepth 2 \( -name 'run_inference*.py' -o -name 'demo*.py' -o -name 'inference*.py' -o -name 'predict*.py' -o -name 'generate*.py' \) 2>/dev/null | head -5)
    if [ -n \"\$SCRIPTS\" ]; then
        echo \"custom_script:\$SCRIPTS\"
    else
        echo 'no_custom_script'
    fi
"
```

### 4.2 检查 GGUF 运行时

```bash
docker exec ${CONTAINER} bash -c "
    python3 -c 'from llama_cpp import Llama; print(\"llama_cpp_available\")' 2>/dev/null || echo 'llama_cpp_unavailable'
"
```

### 4.3 检查 Transformers

```bash
docker exec ${CONTAINER} bash -c "python3 -c 'import transformers; print(transformers.__version__)'" 2>/dev/null
```

### 4.4 推理模式决策

```
决策逻辑（按优先级）：

1. 模型自带推理脚本存在
   → inference_mode = custom_script
   → inference_framework = custom_script
   → custom_script_path = <脚本路径>

2. 模型格式为 GGUF 且 llama_cpp 可用
   → inference_mode = offline_native
   → inference_framework = llama_cpp

3. 其他情况
   → inference_mode = offline_native
   → inference_framework = transformers
```

## 步骤 5 — 写入 context.yaml

```bash
docker exec ${CONTAINER} bash -c "PATH=/opt/conda/bin:\$PATH python3 /Offline_inference_workspace/scripts/update_context.py \
    --set env_exploration.inference_framework=${FRAMEWORK} \
    --set env_exploration.framework_version=${VERSION} \
    --set env_exploration.python_version=${PY_VER} \
    --set env_exploration.cuda_version=${CUDA_VER} \
    --set env_exploration.model_format=${FORMAT} \
    --set env_exploration.model_type=${MODEL_TYPE} \
    --set env_exploration.inference_mode=${INFERENCE_MODE} \
    --set env_exploration.custom_script_path=${SCRIPT_PATH} \
    --set env_exploration.inference_entrypoint=${ENTRYPOINT} \
    --ledger-update 02_env_exploration --ledger-status success \
    --json"
```

---

# 完成条件

- 模型格式已确认（safetensors / bin / gguf）
- 模型类型已检测（causal_lm / vlm / embedding / reranker）
- 推理模式已确定（offline_native / custom_script）
- 推理框架已识别
- Python/CUDA 版本已记录
- context.yaml env_exploration 字段已写入
- workflow_ledger 步骤 02 状态为 success

---

# 故障处理

| 问题 | 处理 |
|------|------|
| 无 Transformers | 尝试 pip install transformers |
| 模型路径为空 | 检查挂载是否正确，尝试容器内搜索 |
| CUDA 不可用 | 记录 GPU 状态，可能需要检查 driver |
| 模型类型无法识别 | 默认为 causal_lm |
| GGUF 格式但无 llama_cpp | 尝试 pip install llama-cpp-python，或回退 Transformers（如支持） |
| 自带脚本存在但无法确定用法 | 记录路径，由 inference-verify 阶段 agent 阅读脚本内容决定 |
