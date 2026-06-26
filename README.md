# upgrade-dependencies-agent

一个用于旧版 JS/TS 项目的依赖升级与测试补充 agent。项目用 Python 实现，核心是手写
ReAct loop，并用 LangGraph 做薄编排示例。

首个目标项目是 [`zation/chai-like`](https://github.com/zation/chai-like)：一个旧版
chai 插件，使用 CommonJS、mocha 4、nyc 11 和早期 Travis 工具链。

本项目也是 AI Agent 工程学习样例：尽量覆盖常见 agent 技术，并把每项技术落到可读代码、
测试和文档中。架构说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，路线图见
[docs/ROADMAP.md](docs/ROADMAP.md)。

## 当前状态

- **M1-M6 ✅**：核心 loop、单依赖升级、批量升级、研究工具、确定性 eval、补测试 workflow
  都已有可用 v1。
- **M7+ ⏳**：后续重点是 Prompt / Skill 质量、运行时 guardrails、成本与上下文优化、
  Research / RAG 深化、CLI / UX 完善。

## 已实现功能

- 手写 ReAct loop：支持 Think → Act → Observe、多工具调用、最大迭代限制和失败收敛。
- 多模型适配：支持 Anthropic Claude 和 OpenAI-compatible API，例如 DeepSeek。
- 工具系统：文件读写、grep/glob、shell、git diff/status、npm outdated/view/releases、
  依赖研究、GitHub releases 和 URL 抓取。
- 路径安全：文件工具通过 `safe_resolve()` 限制在目标项目目录内。
- 依赖升级 workflow：
  - `upgrade`：单依赖标准入口，使用 LangGraph backbone 执行 baseline → research → plan → execute → verify → report。
  - `upgrade-all`：批量升级入口，使用 batch backbone 执行 baseline → queue → 逐包 execute/verify → final verify → report。
- 研究 workflow：`research-upgrade` 只读分析 breaking changes，并结合项目使用方式判断风险。
- 补测试 workflow：
  - `analyze-coverage`：只读分析测试缺口。
  - `generate-tests`：生成小批量测试并验证。
- 上下文管理：基础 token 估算和历史压缩，避免长运行撑爆上下文。
- 可观测性：每次运行写入 JSONL trace，CLI 用 Rich 展示迭代、工具调用、token 和结果。
- 确定性 eval：支持隔离复制目标项目、批量 case、setup/teardown、timeout、客观后置检查、
  trace 顺序检查、trajectory policy 和 failure reason。

## 技术覆盖

| 技术 | 位置 | 状态 |
|------|------|------|
| ReAct | `core/react_loop.py` | ✅ |
| Function Calling / Tool Use | `core/types.py`、`core/llm_client.py` | ✅ |
| 多模型适配 | `core/llm_client.py` | ✅ |
| LangGraph 编排 | `orchestrator/upgrade_backbone.py`、`orchestrator/upgrade_workflow.py` | ✅ v1 |
| Self-healing / Reflection | `upgrade` / `upgrade-all` 的 verify → heal 边 | ✅ v1 |
| Research / RAG groundwork | `dependency_research`、`fetch_releases`、`fetch_url` | ✅ groundwork |
| 只读研究子流程 | `research-upgrade` | ✅ |
| Context engineering | `core/context.py` | ✅ v1 |
| Observability | `core/trace.py`、`cli/ui.py` | ✅ |
| Deterministic evals | `evals/runner.py` | ✅ v1 |
| Structured output / runtime guardrails | `core/structured.py`、`core/runtime_state.py` | ✅ v1 |

## 快速开始

```bash
# 1. 安装依赖
uv sync --extra dev

# 2. 配置模型
cp .env.example .env
# DeepSeek 或其他 OpenAI-compatible API:
#   LLM_PROVIDER=openai-compat
#   LLM_API_KEY=sk-...
# Anthropic Claude:
#   LLM_PROVIDER=anthropic
#   ANTHROPIC_API_KEY=sk-ant-...

# 3. 准备目标项目
git clone https://github.com/zation/chai-like ../chai-like
cd ../chai-like && npm install

# 4. 回到本仓库运行 agent
uv run upgrade-dependencies-agent analyze ../chai-like
uv run upgrade-dependencies-agent analyze-coverage ../chai-like
uv run upgrade-dependencies-agent generate-tests ../chai-like "cover uncovered public APIs"
uv run upgrade-dependencies-agent research-upgrade ../chai-like "mocha 4 -> 11"
uv run upgrade-dependencies-agent upgrade ../chai-like "mocha 4 -> 11"
uv run upgrade-dependencies-agent upgrade-all ../chai-like
uv run upgrade-dependencies-agent ask ../chai-like "your task"
```

## CLI 命令

| 命令 | 工具权限 | 用途 |
|------|----------|------|
| `analyze <project>` | 只读 | 分析项目结构、依赖和升级风险。 |
| `analyze-coverage <project> [focus]` | 只读 | 分析测试和 coverage 信号，输出测试缺口。 |
| `generate-tests <project> [focus]` | 完整工具 | 添加聚焦测试，并运行测试和 coverage 验证。 |
| `research-upgrade <project> "<dep>"` | 只读 | 升级前研究 breaking changes。 |
| `upgrade <project> "<dep>"` | 完整工具 | 标准单依赖升级入口：baseline → research → plan → execute → verify → report。 |
| `upgrade-all <project>` | 完整工具 | 批量升级所有直接依赖：baseline → queue → execute → verify → report。 |
| `ask <project> "<task>"` | 默认完整工具 | 执行任意任务；可加 `--read-only` 禁用写入和 shell。 |

常用参数：

- `--model` / `-m`：覆盖 `.env` 中的默认模型。
- `--max-iters`：限制 ReAct loop 最大迭代数。
- `--verbose` / `-v`：显示更完整的模型输出。
- `upgrade` / `upgrade-all` 可加 `--report-json <path>` 输出结构化 `AgentReport`；
  `changed_files` 会优先来自目标 git worktree 的实际状态。

## Evals

确定性 eval 会把目标项目复制到隔离目录中运行，不直接修改原项目。

```bash
uv run python -m evals.runner evals/cases/chai-like-mocha-upgrade.json
uv run python -m evals.runner evals/cases
```

当前 eval 不使用 LLM judge，而是检查客观结果：依赖版本、测试命令、git diff 范围、trace
顺序、是否先跑 baseline、是否一次升级多个依赖等。输出包含 deterministic failure reason，
便于后续做回归对比。

## 项目结构

```text
src/upgrade_dependencies_agent/
  core/          模型无关、任务无关的 ReAct core
  tools/         文件、shell、git、npm、release/source fetching 工具
  skills/        任务 prompt：分析、升级、研究、补测试
  orchestrator/  LangGraph workflow
  cli/           Typer 入口和 Rich 实时 UI
docs/            架构和路线图
evals/           deterministic eval runner 和 case
tests/           单元测试
```

协作规则和注意事项见 [AGENTS.md](AGENTS.md)。
