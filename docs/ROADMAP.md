# ROADMAP — 后续规划

> 本文件是项目路线图的唯一权威来源。README 只保留简短状态摘要并链接到这里。
> 新 session 开始时，让 agent 读 `AGENTS.md`、`docs/ARCHITECTURE.md` 和本文件即可接续。

## 状态标记

- ✅ 已完成：当前目标已经有可用实现，并有测试或真实运行记录支撑。
- 🚧 进行中：已有一部分落地，但当前 milestone 的目标还没有完整闭环。
- ⏳ 未开始：已有规划，但还没有实质实现。
- 🧊 暂缓：有价值，但暂时不进入近期开发顺序。

## Milestone 总览

| Milestone | 状态 | 标题 | 当前结论 |
|-----------|------|------|----------|
| M1 | ✅ | Agent Core v1 | 手写 ReAct loop、工具协议、路径安全、trace 已完成 |
| M2 | ✅ | 单依赖升级 v1 | `upgrade` 已能完成真实单依赖升级闭环 |
| M3 | ✅ | 批量升级与薄 LangGraph 编排 v1 | `upgrade-all`、`upgrade-graph` 已有可用 v1 |
| M4 | ✅ | 依赖研究工具 v1 | npm metadata、release/source fetching、read-only researcher 已完成 |
| M5 | ✅ | 确定性评估框架 v1 | eval runner、batch、trajectory checks、failure reason 已完成 |
| M6 | ✅ | 补测试 workflow v1 | `analyze-coverage`、`generate-tests` 已完成首版 |
| M7 | ✅ | Prompt / Skill 质量 v1 | 共享片段、结构化 renderer、contract tests、eval fixtures 已完成 |
| M8 | 🚧 | 结构化状态与运行时 Guardrails | baseline-before-mutation guardrail v1 已开始落地 |
| M9 | ⏳ | 成本与上下文优化 | 用 eval 数据驱动优化 |
| M10 | ⏳ | Research / RAG 深化 | 从 source fetching 升级为真正 retrieval |
| M11 | ⏳ | CLI / UX 与集成体验 | JSON/dry-run/CI 等收尾能力 |

---

## 当前关键判断

1. **M3-M5 只保留已经完成的 v1 能力。**  
   原来挂在 M3-M5 下面的完整 state graph、真正 RAG、LLM judge、更多 CLI polish
   都属于后续优化，不再让 M3-M5 长期处于“标题很大但只完成一部分”的状态。

2. **M7 提前做 Prompt / Skill 质量。**  
   当前很多行为约束仍写在长 prompt 里。先整理 prompt/skill、加 regression tests，
   可以让后续 guardrails、structured state、RAG 和成本优化更稳。

3. **后续优化必须由 eval 驱动。**  
   已有 deterministic eval v1，后续每个优化都应能通过 outcome、trajectory、failure reason
   或 cost metrics 看出是否真的变好。

---

## M1：Agent Core v1

**状态**：✅ 已完成

**目标**：实现一个模型无关、任务无关的手写 ReAct agent core。

**已完成**

- [x] `core/react_loop.py`：手写 Think → Act → Observe loop。
- [x] `core/types.py`：模型无关的 Message / Tool / ToolResult 类型。
- [x] `core/llm_client.py`：Anthropic 与 OpenAI-compatible provider 适配。
- [x] `core/trace.py`：JSONL trace。
- [x] `core/context.py`：基础 token 估算与 compaction 框架。
- [x] `tools/_common.py` `safe_resolve()`：限制 target project 文件访问边界。
- [x] fs / shell / git / npm 基础工具。
- [x] `analyze` 真实端到端运行：曾对 `chai-like` 生成依赖升级风险报告。

**后续优化位置**

- context / token / cache 相关工作移到 M9。
- 更严格 runtime guardrails 移到 M8。

---

## M2：单依赖升级 v1

**状态**：✅ 已完成

**目标**：让 agent 能对一个目标依赖完成 baseline → research → version change → verify 的真实升级闭环。

