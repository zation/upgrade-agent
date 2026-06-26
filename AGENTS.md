# AGENTS.md

> 本文件是给 AI agent 和人类贡献者的操作手册。动代码前先读这里。

## 项目是什么

`upgrade-dependencies-agent` 是一个 ReAct + LangGraph agent，用来给旧版 JS/TS 项目升级依赖
并补充测试。项目本身用 Python 写，运行时调用 LLM（Anthropic Claude 或 OpenAI-compatible
API，例如 DeepSeek），并操作另一个磁盘目录中的目标项目。

当前主要目标项目是 `/Users/liuyang/Projects/chai-like`。

项目目标有两个：

- 做出一个真实可用的依赖升级工具。
- 作为学习和简历项目，覆盖常见 AI Agent 工程技术。

## 当前状态

- **M1 ✅ Agent Core v1**：手写 ReAct loop、工具协议、路径安全、trace 已完成。
- **M2 ✅ 单依赖升级 v1**：`upgrade` 可完成真实单依赖升级闭环。
- **M3 ✅ 批量升级与薄 LangGraph 编排 v1**：`upgrade-all` 和 `upgrade-graph` 已有可用 v1。
- **M4 ✅ 依赖研究工具 v1**：npm metadata、release/source fetching、只读 researcher 已完成。
- **M5 ✅ 确定性评估框架 v1**：eval runner、batch、trajectory checks、failure reason 已完成。
- **M6 ✅ 补测试 workflow v1**：`analyze-coverage` 和 `generate-tests` 已完成首版。
- **M7+ ⏳**：后续以 [docs/ROADMAP.md](docs/ROADMAP.md) 为准。

## 基本规则

1. **本仓库只改 agent 自身，不直接改目标项目。**  
   `chai-like` 由 agent 运行时操作，不要从本仓库直接提交或编辑它。
2. **不要提交 secrets。**  
   `.env` 已 gitignore，只提交 `.env.example`。
3. **一个逻辑变更一个 commit。**  
   使用 conventional commit，例如 `feat(scope):`、`fix(scope):`、`docs:`、`chore:`。
4. **提交前必须通过检查。**

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
```

## 常用命令

```bash
# 安装依赖
uv sync --extra dev

# lint / format / test
uv run ruff check .
uv run ruff format --check .
uv run pytest -v

# 运行 agent
uv run upgrade-dependencies-agent analyze /Users/liuyang/Projects/chai-like
uv run upgrade-dependencies-agent analyze-coverage /Users/liuyang/Projects/chai-like
uv run upgrade-dependencies-agent generate-tests /Users/liuyang/Projects/chai-like "cover uncovered behavior"
uv run upgrade-dependencies-agent research-upgrade /Users/liuyang/Projects/chai-like "mocha 4 -> 11"
uv run upgrade-dependencies-agent upgrade /Users/liuyang/Projects/chai-like "mocha 4 -> 11"
uv run upgrade-dependencies-agent upgrade-graph /Users/liuyang/Projects/chai-like "mocha 4 -> 11"
uv run upgrade-dependencies-agent upgrade-all /Users/liuyang/Projects/chai-like
uv run upgrade-dependencies-agent ask /Users/liuyang/Projects/chai-like "your task"

# 运行 eval
uv run python -m evals.runner evals/cases/chai-like-mocha-upgrade.json
uv run python -m evals.runner evals/cases
```

## 目录分层

```text
src/upgrade_dependencies_agent/
  core/          模型无关、任务无关的 agent core
  tools/         agent 工具箱，依赖 core 类型
  skills/        任务 prompt：分析、升级、研究、补测试
  orchestrator/  LangGraph workflow
  cli/           Typer 入口和 Rich UI
evals/           deterministic eval harness
docs/            架构和路线图
```

分层规则：

- `core/` 不能导入 `cli/`、`skills/` 或具体任务逻辑。
- `tools/` 只依赖 `core` 类型。
- `skills/` 和 `cli/` 负责组合 core 与 tools。
- 新增 provider 只改 `core/llm_client.py`。

## 关键文件

- `core/react_loop.py`：手写 Think → Act → Observe loop，是项目核心学习产物。
- `core/llm_client.py`：唯一了解 provider wire format 的模块。
- `core/types.py`：Message、Tool、ToolResult 等模型无关类型。
- `tools/_common.py`：`safe_resolve()`，所有文件工具都必须通过它限制路径。
- `orchestrator/upgrade_graph.py`：LangGraph execute → verify → heal v1。
- `evals/runner.py`：确定性 eval runner。
- `docs/ROADMAP.md`：唯一权威路线图。

## 工具定义方式

工具需要提供：

- `.name`
- `.description`
- `.input_schema`
- `.run(args, ctx) -> ToolResult`

常规工具继承 `ToolImpl`。新工具需要注册到 `tools/__init__.py` 的
`read_only_tools()` 或 `default_tools()`。

## 已知坑

- **OpenAI-compatible 与 Anthropic 的 tool result 映射不同。**  
  Anthropic 可以把多个 `tool_result` 放在一个 user message 中；OpenAI-compatible
  需要按 `tool_call_id` 分成多个 `role: tool` message。改 `llm_client.py` 后要回归多工具调用。
- **Python 3.13 的 `Path.write_text` 不会自动创建父目录。**  
  写文件前先 `mkdir(parents=True, exist_ok=True)`。
- **token 成本主要来自重复读大文件。**  
  早期 analyze 运行曾消耗约 120k input tokens，重点大文件是 `package-lock.json`。
- **`.env` 不是 `.env.example`。**  
  SDK 读取 `.env`，不要只改 example。

## Provider 配置

```text
LLM_PROVIDER=openai-compat      # 或 anthropic
LLM_API_KEY=sk-...              # anthropic 时可用 ANTHROPIC_API_KEY
# 可选：LLM_BASE_URL, LLM_MODEL
```

默认模型：

- `openai-compat`：`deepseek-chat`
- `anthropic`：`claude-sonnet-4-5`

CLI 可用 `--model` 覆盖。

## 不要做什么

- 不要直接编辑或提交 `/Users/liuyang/Projects/chai-like`。
- 不要为了省事引入新的 agent framework 替代手写 ReAct loop。
- 不要让 `core/` 依赖具体 provider、target project、skill 或 CLI。
- 不要绕过 `safe_resolve()` 访问目标项目文件。
