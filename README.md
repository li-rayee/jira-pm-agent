# Jira 智能项目管家 · AI 辅助风险监控

> 面向敏捷团队的**对话式项目管理助手**：连接 Jira REST API，自动做风险扫描 / 周报生成 / 负载查询。
> 核心设计 = **规则引擎（确定性判断）前置过滤 + 大模型（归因与措辞）后置加工**，体现「人机协同」。
> 编排层用 **WorkBuddy 智能体**实现（替代 Coze/Dify）：你直接在对话里问，智能体调用本仓库脚本完成取数、算风险、生成分析，无需注册第三方平台、无需拖画布、无需额外大模型 API Key。

---

## 为什么这样设计

直接把原始 issue 丢给 AI 让它"自己判断风险"会误报（例如把"进行中"判成"延期"）。
本方案先用 **6 类确定性规则**筛出客观风险，再让大模型做归因与建议——**机器算、人定标准、AI 润色**。

- **机器（规则引擎）**：做确定性的事实计算（逾期、超期未动、反复流转等）。
- **人（你）**：定义风险判定阈值（5 天、8 点、5 个任务……按团队实际速率调）。
- **AI（大模型）**：对风险做归因分析与措辞润色，并受"禁止编造"约束。

---

## 架构（WorkBuddy 编排）

```
① 对话入口   在 WorkBuddy 对话里问："本周有什么风险？"
② 智能体编排  WorkBuddy 直接调用本仓库脚本：取数 → 规则引擎 → 大模型分析 → 回复
③ 数据获取   Jira Cloud REST API（API Token 认证，新版 `/search/jql` 端点）
④ 大模型     WorkBuddy 内置模型（风险归因 + 周报生成，受"禁止编造"约束）
```

原方案用 Coze/Dify 做"胶水"，本仓库改用 **WorkBuddy 智能体**承担同样的对话入口 + 工作流编排 + 大模型三层职责；
规则引擎与 Jira 客户端（`risk_engine.py` / `jira_client.py`）是不变的"内核"，只是编排者从 Coze 换成 WorkBuddy。
好处：免第三方注册、免可视化画布、免单独大模型 Key；**对话即调用**，代码已在本仓库跑通验证。

> 若你仍想要一个"团队可访问的独立 Bot"，也可回退到 Coze/Dify —— 思路与 `workbuddy_setup.md` 一致，本仓库逻辑可整体复用。

---

## 快速开始（本地，零第三方依赖）

```bash
# 0) 放好凭证：在项目根目录创建 .env（已被 .gitignore 排除，绝不提交）
#    JIRA_SITE=https://你的站点.atlassian.net
#    JIRA_EMAIL=你创建 Token 的邮箱
#    JIRA_TOKEN=ATATT3xF...        # Jira 后台 → 账户设置 → 安全 → 创建 API Token
#    （脚本会自动读取 .env；也可临时用 export 覆盖）

# 1) 跑规则引擎（用仿真 fixture 验证 6 类规则覆盖）
python risk_engine.py mock_jira_data.json --today 2026-10-18
#    → 输出风险清单 JSON + report.md（命中 11 条，6 类规则全部触发，干净样本零误报）

# 2) 连接【真实 Jira Cloud】并直接跑（凭证走 .env 或环境变量）
python run_real.py "" --out real_report.md
#    → 自动拉取账号下全部项目，归一化后跑引擎，输出 real_report.md

# 2.5) 往真实 Jira 写入覆盖 5 类风险规则的 demo issue（可选，用于现场演示）
python demo_seed.py                       # 清理旧 [Demo] 后重建 9 条覆盖风险的 issue
python run_real.py "project = SCRUM" --out real_report.md
#     → 命中 8 条：逾期2 / 临近到期2 / 负载失衡1 / 反复流转1 / 故事点过大2（高4/中2/低2）

# 3) 组装提示词（设置 API Key 后真实调用大模型；否则只打印 prompt）
#    注：在 WorkBuddy 中可跳过此步，直接用内置模型做归因/周报
export DEEPSEEK_API_KEY="你的key"     # 或 OPENAI_API_KEY
python prompts.py
```

- 依赖：Python 3.8+，仅标准库（`json` / `datetime` / `urllib`）
- **仿真验证（fixture）**：`mock_jira_data.json` 20 条，命中 11 条风险（高 4 / 中 5 / 低 2），6 类规则全部触发，**干净样本零误报**。
- **真实接入验证**：已直连作者 Jira Cloud 站点（`/search/jql` 端点），成功拉取 issue、完成字段映射（中文状态 / `customfield_10016` 故事点 / `progress` 对象 / 从 `changelog` 统计状态变更），在 4 条真实 issue 上**零误报**，证明端到端链路与"规则前置不误报"成立。

---

