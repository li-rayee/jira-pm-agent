# Coze / Dify 工作流配置指南（Jira 智能项目管家）

本仓库提供的不依赖 Coze 的部分：`mock_jira_data.json`（仿真数据）、`risk_engine.py`（规则引擎）、`prompts.py`（提示词）、`report.md`（验证产出）。
本文档告诉你**在 Coze/Dify 画布上怎么把这条链路拖出来**。照着做即可，无需写胶水代码。

---

## 0. 通用准备（三个工作流共用）

| 事项 | 做法 |
|---|---|
| **Jira API Token** | Jira 后台 → 账户设置 → 安全 → 创建 API Token。**存入 Coze「变量/密钥管理」，变量名 `jira_token`，不要硬编码进节点。** |
| **站点地址** | 你自己的 `https://<你的站点>.atlassian.net` |
| **HTTP 节点通用配置** | Method: `GET`；Headers: `Authorization: Bearer {{jira_token}}`，`Accept: application/json` |
| **JQL 示例** | `sprint = "Sprint 23" AND status != Done`（按实际迭代名改） |
| **大模型节点** | 选 DeepSeek-V3 或通义千问；Temperature 0.3；详见第 6 节提示词 |

> Dify 用户：节点名略有差异——「HTTP 请求」同名，「代码」节点同名，「大模型」节点同名，「意图识别」对应 Dify 的「问题分类器」节点。

---

## 1. 工作流 A：风险扫描（用户问"本周有什么风险"）

### 1.1 节点链路（对照流程图）

| # | 节点类型（Coze 名称） | 关键参数 / 内容 | 输入 | 输出变量 | 下一节点 |
|---|---|---|---|---|---|
| 1 | 开始 | 触发方式：用户消息进入 | — | `user_query` | 2 |
| 2 | 意图识别 / 选择器 | 识别为「风险扫描」意图（见下方说明） | `user_query` | `intent` | 3 |
| 3 | HTTP 请求 | URL: `https://<站点>.atlassian.net/rest/api/3/search?jql=sprint="Sprint 23" AND status != Done`；Header 带 Token | — | `jira_response` | 4 |
| 4 | 代码节点（Python） | 粘贴第 5 节规则引擎代码 | `jira_response.issues` | `risk_list` | 5 |
| 5 | 大模型节点 | System/User 见第 6 节「风险归因」模板；把 `risk_list` 拼进 user | `risk_list` | `analysis` | 6 |
| 6 | 结束 | 回复变量 `analysis` | `analysis` | — | — |

### 1.2 意图识别怎么接
- **推荐（零代码）**：在 Coze Bot 内建 3 条独立工作流，分别命名 `risk_scan` / `weekly_report` / `load_query`，用 Coze 的「工作流触发条件」或「意图识别」把"本周有什么风险"之类的问题路由到 `risk_scan`。这样工作流内部不需要选择器，链路就是上表 1→3→4→5→6（跳过第 2 步）。
- **单工作流方案**：若想放一条工作流里，开头加一个「大模型」节点做意图分类（输出 `intent` 字段），再用「选择器」节点按 `intent` 分支到不同子链。

---

## 2. 工作流 B：周报生成（用户问"生成本周周报"）

与工作流 A 几乎相同，**仅 3 处不同**：
- 意图/触发条件改为「周报生成」。
- HTTP 节点的 JQL 改为拉取本迭代全部未 Done + 本周期有更新的 issue（如 `sprint = "Sprint 23" AND updated >= -7d`）。
- 第 5 步大模型节点改用第 6 节「周报生成」模板，并额外传入迭代名/周期等元信息。

其余节点（开始、HTTP、代码节点、结束）配置与 A 一致。

---

## 3. 工作流 C：负载查询（用户问"张三现在手上几个任务"）

| # | 节点类型 | 关键参数 | 输出变量 |
|---|---|---|---|
| 1 | 开始 | 触发：负载查询意图 | `user_query` |
| 2 | HTTP 请求 | JQL: `assignee = "<从问题里提取的名字>" AND status = "In Progress"`（提取名字可用一个「大模型」节点先做 NER，或直接让用户在工作流变量里填） | `jira_response` |
| 3 | 代码节点 | 统计 `jira_response.issues` 长度 + 列出 key | `count`, `keys` |
| 4 | 大模型 / 直接回复 | 模板：「{name} 当前进行中任务 {count} 个：{keys}」 | 回复 |

> 这条链路最短，可作为你**第一次跑通 Coze** 的练手工作流。

---

## 4. 关键设计：规则前置，AI 后置

代码节点（规则引擎）必须放在大模型节点**之前**。原因：直接把原始 issue 丢给 AI 让其"自己判断风险"会误报（如把"进行中"判为"延期"）。先用确定性规则筛客观事实，再让 AI 做归因与措辞——这就是 JD 要的「人机协同」。

---

## 5. Coze 代码节点（Python，可直接粘贴）

Coze 代码节点支持 Python。把下面函数粘贴进去，声明输入参数 `issues`（上游 HTTP 节点返回的 `jira_response.issues`），返回 `risk_list`。这是 `risk_engine.py` 的**精简自包含版**，零依赖。

