# 架构说明

> 面向贡献者、后续 agent 和面试复盘。日常协作规则见 [AGENTS.md](../AGENTS.md)，使用方式见
> [README.md](../README.md)，路线图以 [ROADMAP.md](ROADMAP.md) 为准。

## 设计目标

1. **真实可运行**：能在旧版 JS/TS 项目上分析、升级依赖、补测试，并用测试验证。
2. **学习覆盖面广**：把常见 agent 技术做成可读实现，而不是只写概念。
3. **分层清楚**：核心 loop 与模型、任务、CLI、工具解耦。
4. **安全可回放**：目标项目路径受限，运行过程有 trace，eval 可复现。

## 技术与实现位置

| 技术 | 实现位置 | 状态 |
|------|----------|------|
| ReAct loop | `core/react_loop.py` | ✅ |
| Function calling / tool use | `core/types.py`、`core/llm_client.py` | ✅ |
| 多模型适配 | `core/llm_client.py` | ✅ |
| Context compaction | `core/context.py` | ✅ v1 |
| JSONL trace | `core/trace.py` | ✅ |
| 工具协议 | `core/types.py` 的 `Tool` / `ToolImpl` | ✅ |
| 文件路径隔离 | `tools/_common.py` 的 `safe_resolve()` | ✅ |
| npm / changelog research | `tools/npm.py`、`tools/changelog.py` | ✅ v1 |
| LangGraph 编排 | `orchestrator/upgrade_backbone.py`、`orchestrator/upgrade_workflow.py` | ✅ v1 |
| 自修复边 | `upgrade` / `upgrade-all` 的 verify → heal | ✅ v1 |
| 确定性 eval | `evals/runner.py` | ✅ v1 |
| 结构化输出与 runtime guardrails | `core/structured.py`、`core/runtime_state.py` | ✅ v1 |
| 深度 RAG | Roadmap M10 | ⏳ |

## 分层模型

```text
cli / skills       组合 core 和 tools，定义具体任务
tools              任务相关工具；只依赖 core 类型
core               模型无关、任务无关的 agent 基础设施
llm_client         唯一知道 provider wire format 的模块
```

约束：

- `core/` 不导入 `cli/`、`skills/` 或具体项目逻辑。
- 新增模型 provider 只改 `core/llm_client.py`。
- 工具通过 `Tool` 协议暴露 `.name`、`.description`、`.input_schema` 和 `.run()`。
- 文件工具必须通过 `safe_resolve()`，不能直接拼路径访问目标项目。

## ReAct loop

`core/react_loop.py` 手写 Think → Act → Observe loop，不依赖 agent 框架。

```text
messages = [user task]
repeat up to max_iterations:
    如果接近 token budget，则压缩旧历史
    调用 llm.ask(system, messages, tools)
    记录 assistant message 和 trace
    如果没有 tool_use，则返回最终文本
    执行所有 tool_use
    把 tool_result 作为 user message 追加回上下文
```

loop 上叠加了三件事，但不改变主干逻辑：

- **Trace**：每轮、每个工具调用和最终结果写入 JSONL。
- **Callbacks**：CLI 用 `LoopCallbacks` 渲染实时输出，core 不依赖 Rich。
- **Compaction**：长运行时保留任务和最近消息，中间历史替换为摘要 stub。

## LLM 适配

`core/llm_client.py` 定义 `LLMClient` Protocol，并实现：

- `AnthropicClient`
- `OpenAICompatibleClient`
- `create_client()`

provider 差异被限制在这一层，尤其是 tool result 映射：

- Anthropic 可以把多个 `tool_result` 放进一个 user message。
- OpenAI-compatible 需要每个 `tool_call_id` 对应独立 `role: tool` message。

如果改这层，需要重点回归多工具调用场景。

## 工具系统

工具分两组：

- `read_only_tools()`：读文件、搜索、git status/diff、npm metadata、release/source fetching。
- `default_tools()`：只读工具 + 写文件、编辑文件、运行命令。

当前工具能力：

- 文件：`read_file`、`write_file`、`edit_file`、`grep`、`glob`
- shell：`run_command`
- git：`git_status`、`git_diff`
- npm：`npm_outdated`、`npm_view`、`npm_releases`、`dependency_research`
- 文档研究：`fetch_releases`、`fetch_url`

安全和成本控制：

