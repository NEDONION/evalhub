# EvalHub 文档中心

这里是 EvalHub 的详细文档索引。项目简介、能力概览和快速命令请先看仓库根目录的 [README](../README.md)。

## 推荐阅读顺序

1. 从[本地运行指南](getting-started/LOCAL_RUN.md)启动 EvalHub，并按需配置 [Ollama](getting-started/OLLAMA.md)。
2. 阅读[系统架构](architecture/ARCHITECTURE.md)，建立模块和扩展点的整体认识。
3. 结合 [API 草案](architecture/API.md)与[数据模型](architecture/DATA_MODEL.md)理解接口和领域对象。
4. 通过 [PRD](product/PRD.md) 与 [Roadmap](product/ROADMAP.md)了解产品边界和演进方向。
5. 使用 [Codex 文档沉淀流程](development/CODEX_WORKFLOW.md)维护设计、计划和实现记录。

## 快速开始

| 文档 | 内容 |
| --- | --- |
| [本地运行指南](getting-started/LOCAL_RUN.md) | 安装、启动、真实 Benchmark 与本地控制台 |
| [Ollama 指南](getting-started/OLLAMA.md) | 模型安装、服务验证与故障排查 |

## 架构与参考

| 文档 | 内容 |
| --- | --- |
| [系统架构](architecture/ARCHITECTURE.md) | 分层、核心模块、目标架构与扩展原则 |
| [API 草案](architecture/API.md) | 模型、数据集、任务、结果与门禁接口 |
| [数据模型](architecture/DATA_MODEL.md) | 核心实体、字段和关系 |

## 产品与规划

| 文档 | 内容 |
| --- | --- |
| [产品需求文档](product/PRD.md) | 产品定位、角色、范围与非功能需求 |
| [产品路线图](product/ROADMAP.md) | 阶段目标、交付边界与 backlog |

## 开发协作

| 文档 | 内容 |
| --- | --- |
| [Codex 文档沉淀流程](development/CODEX_WORKFLOW.md) | 对话后同步设计、运行方式与决策的规则 |

## 设计与实施记录

- [`superpowers/specs/`](superpowers/specs/)：经过确认的设计说明与决策边界。
- [`superpowers/plans/`](superpowers/plans/)：可执行的实施步骤与验证命令。

这些记录用于解释“为什么这样设计”和“如何实施”，正式使用说明仍以上述分类文档为准。