**已完成**

- [x] `upgrade` CLI。
- [x] 单依赖升级 prompt workflow。
- [x] baseline-first 要求写入 prompt。
- [x] 测试失败作为普通 tool output 返回，支持模型诊断。
- [x] `chai-like` baseline 已确认：28 passing / 100% coverage。
- [x] `chai-like` 真实单依赖升级跑通过。

**后续优化位置**

- “必须先 baseline”从 prompt 迁移到 runtime guardrail：见 M8。
- 结构化报告与 JSON output：见 M8 / M11。

---

## M3：批量升级与薄 LangGraph 编排 v1

**状态**：✅ 已完成

**目标**：提供批量升级入口和一个最小 LangGraph 编排示例，不追求完整多阶段图。

**已完成**

- [x] `upgrade-all` CLI：按 direct dependency 逐个升级的 workflow 已落地。
- [x] `UPGRADE_ALL` prompt：包含 baseline、queue、per-package verify、final verify。
- [x] `orchestrator/upgrade_graph.py`：薄 LangGraph workflow。
- [x] `upgrade-graph` CLI：execute → verify → self-heal → verify。
- [x] `tests/test_upgrade_graph.py`：覆盖 verify pass、verify fail → heal、heal budget 等路径。

**不再放在 M3 的内容**

- 完整 analyze → research → plan → execute → verify StateGraph。
- Pydantic graph state、结构化 plan node、结构化 verify node。
- workflow artifact 传递与收敛。

这些作为后续优化放入 M8。

---

## M4：依赖研究工具 v1

**状态**：✅ 已完成

**目标**：让 agent 在升级前能获得 npm metadata、版本跨度、候选 release/source，并能跑只读 research flow。

**已完成**

- [x] `tools/npm.py` `dependency_research`：输出 current / target / latest、major span、
  repository / homepage、candidate sources、risk hints。
- [x] `npm_view`、`npm_releases`。
- [x] `tools/changelog.py` `fetch_releases`：GitHub releases fetching。
- [x] `tools/changelog.py` `fetch_url`：读取 changelog、migration guide、docs URL。
- [x] `research-upgrade` CLI。
- [x] `BREAKING_CHANGE_RESEARCHER` prompt：只读 researcher flow。

**不再放在 M4 的内容**

- 真正 RAG：chunk、retrieval、ranking、cache。
- `ResearchBrief` 结构化输出。
- source coverage eval。

这些作为后续优化放入 M10。

---

## M5：确定性评估框架 v1

**状态**：✅ 已完成

**目标**：建立不依赖 LLM judge 的 deterministic eval harness，用来衡量 outcome 与 trajectory。

**已完成**

- [x] `evals/runner.py`：复制 target 到隔离目录，执行 case command，检查客观后置条件。
- [x] `evals/cases/chai-like-mocha-upgrade.json`：第一个真实 case 模板。
- [x] case schema：`timeout`、`env`、`setup`、`teardown`、`budgets`。
- [x] 多 case / 目录批量运行。
- [x] JSON summary output。
- [x] checks：
  - `package_json_version`
  - `command`
  - `git_diff`
  - `trace_sequence`
  - `trajectory_policy: baseline_before_mutation`
  - `trajectory_policy: single_dependency_at_a_time`
- [x] failure reason：
  - `timeout`
  - `wrong_diff`
  - `test_failed`
  - `llm_error`
  - `postcondition_failed`
  - `trajectory_violation`
  - `baseline_missing`
  - `multi_dependency_upgrade`
- [x] `tests/test_evals_runner.py`：覆盖 runner 关键行为。

**不再放在 M5 的内容**

- LLM-as-judge。
- 更多 trajectory policies。
- cost metrics / historical result artifacts。
- CLI `--json` / `--dry-run`。

这些分别放入 M8、M9、M11。

---

## M6：补测试 Workflow v1

**状态**：✅ 已完成

**目标**：让 agent 能分析测试缺口，并生成一小批可验证的测试。

**已完成**

