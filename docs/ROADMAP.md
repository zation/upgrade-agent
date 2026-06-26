# ROADMAP — 后续规划

> 本文件是项目路线图的唯一权威来源。README 只保留简短状态摘要并链接到这里。
> 新 session 开始时，让 agent 读 `AGENTS.md` + `docs/ARCHITECTURE.md` + 本文件即可接续。

## Milestone 状态总览

- **M1 ✅ ReAct core**：手写 ReAct loop + fs/shell/git/npm tools，已用 `chai-like`
  分析任务端到端验证。
- **M2 ✅ single-dependency upgrade**：`upgrade` CLI 已能完成真实单依赖升级；
  `chai-like` baseline 为 28 passing / 100% coverage。
- **M3 🚧 orchestration + cost control**：`upgrade-all` 已可按 direct dependency
  逐个升级；`upgrade-graph` 已有 execute → verify → heal 的薄 LangGraph workflow。
  但完整 analyze → research → plan → execute → verify graph、结构化计划节点、token
  优化仍待完成。
- **M4 🚧 research / RAG**：`dependency_research`、`fetch_releases`、`fetch_url`
  和 `research-upgrade` 已提供只读研究雏形；真正的 chunk/retrieval/cache RAG
  管道仍待完成。
- **M5 🚧 evals + CLI**：deterministic eval runner v1 已完成；批量运行、trajectory
  eval、失败分类、CLI JSON/dry-run 仍待完成。
- **M6 ✅ add-tests workflow v1**：`analyze-coverage` 和 `generate-tests` 已完成首版；
  后续质量回归放入 M10 skill regression tests。
- **M7 ⏳ reliability guardrails + structured state**：未开始。
- **M8 ⏳ cost/context optimization**：未开始。
- **M9 ⏳ research quality**：未开始。
- **M10 ⏳ skill/prompt slimming**：未开始。

## M2 完成后的关键观察（驱动后续优先级）

1. **Token 成本是最大问题**：mocha 升级跑了 31 iterations、672k input tokens。
   根因：agent 反复读 `package-lock.json`（大文件）、重复 grep、每次对话携带全部历史。
   **M3 的隐含前置**：先优化 token 消耗，再做 LangGraph 编排，否则编排后更贵。
2. **M2 证明了升级闭环可用**：baseline → research → change → verify 全跑通。
3. **LangGraph 已有薄 workflow**：`upgrade_graph.py` 已实现 execute → verify → heal，
   但还不是完整的多阶段升级图。

---

## M3：LangGraph 编排 + 自愈 + token 优化

### M3.1 token 优化（推荐先做，影响所有后续 milestone）

**目标**：把同类任务的 token 消耗降低 50%+。

- [ ] `core/context.py` 实际启用 compaction：当前 `needs_compaction()` 写好了但
  react_loop 只做检查没真正触发（实际上触发了，但 budget 150k 远不够）。需要调参：
  - `DEFAULT_INPUT_BUDGET` 降到 80k–100k（强制更早压缩）
  - compaction 时调用 LLM 生成摘要（而不是固定 stub），这样信息保留更多
- [ ] `tools/fs.py` `read_file` 加缓存：同一文件同一 offset+limit 在一次 run 内
  不重复读（返回 "cached, already read above"）
- [ ] `tools/fs.py` `read_file` 对大文件（>500 行）自动只返回前 100 行 + 提示
  "file has N lines, use offset to read more"（当前 2000 行上限太慷慨）
- [ ] `run_command` 对 `npm test` 的输出做智能截断：保留头部（启动信息）和尾部
  （测试结果行），中间省略——当前已经 head+tail 但阈值 8000 chars 对 npm test
  还是太大

### M3.2 LangGraph StateGraph 编排

**目标**：把"分析→研究→计划→执行→验证"建模为状态图。

- [x] `src/upgrade_dependencies_agent/orchestrator/upgrade_graph.py`：薄 workflow 已完成：
  execute → verify → self-heal → verify。
- [x] `upgrade-graph` CLI：调用 `UpgradeGraphRunner`，为单依赖升级增加独立 verify
  和有限 self-heal。
- [ ] `src/upgrade_dependencies_agent/orchestrator/state.py`：定义完整图状态（pydantic model）
  - `project_profile: str` — analyze 节点的产出
  - `upgrade_plan: UpgradePlan` — plan 节点的结构化输出
  - `changes_made: list[str]` — execute 节点的变更记录
  - `test_result: TestResult` — verify 节点的结果
  - `iterations: int` — 自愈循环计数
