---
name: flagos-inference-verify
description: 离线推理验证，编写推理脚本、使用官方测试数据验证、产出完整交付物（原生环境 & FlagGems 环境共用）
version: 4.0.0
triggers:
  - inference verify
  - 推理验证
  - verify inference
  - run inference
depends_on:
  - flagos-env-exploration
provides:
  - native_inference.success
  - native_inference.output_path
  - native_inference.readme_path
  - flaggems_inference.success
  - flaggems_inference.output_path
  - flaggems_inference.readme_path
---

# 推理验证 Skill

## 核心目标

编写推理脚本，使用官方推荐测试数据执行推理，得到完整输出并存储，验证通过后编写 README.md。

**所有产出统一放在容器 `/root/` 目录下**，随镜像一起打包发布。

## 产出文件规范

| 文件 | 用途 | 生成时机 |
|------|------|---------|
| `/root/run_inference.py` | 推理脚本（原生 + FlagGems 共用） | 步骤 3 |
| `/root/test_input.txt` | 测试输入数据（或其他格式） | 步骤 2 |
| `/root/README.md` | 文档（原生 + FlagGems 共用） | 步骤 5（原生），步骤 6（FlagGems 追加） |
| `/root/gems.txt` | FlagGems 算子记录 | FlagGems 推理时自动生成 |
| `/root/output_*` | 推理输出结果文件 | 步骤 4 |

---

# 模式区分

通过调用参数区分两种模式：

| 模式 | 触发条件 | context 写入字段 |
|------|---------|-----------------|
| native | 步骤3 调用 | `native_inference.*` |
| flaggems | 步骤6 调用（FlagGems 已安装后） | `flaggems_inference.*` |

---

# 推理模式 × 模型类型 支持矩阵

| inference_mode | 适用场景 | 推理方式 |
|---------------|---------|---------|
| custom_script | 模型自带推理脚本 | 直接执行脚本 |
| offline_native | 无推理框架 / GGUF 格式 | Transformers 或 llama.cpp |

| model_type | Transformers | llama.cpp |
|-----------|-------------|-----------|
| causal_lm | ✓ | ✓ (GGUF) |
| vlm | ✓ | ✗ |
| embedding | ✓ | ✗ |
| reranker | ✓ | ✗ |

---

# 工作流程（原生推理验证）

## 步骤 1 — 读取环境信息

从容器内 `/Offline_inference_workspace/shared/context.yaml` 读取：
- `env_exploration.inference_mode` → 决定推理路径（custom_script / offline_native）
- `env_exploration.inference_framework` → 具体框架（transformers / llama_cpp / custom_script）
- `env_exploration.model_type` → 决定测试数据和推理 API（causal_lm / vlm / embedding / reranker）
- `env_exploration.model_format` → 模型格式（safetensors / bin / gguf）
- `env_exploration.custom_script_path` → 自带脚本路径（如有）
- `model.container_path` → 模型路径
- `model.name` → 模型名称（用于查询官方文档）

## 步骤 2 — 查询官方测试数据并构造输入

**优先使用官方推荐的测试数据**，而非固定模板。

### 2.1 查询官方数据源

根据 `model.name` 查询以下来源：

1. **模型仓库 README**：查看 HuggingFace/ModelScope 模型卡片的 "How to use" / "Usage" 部分
2. **examples 目录**：检查模型仓库中的 `examples/` 或 `demo/` 目录
3. **官方文档**：搜索模型官方 GitHub 仓库的文档

查询方式示例：
```bash
# 查看模型目录中的 README
docker exec ${CONTAINER} bash -c "cat ${MODEL_PATH}/README.md 2>/dev/null | head -100"

# 查看 examples 目录
docker exec ${CONTAINER} bash -c "ls ${MODEL_PATH}/examples/ 2>/dev/null"

# 或通过 WebSearch 查询官方文档
# 搜索关键词："{model_name} inference example" 或 "{model_name} usage"
```

### 2.2 构造测试输入

根据官方示例构造测试输入，保存到 `/root/test_input.txt`（或其他格式）。

**格式根据模型类型决定**：

