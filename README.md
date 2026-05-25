# Offline_inference

离线推理自动化框架，基于 FlagOS 生态，支持模型组件安装和镜像发布。

## 项目结构

```
├── .claude/
│   └── settings.local.json         # Claude Code 权限配置
├── .github/
│   └── workflows/                  # CI/CD 工作流（待扩展）
├── assets/                         # 项目资源（logo 等）
├── docs/                           # 项目文档
│   └── assets/                     # 文档图片资源
├── examples/                       # 示例文件
├── output/                         # 输出目录
├── prompts/                        # 流水线启动脚本（待扩展）
├── skills/                         # Skill 定义和工具脚本
│   ├── flagos-release/             # 镜像打包发布 + 模型权重上传
│   │   ├── SKILL.md
│   │   └── tools/
│   │       ├── main.py             # 流水线主入口
│   │       ├── requirements.txt
│   │       ├── src/                # 核心模块（配置、芯片检测、发布阶段）
│   │       └── templates/          # README 模板
│   ├── flagos-component-install/   # FlagOS 生态组件安装/升级
│   │   ├── SKILL.md
│   │   └── tools/
│   │       ├── install_component.py
│   │       └── install_flagtree.sh
│   └── shared/                     # skills 间共享资源
├── shared/                         # 共享工具（扩展用）
├── tools/                          # 顶层工具脚本（待扩展）
├── CLAUDE.md                       # Claude Code 项目指令
├── LICENSE                         # Apache 2.0
├── MAINTAINERS.md                  # 维护者列表
├── README.md                       # 本文件
├── README_cn.md                    # 中文文档
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