- [x] `skills/add_tests/prompts.py`。
- [x] `analyze-coverage` CLI：只读测试缺口分析。
- [x] `generate-tests` CLI：生成测试并验证。
- [x] prompt 约束：遵守现有测试风格、先 baseline、聚焦小批量、验证 coverage。

**后续优化位置**

- add-tests 的 prompt regression 放入 M7。
- add-tests 的 eval fixtures 放入 M7 / M8。

---

## M7：Prompt / Skill 质量 v1

**状态**：✅ 已完成

**目标**：先整理 prompt/skill，减少长 prompt 带来的成本和遗忘风险，为后续 guardrails 与结构化状态打基础。

**计划**

- [x] 拆分 prompt 层级 v1：
  - 重复规则已提取为共享片段，例如 baseline rule、verify rule、minimal-change rule、
    read-only rule、source-evidence rule、test-style rule。
  - task prompt 已通过统一 helper 显式引用共享 contracts。
  - `BREAKING_CHANGE_RESEARCHER` 和 `UPGRADE` 已使用结构化 renderer，并复用 breaking-change
    research workflow section。
  - `UPGRADE_ALL` 和 `generate-tests` 已使用结构化 renderer；`generate-tests` 复用 test generation
    workflow section。
  - `analyze` 和 `analyze-coverage` 已使用结构化 renderer，并显式挂载 read-only contract。
- [x] 继续瘦身 prompt v1：
  - 已删除 `upgrade` / `upgrade-all` 中被共享 contracts 覆盖的重复 legacy rules。
  - `BASE_AGENT` 已删除工具清单，只保留全局原则。
  - task prompt 已按 section 拆分；后续更深的成本驱动瘦身移到 M9。
- [x] 清理 prompt 与 runtime 的职责边界 v1：
  - `rendering.py` 明确 prompt contracts 只渲染为文本，不做 runtime enforcement。
  - 可程序化强制的规则放入 M8 guardrails。
- [x] 增加 prompt contract tests：
  - `upgrade` prompt 必须包含 baseline / verify / minimal change。
  - `upgrade-all` prompt 必须包含 one dependency at a time。
  - `research-upgrade` prompt 必须包含 read-only / sources / verdict。
  - `generate-tests` prompt 必须包含 existing style / baseline / verify。
- [x] 为 `upgrade`、`upgrade-all`、`research-upgrade`、`generate-tests` 维护最小 eval fixtures。
- [x] 建立 prompt size baseline：
  - 已增加 prompt 字符数统计 helper，后续可用来比较瘦身前后变化。

**验收标准**

- prompt 结构更短、更分层。
- prompt contract tests 能阻止关键规则被误删。
- 现有 eval tests 全部通过。

---

## M8：结构化状态与运行时 Guardrails

**状态**：🚧 进行中

**目标**：把关键流程从“靠 prompt 遵守”升级为“程序化状态 + 可验证约束”。

**计划**

- [ ] `core/structured.py`：封装 Claude / OpenAI-compatible 的 JSON / structured output。
- [ ] 定义核心 schema：
  - `UpgradePlan`
  - `VerificationResult`
  - `AgentReport`
  - `ResearchBrief`
- [x] runtime state v1：
  - baseline 是否已跑。
  - baseline 是否 green。
- [ ] runtime state 后续：
  - 当前正在升级哪个 dependency。
  - 本轮允许修改的文件范围。
- [x] tool guardrails v1：
  - 没有 green baseline 前禁止 `write_file`、`edit_file`、`npm install` 等 mutating action。
- [ ] tool guardrails 后续：
  - 检测 dirty target，避免覆盖用户已有改动。
  - 禁止危险 revert；只允许 revert 本次修改过的文件。
- [ ] 完整 graph 优化：
  - analyze → research → plan → execute → verify → report。
  - verify fail → self-heal → verify，限制次数。
  - 每个阶段输出结构化 artifact。
- [ ] eval runner 增加 structured report check。

**验收标准**