| model_type | 推荐格式 | 示例 |
|-----------|---------|------|
| causal_lm | 纯文本（每行一条 prompt） | `Hello, how are you?` |
| vlm | JSON（包含 text + image 路径） | `{"prompt": "...", "image": "..."}` |
| embedding | 纯文本（每行一条待编码文本） | `What is deep learning?` |
| reranker | JSON（query + passage 对） | `{"query": "...", "passage": "..."}` |

**如果官方无明确示例**，使用以下默认模板：

#### causal_lm 默认模板
```bash
docker exec ${CONTAINER} bash -c "cat > /root/test_input.txt << 'EOF'
Hello, how are you?
What is 2+2?
Explain quantum computing in one sentence.
EOF"
```

#### vlm 默认模板
```bash
docker exec ${CONTAINER} bash -c "python3 -c '
from PIL import Image
img = Image.new(\"RGB\", (224, 224), color=(128, 128, 200))
img.save(\"/root/test_image.jpg\")
print(\"✓ 测试图片已生成\")
'

cat > /root/test_input.json << 'EOF'
[
    {\"prompt\": \"Describe this image.\", \"image\": \"/root/test_image.jpg\"},
    {\"prompt\": \"What objects can you see?\", \"image\": \"/root/test_image.jpg\"}
]
EOF"
```

#### embedding 默认模板
```bash
docker exec ${CONTAINER} bash -c "cat > /root/test_input.txt << 'EOF'
What is deep learning?
Explain the concept of neural networks.
How does backpropagation work?
EOF"
```

#### reranker 默认模板
```bash
docker exec ${CONTAINER} bash -c "cat > /root/test_input.json << 'EOF'
[
    {\"query\": \"What is deep learning?\", \"passage\": \"Deep learning is a subset of machine learning.\"},
    {\"query\": \"What is deep learning?\", \"passage\": \"The weather today is sunny.\"}
]
EOF"
```

## 步骤 3 — 编写推理脚本

编写 `/root/run_inference.py`，参照 `/mnt/data/test/run_inferench.py` 的结构。

### 3.1 脚本基本结构

```python
# /root/run_inference.py
try:
    import flag_gems
    flag_gems.enable(record=True, once=True, unused=[], path="/root/gems.txt")
except ImportError:
    pass

import argparse
# ... 其他导入 ...

def parse_args():
    parser = argparse.ArgumentParser(description="...")
    parser.add_argument("--model_path", type=str, required=True, help="Path to model directory")
    parser.add_argument("--input_file", type=str, required=True, help="Path to input file")
    parser.add_argument("--output_file", type=str, default=None, help="Path to save output")
    parser.add_argument("--device", type=str, default="cuda:0", help="Device for inference")
    parser.add_argument("--batch_size", type=int, default=32, help="Batch size")
    # ... 根据 model_type 添加其他参数 ...
    return parser.parse_args()

def main():
    args = parse_args()
    # ... 模型加载、推理、输出 ...

if __name__ == "__main__":
    main()
```

### 3.2 关键要求

1. **顶部必须有 flag_gems try/except 块**：原生环境自动跳过，FlagGems 环境自动激活
2. **argparse 参数化**：model_path、input_file、output_file、device 为基本参数
3. **推理逻辑根据 model_type 编写**：使用正确的模型类、tokenizer、推理 API
4. **输出到 stdout + 可选保存文件**：每个样本打印摘要，支持 --output_file 保存完整结果
5. **UNK token 检查**（如适用）：对 tokenizer 输出检查未识别 token
6. **输出文件必须包含完整推理结果**：embedding 模型保存完整向量（不能只保存前 N 个值或摘要），causal_lm 保存完整生成文本，reranker 保存完整 score。控制台 print 可以只显示摘要（如 embedding[:5]），但 --output_file 写入的必须是完整数据

### 3.3 各 model_type 推理脚本参考

#### causal_lm

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

