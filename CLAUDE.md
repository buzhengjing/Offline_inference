# Offline Inference Workflow — 项目级指令

> 此文件由 Claude Code 自动加载，提供 Skill 路由、工作流定义和自动决策规则。

---

## 项目结构

本项目是离线推理验证的 **workflow 自动化框架**，基于状态机驱动 Claude 执行各阶段任务。

```
workflow/          — 核心 workflow 代码（状态机、节点、步骤、runner）
skills/            — 各阶段的 Skill 定义和工具脚本（被 workflow 调用）
tools/             — 辅助工具（extract_stream_text.py）
tests/             — 测试代码
docs/              — 文档
```

---

## Skill 路由表

| 触发词 | Skill 名称 | SKILL.md 路径 |
|--------|-----------|---------------|
| 容器准备 / container preparation / prepare container | flagos-container-preparation | `skills/flagos-container-preparation/SKILL.md` |
| 环境探索 / env exploration / explore environment | flagos-env-exploration | `skills/flagos-env-exploration/SKILL.md` |
| 推理验证 / inference verify / run inference / 离线推理 | flagos-inference-verify | `skills/flagos-inference-verify/SKILL.md` |
| 组件安装 / install component / 安装 FlagGems / 安装 FlagTree / 升级 FlagGems / flag upgrade | flagos-component-install | `skills/flagos-component-install/SKILL.md` |
| 发布 / 镜像上传 / 镜像打包 / 模型发布 / release / publish / image upload / package image | flagos-release | `skills/flagos-release/SKILL.md` |

---

## 权限预配置

`settings.local.json` 已预配置 docker/pip/curl/nvidia-smi/git/modelscope/huggingface 等常用命令白名单。

使用前确认权限文件已就位：

```bash
ls .claude/settings.local.json 2>/dev/null && echo "EXISTS" || echo "MISSING — 请执行: mkdir -p .claude && cp settings.local.json .claude/settings.local.json"
```

---

## Workflow 运行方式

```bash
python -m workflow.cli <镜像或容器> <模型名> <HARBOR_USER> <HARBOR_PASSWORD> [--model-path <路径>] [--verbose]
```

流程总览见 `docs/SKILLS-OVERVIEW.md`。

---

## 约束规则

1. **工具脚本执行**：skills 下的 Python/Shell 脚本通过 `python3 skills/...` 或 `bash skills/...` 执行
2. **镜像发布**：使用 `skills/flagos-release/tools/main.py`，支持 `--from-context` 从 context.yaml 加载配置
3. **组件安装**：使用 `skills/flagos-component-install/tools/install_component.py`，支持 FlagGems 和 FlagTree
4. **容器内操作**：通过 `docker exec $CONTAINER` 前缀执行
5. **状态传递**：通过容器内 `/Offline_inference_workspace/shared/context.yaml`，使用 `update_context.py` 更新
6. **失败策略**：原生推理失败时排查修复，修复不了则记录问题并终止流程
