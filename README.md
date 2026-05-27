# Offline Inference Workflow

离线推理验证 workflow 自动化框架，基于状态机驱动 Claude 执行各阶段任务。

## 项目结构

```
├── workflow/                       # 核心 workflow 代码
│   ├── cli.py                      # CLI 入口
│   ├── state.py                    # 状态定义
│   ├── runner.py                   # 状态机 runner
│   ├── nodes/                      # 确定性节点（node graph 模式）
│   └── steps/                      # Claude 步骤（step 模式）
├── skills/                         # Skill 定义和工具脚本（被 workflow 调用）
│   ├── flagos-release/             # 镜像打包发布 + 模型权重上传
│   ├── flagos-component-install/   # FlagOS 生态组件安装/升级
│   ├── flagos-container-preparation/
│   ├── flagos-env-exploration/
│   ├── flagos-inference-verify/
│   └── shared/                     # skills 间共享资源
├── tools/                          # 辅助工具脚本
├── tests/                          # 测试代码
├── docs/                           # 文档
├── CLAUDE.md                       # Claude Code 项目指令
├── LICENSE                         # Apache 2.0
└── settings.local.json             # 权限预配置
```

## Skills 说明

### flagos-release（镜像发布）

将验证完成的 FlagOS 环境打包为 Docker 镜像并发布到 Harbor，同时将模型权重上传到 ModelScope / HuggingFace。

支持 9 种芯片厂商自动检测：NVIDIA、沐曦、摩尔线程、天数智芯、华为昇腾、海光、昆仑芯、寒武纪、清微智能。

```bash
python3 skills/flagos-release/tools/main.py --from-context <context.yaml> [--dry-run]
```

### flagos-component-install（组件安装）

统一管理 FlagGems / FlagTree 的安装、升级、卸载。

```bash
# 容器内执行
docker exec $CONTAINER bash -c "python3 /Offline_inference_workspace/scripts/install_component.py --component flaggems --action install --json"
```

## 环境要求

- Docker
- Claude Code CLI
- 环境变量：`HARBOR_USER`、`HARBOR_PASSWORD`、`MODELSCOPE_TOKEN`、`HF_TOKEN`（按需）

## License

Apache License 2.0
