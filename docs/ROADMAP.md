# ROADMAP — 后续规划

> M1 ✅ M2 ✅ 已完成。以下是 M3–M6 的详细拆分，每个 item 可独立作为一个 task/session 执行。
> 新 session 开始时，让 agent 读 `AGENTS.md` + `docs/ARCHITECTURE.md` + 本文件即可接续。

## M2 完成后的关键观察（驱动后续优先级）

1. **Token 成本是最大问题**：mocha 升级跑了 31 iterations、672k input tokens。
   根因：agent 反复读 `package-lock.json`（大文件）、重复 grep、每次对话携带全部历史。
   **M3 的隐含前置**：先优化 token 消耗，再做 LangGraph 编排，否则编排后更贵。
2. **M2 证明了升级闭环可用**：baseline → research → change → verify 全跑通。
3. **LangGraph 依赖已装但未用**：pyproject.toml 里有 `langgraph`，但还没写任何代码。

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

- [ ] `src/refactor_agent/orchestrator/state.py`：定义图状态（pydantic model）
  - `project_profile: str` — analyze 节点的产出
  - `upgrade_plan: UpgradePlan` — plan 节点的结构化输出
  - `changes_made: list[str]` — execute 节点的变更记录
  - `test_result: TestResult` — verify 节点的结果
  - `iterations: int` — 自愈循环计数
- [ ] `src/refactor_agent/orchestrator/graph.py`：StateGraph 定义
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
- [ ] CLI 新增 `--orchestrated` flag，或新命令 `upgrade-graph`，用 StateGraph 替代
  直接 ReActLoop

### M3.3 Structured Output（计划节点需要）

- [ ] `src/refactor_agent/core/structured.py`：封装 Claude/OpenAI 的 structured
  output / JSON mode，输入 pydantic BaseModel，返回验证过的实例
- [ ] `UpgradePlan` schema：steps 数组，每步有 file / change_type / description / risk
- [ ] `TestResult` schema：exit_code / passing / failing / output_excerpt

---

## M4：RAG + 研究子 agent

### M4.1 changelog/realease-notes 抓取工具

- [ ] `tools/changelog.py`：
  - `fetch_changelog(package, from_ver, to_ver)` — 从 GitHub releases page 抓
    release notes（用 httpx + HTML 解析）
  - `fetch_github_releases(owner, repo)` — GitHub API 获取 releases 列表
- [ ] 错误处理：GitHub rate limit、页面不存在、格式异常

### M4.2 简单 RAG 管道

- [ ] `tools/rag.py`：
  - 对抓到的 release notes 做简单切分（按 section heading）
  - 关键词检索（先用 keyword matching，不用向量库）
  - 返回与查询相关的片段 + 来源链接
- [ ] 积累 changelog 缓存目录 `data/changelogs/`，避免重复抓取

### M4.3 研究子 agent

- [ ] `skills/research.py`：一个独立的 ReActLoop（用 changelog + rag 工具），
  专门做"给定 package A 从 v1→v5，找出影响这个项目的破坏性变更"
- [ ] 在 M3 的 LangGraph 里，`research` 节点调用这个子 agent

---

## M5：评估 + CLI 体验

### M5.1 评估框架

- [ ] `evals/cases/chai-like-mocha-upgrade.yaml`：固定的评估任务定义
  ```yaml
  name: "mocha 4→11 upgrade"
  target: "../chai-like"
  task: "Upgrade mocha from 4.x to latest stable"
  success_criteria:
    - mocha_version: ">=11.0.0"
    - tests_passing: 28
    - tests_failing: 0
  ```
- [ ] `evals/runner.py`：跑评估 case → 对比结果 vs golden → 输出 pass/fail
- [ ] `evals/golden/chai-like-mocha-upgrade.json`：期望结果

### M5.2 CLI 打磨

- [ ] `--json` flag：输出 JSON 格式的结果（CI 集成用）
- [ ] `--dry-run` flag：只 plan 不 execute（read-only 模式预览）
- [ ] 进度条 / spinner：长 run 时给用户反馈
- [ ] 多依赖批量升级：`upgrade chai-like "mocha, nyc"`

---

## M6：补测试技能

### M6.1 analyze-coverage 分析技能

- [x] `skills/add_tests/prompts.py`：补测试专用 system prompt
- [x] 先分析哪些模块/函数缺少测试（读源码 + 读覆盖率报告）
- [x] 输出"测试缺口列表"：每个缺口有 file / function / 建议的测试场景
- [x] CLI 新增 `analyze-coverage` 只读入口

### M6.2 generate-tests 执行技能

- [x] 对每个缺口，用 ReActLoop 生成测试代码
- [ ] 写入 `test/*.test.js`（遵守项目现有测试风格）
- [x] 跑 `npm test` 验证新测试通过且覆盖率提升
- [x] CLI 新增 `generate-tests` 执行入口

---

## 建议的执行顺序

```
M3.1 (token优化) → M3.3 (structured output) → M3.2 (LangGraph编排)
  → M4.1 (changelog工具) → M4.2 (RAG) → M4.3 (子agent)
    → M5.1 (评估) → M5.2 (CLI)
      → M6.1 → M6.2
```

理由：token 优化是全局受益的，先做；structured output 是 LangGraph plan 节点
的前置；LangGraph 是后续所有编排的基础；RAG/子agent 依赖编排框架；评估依赖
前面的稳定功能；补测试是最后一个独立技能。