## 目录结构

| 文件 | 作用 |
|---|---|
| `risk_engine.py` | 规则引擎（6 类风险），阈值集中在 `RULE_CONFIG`，支持中英文状态名 |
| `prompts.py` | 两套提示词（风险归因 + 周报）+ 大模型调用占位（`urllib` 调 DeepSeek/通义，无 key 只打印） |
| `jira_client.py` | 真实 Jira Cloud REST 客户端（认证 + `/search/jql` 拉取 + 字段映射辅助），自动读取 `.env` 凭证 |
| `jira_adapter.py` | 把真实 Jira issue 归一化为引擎输入（从 `changelog` 算状态变更、`progress` 算百分比） |
| `run_real.py` | 「拉真实数据 → 归一化 → 跑引擎 → 出报告」一键脚本 |
| `mock_jira_data.json` | 仿真 Jira 返回 fixture（20 条，含 6 类风险样本） |
| `report.md` / `real_report.md` | 仿真 / 真实数据验证文本报告 |
| `demo_seed.py` | 往真实 Jira（SCRUM 项目）写入覆盖 5 类风险规则的 demo issue（可重复运行，用于现场演示） |
| `explore_meta.py` | 探测项目元数据（accountId / issuetype / 可用状态流转），辅助接入 |
| `workbuddy_setup.md` | **WorkBuddy 用法**：对话式调用 + 每日自动化配置 + 三段提示词原文 |

---

## 风险判定规则（阈值可配置）

| 规则 | 判定条件 | 严重度 |
|---|---|---|
| 逾期 | 状态未完成 且 due < 今天 | 高 |
| 长期未更新 | 最近状态变更 > 5 天前 | 中 |
| 反复流转 | 状态变更次数 > 5 | 中 |
| 故事点过大 | story_points > 8 | 低 |
| 负载失衡 | 某人进行中任务 > 5 个 | 中 |
| 临近到期未完成 | due 在 3 天内且进度 < 50% | 高 |

- 状态名做了中英文兼容（`done_statuses` / `in_progress_statuses`），适配中文 Jira。
- 阈值在 `risk_engine.py` 的 `RULE_CONFIG` 中集中管理，按团队实际速率调整。

---

## WorkBuddy 接入

见 **`workbuddy_setup.md`**：两种用法（A. 直接在 WorkBuddy 对话里问；B. 配置每日自动化定时扫描）。
其中风险归因 / 周报 / 负载查询三段提示词原文见该文档，可由 WorkBuddy 直接套用 `prompts.py` 或自行生成。
规则引擎逻辑在 `risk_engine.py`，可被智能体直接调用。

---

## 数据来源说明（诚实标注，写简历/面试请据此表述）

- **规则覆盖验证**：用 `mock_jira_data.json`（仿真 fixture）驱动引擎，覆盖全部 6 类风险且零误报 —— 证明规则逻辑正确。
- **真实接入验证**：已直连真实 Jira Cloud（作者站点），端到端跑通并零误报 —— 证明 API 集成、字段映射、changelog 状态变更统计均有效。
- **真实 Demo 数据现场演示**：用 `demo_seed.py` 在真实 Jira 写入 9 条覆盖风险的 `[Demo]` issue（其中 8 个挂在同一人进行中任务以触发负载失衡，1 条做 7 次状态跳转以触发反复流转），再用 `run_real.py` 真实跑批，**命中 8 条风险、覆盖 5 类规则**（逾期2 / 临近到期2 / 负载失衡1 / 反复流转1 / 故事点过大2，高4/中2/低2）。
- **关于「长期未更新」规则（诚实说明）**：该规则判定"最近状态变更 > 5 天前"，依赖历史时间戳，新建 demo 数据无法真实制造；因此本仓库用仿真 fixture（`mock_jira_data.json`）完整覆盖该规则（mock 共命中 11 条、6 类全触发）。真实环境中它会在历史数据上正常生效。
- 若要在简历写"已接入真实团队数据并调参"，需在真实迭代跑满一个周期后，用 `run_real.py` 实测替换本仓库的占位结论。

---

## 免责声明

- 本项目用于**学习 / 作品集展示**。仿真 `mock_jira_data.json` 为非生产数据。
- 真实 Jira 接入：`lastStatusChange` 与 `statusChangeCount` 由 `jira_adapter.py` 从 `changelog.histories` 统计；`progress` 由 `progress` 对象换算百分比。
- **密钥安全**：`JIRA_TOKEN` 等只通过 `.env` 或环境变量传入，绝不写进代码或提交仓库（见 `.gitignore`）。本地运行与 WorkBuddy 自动化均读取项目根目录 `.env`。
- 大模型输出受"禁止编造 / 需人工确认"约束；本项目不构成任何生产系统保障。