tokenizer = AutoTokenizer.from_pretrained(args.model_path, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(args.model_path, trust_remote_code=True, device_map="auto", torch_dtype="auto")

with open(args.input_file) as f:
    lines = [line.strip() for line in f if line.strip()]

for i, prompt in enumerate(lines):
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    with torch.no_grad():
        output_ids = model.generate(**inputs, max_new_tokens=128)
    generated = tokenizer.decode(output_ids[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    print(f"Sample {i}: input = {prompt}")
    print(f"  output = {generated[:100]}...")
```

#### embedding

```python
from transformers import AutoModel, AutoTokenizer
import torch, json

tokenizer = AutoTokenizer.from_pretrained(args.model_path, trust_remote_code=True)
model = AutoModel.from_pretrained(args.model_path, trust_remote_code=True, device_map="auto", torch_dtype="auto")

with open(args.input_file) as f:
    lines = [line.strip() for line in f if line.strip()]

results = []
for i, text in enumerate(lines):
    inputs = tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512).to(model.device)
    with torch.no_grad():
        output = model(**inputs, output_hidden_states=True)
    # 完整 embedding 向量，不能截断
    emb = output.last_hidden_state[:, 0, :].squeeze().cpu().tolist()
    results.append({"input": text, "embedding": emb})
    # 控制台只打印摘要
    print(f"Sample {i}: input = {text}")
    print(f"  dim = {len(emb)}, embedding[:5] = {emb[:5]}")

if args.output_file:
    with open(args.output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Full results ({len(results)} samples) saved to {args.output_file}")
```

#### vlm

```python
from transformers import AutoProcessor, AutoModelForVision2Seq
from PIL import Image
import torch, json

processor = AutoProcessor.from_pretrained(args.model_path, trust_remote_code=True)
model = AutoModelForVision2Seq.from_pretrained(args.model_path, trust_remote_code=True, device_map="auto", torch_dtype="auto")

with open(args.input_file) as f:
    test_data = json.load(f)

for i, item in enumerate(test_data):
    image = Image.open(item["image"]).convert("RGB")
    inputs = processor(text=item["prompt"], images=image, return_tensors="pt").to(model.device)
    with torch.no_grad():
        output_ids = model.generate(**inputs, max_new_tokens=128)
    generated = processor.decode(output_ids[0], skip_special_tokens=True)
    print(f"Sample {i}: input = {item['prompt']}")
    print(f"  output = {generated[:100]}...")
```

#### reranker

```python
from transformers import AutoModelForSequenceClassification, AutoTokenizer
import torch, json

tokenizer = AutoTokenizer.from_pretrained(args.model_path, trust_remote_code=True)
model = AutoModelForSequenceClassification.from_pretrained(args.model_path, trust_remote_code=True, device_map="auto", torch_dtype="auto")

with open(args.input_file) as f:
    test_data = json.load(f)

for i, item in enumerate(test_data):
    inputs = tokenizer(item["query"], item["passage"], return_tensors="pt", padding=True, truncation=True).to(model.device)
    with torch.no_grad():
        output = model(**inputs)
    score = output.logits.squeeze().item()
    print(f"Sample {i}: query={item['query']}, score={score:.4f}")
```

#### llama.cpp / GGUF

```python
from llama_cpp import Llama
import glob

gguf_files = glob.glob(f"{args.model_path}/*.gguf")
llm = Llama(model_path=gguf_files[0], n_ctx=2048, n_gpu_layers=-1)

with open(args.input_file) as f:
    lines = [line.strip() for line in f if line.strip()]

for i, prompt in enumerate(lines):
    output = llm(prompt, max_tokens=128, temperature=0.7)
    text = output["choices"][0]["text"]
    print(f"Sample {i}: input = {prompt}")
    print(f"  output = {text[:100]}...")
```

#### custom_script 模式

如果模型自带推理脚本：
1. **先阅读脚本内容**（`cat ${CUSTOM_SCRIPT_PATH}`），理解其参数和用法
2. **编写包装脚本** `/root/run_inference.py`，调用或参考自带脚本逻辑
3. 包装脚本同样需要顶部 flag_gems 导入和 argparse 参数
4. 如果自带脚本不便修改，可直接使用自带脚本但在 `/root/run_inference.py` 中封装调用逻辑

---

## 步骤 4 — 执行推理验证

### 4.1 执行推理

```bash
docker exec ${CONTAINER} bash -c "python3 /root/run_inference.py \
    --model_path ${MODEL_PATH} \
    --input_file /root/test_input.txt \
    --output_file /root/output_result.json \
    --device cuda:0 \
    2>&1 | tee /root/inference.log"
```

**注意**：具体参数根据模型类型调整，output_file 格式可以是 `.json`、`.npy` 等。

### 4.2 验证输出

验证标准：
1. 脚本退出码为 0
2. 推理输出非空（stdout 有有效输出）
3. 输出文件已生成且非空（如指定了 --output_file）
4. 输出格式正确（embedding 维度合理、生成文本非空、score 为数值等）

```bash
# 检查退出码和输出文件
docker exec ${CONTAINER} bash -c "
    test -f /root/inference.log && echo '✓ 推理日志存在' || echo '✗ 无推理日志'
    test -s /root/output_result.json && echo '✓ 输出文件非空' || echo '⚠ 无输出文件（检查是否指定了 --output_file）'
    grep -c 'Sample' /root/inference.log | xargs -I{} echo '✓ 处理了 {} 个样本'
"
```

---

## 步骤 5 — 编写 README.md

验证通过后，编写 `/root/README.md`。

### README 格式要求

**参照 `/mnt/data/test/README.md` 格式**，必须包含以下章节：

```markdown
# {模型名称} - 离线推理验证

## 项目说明
模型名称、架构、用途的简要描述。

## 环境准备
### 创建容器
docker run 命令（包含 GPU、挂载等参数）
### 进入容器
docker exec 命令
### 安装依赖
容器内需要额外安装的包

## 模型路径
模型在容器内的实际路径

## 推理入口文件
/root/run_inference.py

## 输入输出说明
### 输入格式
输入文件的格式说明和示例
### 输出格式
输出的 shape、类型、格式说明

## 命令行参数
参数表（参数名、必填、默认值、说明）

## 完整执行命令
基本推理命令 + 带可选参数的完整命令

## GPU 使用说明
验证使用的 GPU 型号、设备指定方式

## flag_gems 接入位置
说明 flag_gems 代码位置和行为

## 输入样例
测试输入的实际内容

## 输出结果示例
推理产生的实际输出数值（从 inference.log 中提取真实结果）

## 跑通状态
**已跑通** - {日期}
- 测试样本数
- GPU 验证情况
- flag_gems 记录情况（如有）

## 文件清单
| 文件 | 用途 |
|------|------|
| /root/run_inference.py | 推理脚本 |
| /root/test_input.txt | 测试输入 |
| /root/README.md | 本文档 |
| ... | ... |

## 注意事项
模型使用的特殊注意点
```

### README 编写规则

1. **输出结果示例必须是真实数值**：从推理日志中提取实际输出，不能编造
2. **命令行参数表必须与脚本实际参数一致**
3. **模型路径使用容器内实际路径**
4. **容器创建命令包含实际使用的镜像地址**

---

## 步骤 6 — 写入成功状态

```bash
docker exec ${CONTAINER} bash -c "PATH=/opt/conda/bin:\$PATH python3 /Offline_inference_workspace/scripts/update_context.py \
    --set native_inference.success=true \
    --set native_inference.output_path=/root/output_result.json \
    --set native_inference.readme_path=/root/README.md \
    --set workflow.native_inference_ok=true \
    --ledger-update 03_native_inference --ledger-status success \
    --json"
```

---

# FlagGems 场景补充流程

FlagGems 模式下（步骤6调用），使用同一个推理脚本，FlagGems 自动激活。

## 步骤 1 — 执行 FlagGems 推理验证

前提：FlagGems + FlagTree 已安装（由 component-install skill 完成）。

```bash
docker exec ${CONTAINER} bash -c "python3 /root/run_inference.py \
    --model_path ${MODEL_PATH} \
    --input_file /root/test_input.txt \
    --output_file /root/output_result_flaggems.json \
    --device cuda:0 \
    2>&1 | tee /root/inference_flaggems.log"
```

验证：
1. 推理成功（退出码 0）
2. 输出结果与原生结果格式一致
3. `/root/gems.txt` 已生成，包含 FlagGems 接管的算子列表

```bash
docker exec ${CONTAINER} bash -c "
    test -f /root/gems.txt && echo '✓ gems.txt 已生成' && cat /root/gems.txt || echo '✗ gems.txt 未生成'
"
```

## 步骤 2 — 更新 README.md

在 `/root/README.md` 中追加 FlagGems 验证结果：

追加内容示例：
```markdown
## flag_gems 接入位置

位于 `/root/run_inference.py` 文件顶部，使用 try/except 保护，无 flag_gems 环境时自动跳过：

\```python
try:
    import flag_gems
    flag_gems.enable(record=True, once=True, unused=[], path="/root/gems.txt")
except ImportError:
    pass
\```

flag_gems 记录文件输出至 `/root/gems.txt`，记录的算子包括：
- GEMS XXX
- GEMS YYY
- ...（从实际 gems.txt 中读取）
```

**注意**：gems.txt 中的算子列表必须从实际生成的文件中读取，不能编造。

## 步骤 3 — 写入成功状态

```bash
docker exec ${CONTAINER} bash -c "PATH=/opt/conda/bin:\$PATH python3 /Offline_inference_workspace/scripts/update_context.py \
    --set flaggems_inference.success=true \
    --set flaggems_inference.output_path=/root/output_result_flaggems.json \
    --set flaggems_inference.readme_path=/root/README.md \
    --set workflow.flaggems_inference_ok=true \
    --ledger-update 06_flaggems_inference --ledger-status success \
    --json"
```

---

# 失败排查

如果推理失败，按以下顺序排查：

1. **检查错误日志**：CUDA OOM → 减小 batch / 调整参数
2. **检查模型加载**：权重文件完整性、config.json 兼容性
3. **检查依赖版本**：torch/cuda 版本匹配
4. **推理模式回退**：framework 失败 → 尝试 offline_native；llama.cpp 失败 → 尝试 Transformers
5. **尝试修复**：根据错误类型自动调整参数重试（最多 3 次）
6. **仍失败**：记录详细错误信息到 context，终止流程

```bash
# 记录失败
docker exec ${CONTAINER} bash -c "PATH=/opt/conda/bin:\$PATH python3 /Offline_inference_workspace/scripts/update_context.py \
    --set native_inference.success=false \
    --set native_inference.failure_reason='${ERROR_MSG}' \
    --set native_inference.troubleshoot_attempts=${ATTEMPTS} \
    --set workflow.terminated=true \
    --set workflow.termination_reason='原生推理验证失败，排查后仍无法修复' \
    --ledger-update 03_native_inference --ledger-status failed --ledger-fail-reason '${ERROR_MSG}' \
    --json"
```

---

# 完成条件

## 原生推理验证

- [x] 测试数据已构造（来自官方推荐或默认模板）
- [x] 推理脚本 `/root/run_inference.py` 已编写（含 flag_gems try/except）
- [x] 推理已执行，输出非空且格式正确
- [x] 输出结果已保存到 `/root/`
- [x] `/root/README.md` 已编写（包含真实输出结果示例）
- [x] context.yaml 已更新
- [x] workflow_ledger 步骤状态已更新

## FlagGems 推理验证

- [x] 同一推理脚本执行成功（flag_gems 自动激活）
- [x] `/root/gems.txt` 已生成
- [x] `/root/README.md` 已追加 FlagGems 验证结果和算子列表
- [x] context.yaml 已更新
- [x] workflow_ledger 步骤状态已更新

## 镜像打包产出（/root/ 目录下）

| 文件 | 必须 | 说明 |
|------|------|------|
| `run_inference.py` | ✓ | 推理脚本 |
| `test_input.txt` | ✓ | 测试输入数据 |
| `README.md` | ✓ | 文档 |
| `inference.log` | ✓ | 原生推理日志 |
| `output_*` | ✓ | 推理输出结果 |
| `gems.txt` | FlagGems 场景 | 算子记录 |
| `inference_flaggems.log` | FlagGems 场景 | FlagGems 推理日志 |

---

# 故障排查

| 问题 | 处理 |
|------|------|
| CUDA OOM | 减小 max_tokens / 减小 batch size |
| 模型加载失败 | 检查 config.json、权重完整性、trust_remote_code |
| tokenizer 错误 | 检查 tokenizer_config.json、special_tokens |
| 推理超时 | 设置 timeout，减小输入长度 |
| FlagGems 兼容性 | 检查 flag_gems.enable() 是否支持当前 Transformers 版本 |
| GGUF 加载失败 | 检查 llama-cpp-python 版本、GGUF 文件完整性 |
| 自带脚本执行失败 | 阅读脚本源码，检查依赖和参数 |
| VLM 图片加载失败 | 检查 PIL/Pillow 是否安装 |
| Embedding 维度为 0 | 检查模型是否正确加载，尝试不同 pooling 策略 |
| gems.txt 未生成 | 确认 flag_gems 已正确安装，检查 import flag_gems 是否成功 |