```python
from datetime import date, timedelta

CFG = {
    "overdue_days": 0, "stale_days": 5, "churn_count": 5,
    "big_story_points": 8, "load_threshold": 5,
    "due_soon_window": 3, "due_soon_progress": 50, "done_status": "Done",
}
SEV = {"高": 3, "中": 2, "低": 1}

def _date(v):
    if not v: return None
    try: return date.fromisoformat(str(v)[:10])
    except: return None

def main(issues):
    today = date.today()
    risks = []
    wip = {}
    for it in issues:
        f = it.get("fields", {})
        key = it.get("key")
        st = (f.get("status", {}) or {}).get("name", "").strip()
        due = _date(f.get("duedate"))
        last = _date(f.get("lastStatusChange"))
        churn = int(f.get("statusChangeCount") or 0)
        pts = float(f.get("storyPoints") or 0)
        prog = float(f.get("progress") or 0)
        who = (f.get("assignee", {}) or {}).get("displayName") or "未分配"
        if st == "In Progress":
            wip.setdefault(who, []).append(key)
        if st != CFG["done_status"] and due and due < today:
            risks.append({"issue_key": key, "rule": "逾期", "severity": "高",
                "detail": f"{st}未完成，到期 {due} 已过期 {(today-due).days} 天"})
        if last and (today - last).days > CFG["stale_days"]:
            risks.append({"issue_key": key, "rule": "长期未更新", "severity": "中",
                "detail": f"最近变更 {last}，已 {(today-last).days} 天未动"})
        if churn > CFG["churn_count"]:
            risks.append({"issue_key": key, "rule": "反复流转", "severity": "中",
                "detail": f"状态变更 {churn} 次，需求可能不清晰或阻塞"})
        if pts > CFG["big_story_points"]:
            risks.append({"issue_key": key, "rule": "故事点过大", "severity": "低",
                "detail": f"故事点 {pts:.0f}，建议拆分"})
        if st != CFG["done_status"] and due and today <= due <= today+timedelta(days=CFG["due_soon_window"]) and prog < CFG["due_soon_progress"]:
            risks.append({"issue_key": key, "rule": "临近到期未完成", "severity": "高",
                "detail": f"到期 {due}（{(due-today).days} 天内），进度 {prog:.0f}%"})
    for who, keys in wip.items():
        if len(keys) > CFG["load_threshold"]:
            risks.append({"issue_key": None, "assignee": who, "rule": "负载失衡",
                "severity": "中", "detail": f"{who} 进行中 {len(keys)} 个：{', '.join(keys)}"})
    risks.sort(key=lambda r: (-SEV.get(r["severity"], 0), r.get("issue_key") or ""))
    return {"risk_list": risks, "risk_count": len(risks)}
```

> 真实 Jira 里 `lastStatusChange` 和 `statusChangeCount` 需从 `changelog.histories` 统计；`progress` 可用子任务完成比例或自定义字段替代。在 HTTP 节点后可加一个预处理代码节点算好这两个值，再喂给上面的逻辑。

---

## 6. 大模型节点提示词（与 prompts.py 一致）

### 6.1 风险归因分析（工作流 A 第 5 步）
**System：**
```
你是项目风险分析师，服务于敏捷软件开发团队。
你的输入是「规则引擎」预先筛选出的结构化风险清单（JSON）。
规则引擎已完成客观判定，你只负责三件事：归因、建议、措辞。
硬性约束：
1. 只基于给定数据回答，绝不编造 issue、负责人、日期或数字。
2. 若某条风险的数据不足以判断归因，必须明确写「需人工确认」，不要猜测。
3. 严重度（高/中/低）以风险清单中的字段为准，不要自行调整。
4. 输出结构清晰，先总览再逐条，控制在适读长度。
```
**User：**
```
以下是本迭代的结构化风险清单（JSON）：
{{risk_list}}
请完成三件事：
1. 按严重程度（高/中/低）对风险排序并做总览。
2. 每条风险给出可能的归因（一句话，基于状态/变更/进度等客观事实）。
3. 给出具体建议动作（谁、做什么、什么时间前）。
注意：只基于给定数据回答，不要编造。如果数据不足以判断，明确说「需人工确认」。
```

### 6.2 周报生成（工作流 B 第 5 步）
**System：**
```
你是项目经理的周报助手。你的输入是本周迭代的结构化数据（风险清单 + 关键指标）。
请把信息整理成一份发给团队/上级的 Markdown 周报。
硬性约束：
1. 只基于给定数据撰写，不虚构进展、不夸大完成度。
2. 数据缺失时写「待补充」，不要编一个数字填进去。
3. 风险部分直接引用规则引擎结论，不擅自修改严重度。
4. 语气专业、简洁，适合工作群或邮件发送。
```
**User：**
```
迭代信息：迭代名称 {{sprint_name}}，统计周期 {{period}}，扫描 issue 总数 {{total}}，风险条目 {{risk_count}}。
结构化风险清单（JSON）：{{risk_list}}
请生成 Markdown 周报，包含：1.本周概览 2.风险清单与建议（按严重度排序）3.下周重点 4.需人工确认的事项。
只基于给定数据，不要编造。
```

---

## 7. 安全与三个坑

1. **Token 绝不硬编码**：存 Coze 密钥管理，用 `{{jira_token}}` 引用；本地测试可用环境变量，别提交到 GitHub。
2. **规则前置**：见第 4 节，直接让 AI 判风险必翻车。
3. **别喂原始大 JSON**：先在代码节点压成精简结构（只留 key/状态/负责人/到期/点数/变更次数），再给大模型，又快又省又稳。

---

## 8. 验证状态（本地已跑通）

用 `mock_jira_data.json`（20 条，刻意埋 6 类风险 + 干净样本）运行 `risk_engine.py` 结果：
- 命中 11 条风险：高 4 / 中 5 / 低 2
- 规则覆盖：逾期 2、临近到期 2、负载失衡 1、长期未更新 2、反复流转 2、故事点过大 2
- 干净样本零误报 → 证明规则逻辑正确，上 Coze 时可直接复用。
