# Offline Inference Workflow

基于状态机驱动的离线推理验证自动化框架。通过 Claude Code 编排容器准备、环境探索、推理验证、组件安装和镜像发布的完整流水线。

## 功能特性

- 状态机驱动的多阶段 workflow，支持断点续跑和失败重试
- 自动检测 GPU 厂商（NVIDIA、沐曦、摩尔线程、天数智芯等 9 种）
- 支持多种模型类型：causal_lm、vlm、embedding、reranker
- 支持多种推理框架：Transformers、llama.cpp、自定义脚本
- FlagGems / FlagTree 组件自动安装与验证
- 镜像自动打包发布到 Harbor
- Checkpoint 机制保证流程可恢复

## 项目结构

```
├── workflow/                        # 核心 workflow 引擎
│   ├── cli.py                       # CLI 入口
│   ├── state.py                     # 状态定义（Pydantic）
│   ├── runner.py                    # 线性步骤 runner
│   ├── state_machine_runner.py      # 状态机 runner
│   ├── node_runner.py               # DAG 节点 runner
│   ├── build_graph.py               # 构建执行图
│   ├── checkpoint.py                # 断点保存/恢复
│   ├── claude_executor.py           # Claude Code 子进程调用
│   ├── nodes/                       # 细粒度节点定义
│   ├── steps/                       # Claude 步骤定义
│   ├── runtime/                     # 命令执行运行时
│   ├── artifacts/                   # 产物管理
│   └── templates/                   # 推理脚本模板（Jinja2）
├── skills/                          # Skill 定义和工具脚本
│   ├── flagos-container-preparation/  # 容器准备
│   ├── flagos-env-exploration/        # 环境探索
│   ├── flagos-inference-verify/       # 推理验证
│   ├── flagos-component-install/      # 组件安装
│   └── flagos-release/                # 镜像发布
├── tools/                           # 辅助工具
├── tests/                           # 测试
├── docs/                            # 文档
└── shared/                          # 状态快照
```

## 环境要求

- Python 3.10+
- Docker（宿主机需安装并运行）
- Claude Code CLI
- GPU 驱动（NVIDIA 或其他支持的厂商）

Python 依赖（首次运行时自动安装）：

- pydantic >= 2.0
- pyyaml >= 6.0

## 快速开始

### 基本用法

```bash
python -m workflow.cli <镜像地址或容器名> <模型名> <HARBOR_USER> <HARBOR_PASSWORD>
```

### 完整参数

```bash
python -m workflow.cli <target> <model> <harbor_user> <harbor_password> \
    [--model-path PATH]        # 宿主机模型路径（可选）
    [--verbose]                # 详细输出
    [--resume]                 # 从上次断点续跑
    [--rerun-from STEP]        # 从指定步骤重新执行
    [--timeout SECONDS]        # 总超时时间（默认 10800 秒 / 3 小时）
    [--engine {dag,state_machine}]  # 执行引擎（默认 dag）
    [--use-nodes]              # 使用细粒度节点模式
```

### 示例

```bash
# 从镜像启动完整流程
python -m workflow.cli \
    harbor.baai.ac.cn/flagos/pytorch-nvidia:latest \
    Qwen3-8B \
    myuser mypassword \
    --model-path /data/models/Qwen3-8B \
    --verbose

# 从已有容器续跑
python -m workflow.cli \
    my_container \
    Qwen3-8B \
    myuser mypassword \
    --resume

# 从指定步骤重跑
python -m workflow.cli \
    my_container \
    Qwen3-8B \
    myuser mypassword \
    --rerun-from native_inference
```

## 流水线阶段

整个流程分为 3 个段（Segment），共 7 个步骤：

### 段 1：容器与环境

| 步骤 | 名称 | 说明 |
|------|------|------|
| 1 | container-preparation | 从镜像创建容器、挂载模型、部署工作空间 |
| 2 | env-exploration | 探测推理框架、模型格式、GPU 信息、关键依赖 |

### 段 2：原生推理

| 步骤 | 名称 | 说明 |
|------|------|------|
| 3 | inference-verify | 编写推理脚本、使用官方测试数据验证、生成产出 |
| 4 | release | 原生镜像打包发布到 Harbor |

### 段 3：FlagGems 推理

| 步骤 | 名称 | 说明 |
|------|------|------|
| 5 | component-install | 安装 FlagGems + FlagTree |
| 6 | inference-verify | 复用推理脚本验证 FlagGems 加速效果 |
| 7 | release | FlagGems 镜像打包发布 |

## 执行引擎

框架提供两种执行引擎：

- **dag**（默认）：线性步骤执行，每步调用 Claude Code 完成任务
- **state_machine**：状态机驱动，支持条件分支和更复杂的流程控制

```bash
# 使用状态机引擎
python -m workflow.cli ... --engine state_machine
```

## 状态传递

各步骤通过 `context.yaml` 传递状态：

- 容器内路径：`/Offline_inference_workspace/shared/context.yaml`
- 宿主机快照：`/data/Offline_inference_workspace/<model>/config/context_snapshot.yaml`
- 更新工具：容器内 `/Offline_inference_workspace/scripts/update_context.py`

## 推理产出

所有推理产出统一放在容器 `/root/` 目录下，随镜像打包：

| 文件 | 必须 | 说明 |
|------|------|------|
| `/root/run_inference.py` | ✓ | 推理脚本（原生 + FlagGems 共用） |
| `/root/test_input.txt` | ✓ | 测试输入数据 |
| `/root/README.md` | ✓ | 模型使用文档 |
| `/root/inference.log` | ✓ | 原生推理日志 |
| `/root/output_*` | ✓ | 推理输出结果 |
| `/root/gems.txt` | FlagGems | FlagGems 算子记录 |
| `/root/inference_flaggems.log` | FlagGems | FlagGems 推理日志 |

## 失败策略

- 原生推理失败：自动排查 → 尝试修复（最多 3 次）→ 仍失败则终止流程
- FlagGems 推理失败：记录问题，不影响已发布的原生镜像

## 断点恢复

Workflow 运行时自动保存 checkpoint。中断后可通过 `--resume` 从上次断点继续：

```bash
python -m workflow.cli my_container Qwen3-8B user pass --resume
```

也可指定从某个步骤重新执行：

```bash
python -m workflow.cli my_container Qwen3-8B user pass --rerun-from native_inference
```

## 测试

```bash
pytest tests/ -v
```

## 环境变量

| 变量 | 用途 | 必须 |
|------|------|------|
| `HARBOR_USER` | Harbor 仓库用户名 | ✓（或通过参数传入） |
| `HARBOR_PASSWORD` | Harbor 仓库密码 | ✓（或通过参数传入） |
| `MODELSCOPE_TOKEN` | ModelScope 上传 token | 按需 |
| `HF_TOKEN` | HuggingFace 上传 token | 按需 |

## License

Apache License 2.0