- [ ] `src/upgrade_dependencies_agent/orchestrator/graph.py`：完整 StateGraph 定义
  ```
  analyze → research → plan → execute → verify
                                        ↓ (pass) → report (END)
                                        ↓ (fail) → self_heal → verify (loop, max 3x)
  ```
- [ ] `orchestrator/nodes/analyze.py`：调 ReActLoop（read-only tools），输出项目画像
- [ ] `orchestrator/nodes/research.py`：调 ReActLoop + npm/changelog 工具，输出破坏性变更
- [ ] `orchestrator/nodes/plan.py`：单次 LLM 调用 + pydantic structured output，
  输出分步升级计划（每步：文件、改动、风险等级）
- [ ] `orchestrator/nodes/execute.py`：调 ReActLoop（full tools），执行计划
- [ ] `orchestrator/nodes/verify.py`：跑 `npm test`，解析输出为 TestResult
- [ ] `orchestrator/nodes/self_heal.py`：读测试报错 → 调 ReActLoop 修代码 → 返回 verify

### M3.3 Structured Output（计划节点需要）

- [ ] `src/upgrade_dependencies_agent/core/structured.py`：封装 Claude/OpenAI 的 structured
  output / JSON mode，输入 pydantic BaseModel，返回验证过的实例
- [ ] `UpgradePlan` schema：steps 数组，每步有 file / change_type / description / risk
- [ ] `TestResult` schema：exit_code / passing / failing / output_excerpt

---

## M4：RAG + 研究子 agent

**当前状态**：`dependency_research` 已能输出 npm metadata、version span、candidate
release-note sources；`research-upgrade` 已作为只读 breaking-change researcher
入口存在。下一步是把"能 fetch source"升级成可缓存、可检索、可复用的 RAG brief。

### M4.1 changelog/realease-notes 抓取工具

- [x] `tools/npm.py` `dependency_research`：输出 current/target/latest、major span、
  repository/homepage、candidate changelog/release URLs、risk hints。
- [x] `tools/changelog.py` `fetch_releases`：通过 GitHub API 获取 release notes。
- [x] `tools/changelog.py` `fetch_url`：读取 changelog、migration guide、docs URL。
- [ ] 增强错误处理：GitHub rate limit、页面不存在、格式异常、source gap 汇报。
- [ ] `fetch_changelog(package, from_ver, to_ver)`：按 package/version span 聚合 release notes。

### M4.2 简单 RAG 管道

- [ ] `tools/rag.py`：
  - 对抓到的 release notes 做简单切分（按 section heading）
  - 关键词检索（先用 keyword matching，不用向量库）
  - 返回与查询相关的片段 + 来源链接
- [ ] 积累 changelog 缓存目录 `data/changelogs/`，避免重复抓取

### M4.3 研究子 agent

- [x] `research-upgrade` CLI：只读 breaking-change researcher flow。
- [x] `BREAKING_CHANGE_RESEARCHER` prompt：要求确认版本、读 source、搜索项目 usage、
  输出 relevant breaking changes 和 verdict。
- [ ] `skills/research.py`：将 researcher 从 prompt 常量沉淀为独立 skill/module。
- [ ] 在完整 M3 graph 里，`research` 节点调用 researcher 并输出结构化 `ResearchBrief`。

---

## M5：评估 + CLI 体验

### M5.1 评估框架

- [x] `evals/runner.py`：deterministic eval v1。复制 target 到隔离目录，运行 case command，
  再检查客观后置条件（package version、test command、git diff allowed paths）。
- [x] `evals/cases/chai-like-mocha-upgrade.json`：第一个真实 case 模板，覆盖 mocha 4→11。
- [x] `tests/test_evals_runner.py`：覆盖成功/失败 check，并确认不会修改原 target。
- [x] 扩展 case schema：加入 `timeout`、环境变量、setup/teardown、expected token/iteration budget。
- [x] 支持多 case 批量运行：输出 JSON summary，适合 CI 或本地回归。
- [x] 加通用 `trace_sequence` check：从 JSONL trace 判断关键 tool/command 是否按顺序出现。
- [x] 增加 `baseline_before_mutation` trajectory policy：判断是否先 baseline，再进行
  install/edit 等 mutating action；失败分类为 `baseline_missing`。
- [x] 增加 `single_dependency_at_a_time` trajectory policy：检测一次 `npm install`
  是否包含多个 package target；失败分类为 `multi_dependency_upgrade`。