- 文件路径限制在目标项目目录内。
- `read_file`、`run_command`、`grep`、`glob` 都有输出上限。
- 测试失败作为普通 tool output 返回，供模型诊断和自修复。

## CLI workflow

| 命令 | 说明 |
|------|------|
| `analyze` | 只读分析项目和依赖风险。 |
| `analyze-coverage` | 只读分析测试缺口。 |
| `generate-tests` | 添加聚焦测试并验证。 |
| `research-upgrade` | 只读研究一个依赖升级。 |
| `upgrade` | 标准单依赖升级入口，使用 LangGraph backbone。 |
| `upgrade-all` | 批量升级入口，使用 batch backbone。 |
| `ask` | 任意任务，可用 `--read-only` 限制权限。 |

## LangGraph 编排

`orchestrator/upgrade_backbone.py` 和 `orchestrator/upgrade_workflow.py`
当前承载单依赖与批量升级的 LangGraph backbone。

已实现：

- baseline 节点建立升级前测试基线。
- 单依赖 research 节点只读研究 breaking changes。
- 批量 queue 节点只读生成结构化 direct dependency 升级队列。
- plan 节点生成最小升级计划。
- execute 节点执行升级。
- 批量 execute 节点按结构化 queue 逐个 package 派发 `execute_package` / `verify_package`。
- verify 节点独立验证结果。
- verify 失败时进入 heal 节点。
- report 节点汇总最终结果。
- heal 次数受 `max_heal_attempts` 限制。
- mutation stage 会把当前 dependency 和允许修改文件传入 runtime config。
- runtime guardrail 会拒绝超出 `allowed_files` 的显式文件写入或编辑。
- runtime guardrail 会拒绝危险全局 revert 命令。
- CLI stage runner 会在第一次 mutation stage 前检查 target worktree，已有改动时停止。
- 单元测试覆盖通过、失败后修复、超过修复预算、CLI 接入等路径。

后续要把 verify 的自然语言 verdict 判断替换为 structured output，见 Roadmap M8。

## Research 能力

当前是 RAG groundwork，而不是真正向量检索：

- `dependency_research` 返回 current / target / latest、major span、候选 source 和风险提示。
- `npm_view` / `npm_releases` 读取 npm metadata。
- `fetch_releases` 读取 GitHub release notes。
- `fetch_url` 读取 changelog、migration guide、docs 页面并转为文本。
- `research-upgrade` 用只读工具结合项目 usage search 输出结论。

真正的 chunk、ranking、cache、source coverage eval 放在 Roadmap M10。

## Eval 设计

`evals/runner.py` 是 deterministic eval harness：

- 将目标项目复制到临时目录。
- 初始化 git baseline。
- 执行 case command。
- 运行客观 checks。
- 输出单 case 或批量 JSON summary。

已支持：

- `timeout`、`env`、`setup`、`teardown`、`budgets`
- `package_json_version`
- `command`
- `git_diff`
- `trace_sequence`
- `trajectory_policy: baseline_before_mutation`
- `trajectory_policy: single_dependency_at_a_time`
- failure reason：`timeout`、`wrong_diff`、`test_failed`、`llm_error`、
  `postcondition_failed`、`trajectory_violation`、`baseline_missing`、
  `multi_dependency_upgrade`

后续 eval 扩展见 Roadmap M8-M11。

## 关键取舍

- **手写 loop，不用 agent framework 替代核心**：这是学习目标，也是核心可解释性来源。
- **LangGraph 只做编排**：用于 state machine 与 self-heal，不替代 ReAct loop。
- **失败测试不是异常**：测试失败是模型修复问题所需的观察信号。
- **`edit_file` 使用精确唯一匹配**：避免模型重写整文件或误改多个位置。
- **先做 deterministic eval**：优先验证客观 outcome 和 trajectory，再考虑 LLM judge。

## 当前路线图摘要

- **M1-M6 ✅**：core、单依赖升级、批量/graph v1、研究工具 v1、eval v1、补测试 v1。
- **M7 ✅**：Prompt / Skill 质量 v1。
- **M8 🚧**：LangGraph backbone、结构化状态与 runtime guardrails。
- **M9 ⏳**：成本与上下文优化。
- **M10 ⏳**：Research / RAG 深化。
- **M11 ⏳**：CLI / UX 与集成体验。
