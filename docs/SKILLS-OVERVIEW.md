# 离线推理验证 — Skills 流程总览

## 流水线架构

```
workflow/cli.py (Python 入口)
    │
    ├── preflight: 模型下载 + 预检
    │
    ├── 段1: seg1_container_env (Claude 步骤)
    │     ├── 步骤1: container-preparation  容器准备
    │     └── 步骤2: env-exploration        环境探索
    │
    ├── seg1_validate: 验证容器就绪
    │
    ├── 段2: seg2_native_inference (Claude 步骤)
    │     ├── 步骤3: inference-verify       原生推理验证（编写脚本+验证+README）
    │     │     └── 失败 → 排查修复 → 仍失败则终止
    │     └── 步骤4: release                原生镜像发布（/root/ 产出随镜像打包）
    │
    ├── seg2_validate: 验证原生推理通过
    │
    ├── 段3: seg3_flaggems (Claude 步骤)
    │     ├── 步骤5: component-install      FlagGems + FlagTree 安装
    │     ├── 步骤6: inference-verify       FlagGems 推理验证（复用脚本+更新README）
    │     └── 步骤7: release                FlagGems 镜像发布（/root/ 产出随镜像打包）
    │
    └── seg3_validate: 验证 FlagGems 完成
```

## Skills 清单

| # | Skill | 目录 | 职责 |
|---|-------|------|------|
| 1 | container-preparation | `skills/flagos-container-preparation/` | 从镜像创建容器、挂载模型、部署工作空间 |
| 2 | env-exploration | `skills/flagos-env-exploration/` | 探测推理框架、模型格式、关键依赖 |
| 3 | inference-verify | `skills/flagos-inference-verify/` | 查询官方测试数据、编写推理脚本、执行验证、编写 README，产出放 /root/ |
| 4 | component-install | `skills/flagos-component-install/` | FlagGems/FlagTree 安装 |
| 5 | release | `skills/flagos-release/` | 镜像打包发布 + README 生成 |

## 推理产出规范

所有推理产出统一放在容器 `/root/` 目录下，随镜像一起打包：

| 文件 | 必须 | 说明 |
|------|------|------|
| `/root/run_inference.py` | ✓ | 推理脚本（原生 + FlagGems 共用，顶部含 flag_gems try/except） |
| `/root/test_input.txt` | ✓ | 测试输入数据（来自官方推荐） |
| `/root/README.md` | ✓ | 文档（原生 + FlagGems 共用，FlagGems 验证后追加内容） |
| `/root/inference.log` | ✓ | 原生推理日志 |
| `/root/output_*` | ✓ | 推理输出结果 |
| `/root/gems.txt` | FlagGems | FlagGems 算子记录 |
| `/root/inference_flaggems.log` | FlagGems | FlagGems 推理日志 |

## 状态传递

- **Skill 间**：通过容器内 `/Offline_inference_workspace/shared/context.yaml`
- **段间**：Shell 层从容器 cp 到宿主机 `/data/Offline_inference_workspace/<model>/config/context_snapshot.yaml`
- **更新工具**：`/Offline_inference_workspace/scripts/update_context.py`

## 失败策略

- 步骤3 原生推理失败：排查 → 尝试修复（最多 3 次）→ 仍失败则记录原因并终止流程
- 步骤6 FlagGems 推理失败：记录问题，不影响原生镜像已发布的结果

## 启动方式

```bash
python -m workflow.cli <镜像地址或容器名> <模型名> <HARBOR_USER> <HARBOR_PASSWORD> [--model-path <路径>] [--verbose]
```