- 关键流程违规不只靠 prompt，能被 runtime 拦截或 eval 标记。
- `upgrade-graph` 不再依赖自然语言关键词判断 pass/fail。

---

## M9：成本与上下文优化

**状态**：⏳ 未开始

**目标**：用 eval 数据驱动 token、tool call、iteration 和 wall time 的下降，同时保持成功率。

**计划**

- [ ] `read_file` per-run cache：同一文件同一 offset + limit 不重复返回全文。
- [ ] 大文件默认摘要：
  - `package-lock.json`
  - coverage report
  - 长 changelog
- [ ] 命令输出智能摘要：
  - `npm test`：保留 exit code、pass/fail summary、失败堆栈尾部。
  - `npm install`：保留 ERESOLVE、peer warnings、deprecations、package changed summary。
  - `npm outdated`：返回 parsed JSON。
- [ ] compaction 优化：
  - 降低 `DEFAULT_INPUT_BUDGET` 到 80k-100k。
  - compaction summary 保留 baseline、已改文件、失败原因、验证结论、剩余 TODO。
- [ ] eval summary 增加：
  - iterations
  - tool_calls
  - input_tokens
  - output_tokens
  - wall_time
  - compaction_count
- [ ] 为核心 case 设置预算阈值。

**验收标准**

- 核心 eval case 能输出成本指标。
- 至少一个真实升级 case 的 token 或 tool call 明显下降。

---

## M10：Research / RAG 深化

**状态**：⏳ 未开始

**目标**：把当前 source fetching 升级为可缓存、可检索、可引用的 research pipeline。

**计划**

- [ ] 增强 source discovery：
  - GitHub releases
  - CHANGELOG
  - migration guide
  - docs site
  - npm README
- [ ] 增强错误处理：
  - GitHub rate limit
  - 404
  - redirect
  - 非 Markdown / HTML 页面
  - source gap 汇报
- [ ] changelog / release notes cache：同一 package/version/source 不重复请求。
- [ ] release notes 按 version 和 heading chunk。
- [ ] keyword retrieval：
  - breaking
  - removed
  - deprecated
  - ESM / CJS
  - Node minimum
  - peer dependency
  - CLI / config
- [ ] 结合 project usage search：只有发现项目使用模式时，才把 generic breaking change 标为相关风险。
- [ ] 输出结构化 `ResearchBrief`。
- [ ] read-only research eval：
  - `research-upgrade` 不允许产生 git diff。
  - 报告必须包含实际读过的 source。
  - source 不足时必须明确降级为测试驱动验证。

**验收标准**

- research 输出能被 upgrade flow 消费。
- eval 能检查 read-only、source coverage 和 hallucination 风险。

---

## M11：CLI / UX 与集成体验

**状态**：⏳ 未开始

**目标**：让工具更适合本地反复使用和 CI 集成。

**计划**

- [ ] CLI `--json`：输出机器可读结果。
- [ ] CLI `--dry-run`：只 plan / research，不执行 mutating action。
- [ ] 长 run 进度展示优化。
- [ ] eval runner CI 用法文档。
- [ ] 多依赖显式指定：例如 `upgrade <project> "mocha, nyc"`。
- [ ] README 与 ARCHITECTURE 根据新 milestone 同步精简。

**验收标准**

- 本地和 CI 都能稳定调用 eval / upgrade 命令。
- README 不再重复 ROADMAP，只指向 canonical 文档。

---

## 建议执行顺序

1. **M7 Prompt / Skill 质量**  
   先把 prompt 分层和 contract tests 做掉，减少后续改动时的行为漂移。

2. **M8 结构化状态与运行时 Guardrails**  
   将最关键的流程约束迁移到 runtime 和 structured artifact。

3. **M9 成本与上下文优化**  
   基于 eval 输出的成本指标，减少 token 和重复 tool call。

4. **M10 Research / RAG 深化**  
   在 prompt、eval、结构化状态稳定后，再升级 research pipeline。

5. **M11 CLI / UX 与集成体验**  
   最后补齐 JSON、dry-run、CI 文档和使用体验。