- [ ] 增加更多专用 trajectory policies：判断是否读取真实失败输出、是否产生越权/无关修改。
- [x] 基础失败分类：`test_failed`、`wrong_diff`、`timeout`、`llm_error`、
  `trajectory_violation`、`postcondition_failed` 等，方便比较优化前后。
- [x] 在专用 trajectory policies 中补充 `baseline_missing` 流程违规分类。
- [ ] 可选 LLM-as-judge：只评价报告质量、风险解释、source coverage；核心 pass/fail 仍靠脚本。

### M5.2 CLI 打磨

- [ ] `--json` flag：输出 JSON 格式的结果（CI 集成用）
- [ ] `--dry-run` flag：只 plan 不 execute（read-only 模式预览）
- [ ] 进度条 / spinner：长 run 时给用户反馈
- [ ] 多依赖批量升级：`upgrade chai-like "mocha, nyc"`

---

## M6：补测试技能

**当前状态**：add-tests workflow v1 已完成。后续不再单独扩展 M6，而是在 M5/M10
中用 eval 和 prompt regression 保护它。

### M6.1 analyze-coverage 分析技能

- [x] `skills/add_tests/prompts.py`：补测试专用 system prompt
- [x] 先分析哪些模块/函数缺少测试（读源码 + 读覆盖率报告）
- [x] 输出"测试缺口列表"：每个缺口有 file / function / 建议的测试场景
- [x] CLI 新增 `analyze-coverage` 只读入口

### M6.2 generate-tests 执行技能

- [x] 对每个缺口，用 ReActLoop 生成测试代码
- [x] 写入 `test/*.test.js`（遵守项目现有测试风格）
- [x] 跑 `npm test` 验证新测试通过且覆盖率提升
- [x] CLI 新增 `generate-tests` 执行入口

---

## M7：可靠性 guardrails + 结构化状态

**目标**：把"靠 prompt 约束"升级为"程序化约束 + 可验证状态"，降低 agent 忘步骤或误报成功的概率。

### M7.1 结构化结果与状态

- [ ] `core/structured.py`：封装 Claude/OpenAI-compatible 的 JSON/structured output，
  输入 pydantic schema，返回验证过的对象。
- [ ] 定义 `UpgradePlan`、`VerificationResult`、`AgentReport`：
  - `UpgradePlan`: dependency / from_version / to_version / steps / risk / expected files
  - `VerificationResult`: command / exit_code / passing / failing / output_excerpt / verdict
  - `AgentReport`: changes / tests / warnings / blocked_reason
- [ ] `upgrade-graph` 的 verify 节点优先消费 `VerificationResult`，减少关键词启发式。
- [ ] eval runner 增加 structured report check：如果 CLI 输出 JSON，直接检查字段，而不是读自然语言。

### M7.2 执行 guardrails

- [ ] 在 tool/runtime 层记录 run state：baseline 是否已跑、baseline 是否 green、当前正在升级哪个包。
- [ ] 没有 green baseline 前，禁止 `write_file`、`edit_file`、`npm install` 等 mutating action。
- [ ] `upgrade-all` 中检测一次只升级一个 direct dependency；发现 package.json 同时改多个 direct dep 时标记失败。
- [ ] 禁止危险 revert：把 prompt 中的 `git reset --hard` 改成"只 revert 本次修改过的文件"，并用工具层保护。
- [ ] 运行前检测 dirty target：如果 target 已有未提交改动，要求 agent 报告并避免覆盖用户改动。

### M7.3 Workflow 收敛

- [ ] 将 `upgrade`、`upgrade-all`、`generate-tests` 的关键阶段映射成状态机：
  baseline → plan/research → execute → verify → report。
- [ ] 每个阶段输出结构化 artifact，后续阶段只消费 artifact + 必要上下文，减少全历史依赖。
- [ ] self-heal 限定为"基于 verify failure 的最小修复"，每次 heal 后必须重新 verify。

---

## M8：成本与上下文优化

**目标**：以 eval 数据为准，把真实 upgrade run 的 token、tool call、iteration 降下来，同时保持成功率。

### M8.1 文件读取与命令输出优化

- [ ] `read_file` 加 per-run cache：同一文件同一 offset+limit 不重复返回全文。
- [ ] 大文件默认摘要：`package-lock.json`、coverage report、长 changelog 首次只返回结构摘要和可分页提示。
- [ ] `run_command` 对常见命令做智能摘要：
  - `npm test`: 保留命令、exit code、passing/failing summary、失败堆栈尾部
  - `npm install`: 保留 ERESOLVE、peer warnings、deprecations、package changed summary
  - `npm outdated`: 尽量返回 parsed JSON 而非原始噪声
- [ ] grep/glob 支持 ignore 配置和更强 caps，避免 node_modules、coverage、lockfile 噪声进入上下文。

### M8.2 Memory / compaction

- [ ] 降低 `DEFAULT_INPUT_BUDGET` 到 80k-100k，让 compaction 更早触发。
- [ ] compaction 时生成任务摘要：保留 baseline、已改文件、失败原因、验证结论、剩余 TODO。
- [ ] trace 中记录 compaction 前后 token 估算，eval summary 统计 compaction 次数和节省量。

### M8.3 成本回归指标

- [ ] eval summary 输出 `iterations`、`tool_calls`、`input_tokens`、`output_tokens`、`wall_time`。
- [ ] 为核心 case 设置预算阈值，例如 mocha upgrade 不超过 N iterations / N tokens。
- [ ] 建一个小的历史结果文件或 CI artifact，用于比较优化前后成功率和成本。

---

## M9：RAG / dependency research 质量提升

**目标**：让研究阶段既少读无关 changelog，又能给升级阶段稳定提供"和本项目有关"的 breaking-change 证据。

### M9.1 Source discovery 与缓存

- [ ] `dependency_research` 输出更可靠的 source candidates：GitHub releases、CHANGELOG、migration guide、
  docs site、npm README。
- [ ] `data/changelogs/` 或 per-run cache：同一 package/version/source 不重复请求。
- [ ] 处理 GitHub rate limit、404、重定向、非 Markdown/HTML 页面，并在报告里说明 source gap。

### M9.2 Retrieval 与 relevance

- [ ] release notes / changelog 按版本和 heading chunk。
- [ ] 先用 keyword retrieval：breaking、removed、deprecated、require、ESM、Node、peer、CLI/config。
- [ ] 结合 project usage search：只有找到项目使用模式时，才把 generic breaking change 标成"相关风险"。
- [ ] research 输出结构化 `ResearchBrief`：version span、relevant breaking changes、project usages、
  source URLs、confidence。

### M9.3 Research eval

- [ ] 增加 read-only eval case：`research-upgrade` 不允许产生 git diff。
- [ ] 对 known package upgrades 建 source coverage check：报告必须包含实际读过的 changelog/release source。
- [ ] 对"没有找到可靠 changelog"的情况，要求明确降级为测试驱动验证，不允许伪造 breaking change。

---

## M10：Skill / prompt 体系瘦身

**目标**：减少长 prompt 带来的成本和遗忘风险，把稳定规则下沉到代码和 schema，把 prompt 留给任务策略。

### M10.1 Prompt 分层

- [ ] `BASE_AGENT` 只保留全局原则：调查、最小修改、验证、清晰报告。
- [ ] 各 task prompt 只描述本任务阶段、输出 schema、特殊规则。
- [ ] 把重复规则提取成共享片段，例如 baseline rule、verify rule、report rule。

### M10.2 Prompt 与 guardrail 分工

- [ ] 删除或弱化可以由工具层强制的规则，避免 prompt 又长又重复。
- [ ] 把"必须先 baseline"、"只能改 allowed paths"、"不能危险 revert"迁移到 runtime/eval。
- [ ] prompt 中保留"为什么要这么做"和异常时如何报告。

### M10.3 Skill regression tests

- [ ] 增加 prompt snapshot/contract tests：确保关键规则和输出字段仍存在。
- [ ] 用 eval cases 对比 prompt 改短前后的成功率、成本、违规率。
- [ ] 对 `upgrade`、`upgrade-all`、`generate-tests` 分别维护一组最小 fixture。

---

## 建议的执行顺序

```
M5.1 v1 已完成 → M5.1 扩展批量/trajectory eval
  → M7.1 structured output/state → M7.2 guardrails
    → M8 成本与上下文优化
      → M9 RAG/research 质量提升
        → M10 prompt/skill 瘦身
          → M5.2 CLI 打磨
```

理由：先把 eval 扩展成能稳定衡量 outcome + trajectory + cost 的工具，再做
structured output 和 guardrails，让后续优化有客观反馈；成本优化和 RAG 质量提升都
应该用 eval 数据验证；最后再瘦身 skill/prompt，避免在没有回归保护时改坏 agent 行为。
