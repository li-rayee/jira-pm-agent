# Jira 智能项目管家

一个连着 Jira REST API 跑的轻量风险监控脚本集。核心想法很朴素：让机器先把客观风险算出来，再让大模型做归因和措辞润色。我把它跑在 WorkBuddy 智能体里，直接在对话里问本周有哪些风险、谁手上活太多、这周周报怎么写，智能体调用仓库里的脚本取数、算风险、生成分析，不用注册任何第三方平台，也不碰单独的模型 Key。

说起来，这套做法最早是想解决一个具体痛点——把原始 issue 直接丢给大模型让它自己判断风险，它经常把"进行中"判成"延期"，误报很烦人。后来改成先用六类确定性规则筛出客观事实，再交给大模型归因。机器算、人定标准、AI 润色，分工清楚之后误报就下来了。

## 为什么把规则引擎放在前面

直接交给模型判断风险，问题出在模型拿不准"标准"。逾期、超期未动、反复流转这些状态，用代码算比用语言描述更稳。规则引擎负责确定性计算，阈值由使用的人按团队实际速率来定——五天、八点、五个任务，都是可以调的旋钮。大模型只做它擅长的事：把结构化风险清单翻成人话，顺带给归因和建议，而且被约束在"只基于给定数据、不编造"的框里。

## 架构

整条链路分四步。对话入口在 WorkBuddy 里，用户开口问"本周有什么风险"，智能体直接调用本仓库脚本：先去 Jira Cloud 取数，交给规则引擎算风险，再用内置模型做归因和周报，最后把结果回给用户。数据获取走 Jira Cloud 的 `/search/jql` 端点，用 API Token 认证。

早先这层"胶水"是用 Coze/Dify 做的，后来换成 WorkBuddy 智能体，把对话入口、工作流编排和大模型三层职责一起扛了。规则引擎和 Jira 客户端是恒定的内核，换的只是编排者。实际好处是免了第三方注册、免了可视化画布、免了单独的模型 Key，对话即调用，代码在本仓库已经跑通。

> 如果更想要一个团队能独立访问的 Bot，回头接 Coze/Dify 也能做，思路跟 workbuddy_setup.md 一致，逻辑可以整体搬过去。

## 快速开始

```bash
# 在项目根目录创建 .env 放凭证（已被 .gitignore 排除，不会进仓库）
#   JIRA_SITE=https://你的站点.atlassian.net
#   JIRA_EMAIL=你创建 Token 的邮箱
#   JIRA_TOKEN=ATATT3xF...        # Jira 后台，账户设置，安全，创建 API Token
#   脚本自动读 .env，也可以用 export 临时覆盖

# 跑规则引擎，用仿真 fixture 验证六类规则覆盖
python risk_engine.py mock_jira_data.json --today 2026-10-18
#   输出风险清单 JSON + report.md：命中 11 条，六类规则全触发，干净样本零误报

# 连真实 Jira Cloud 直接跑，凭证走 .env 或环境变量
python run_real.py "" --out real_report.md
#   拉取账号下全部项目，归一化后跑引擎，产出 real_report.md

# 往真实 Jira 写一批覆盖五类规则的风险 issue（可选，做现场演示用）
python demo_seed.py                       # 清掉旧 [Demo] 后重建 9 条覆盖风险的 issue
python run_real.py "project = SCRUM" --out real_report.md
#   命中 8 条：逾期 2 / 临近到期 2 / 负载失衡 1 / 反复流转 1 / 故事点过大 2（高 4 / 中 2 / 低 2）

# 组装提示词（设好 API Key 才真实调用大模型，否则只打印 prompt）
#   在 WorkBuddy 里可以跳过这步，直接用内置模型做归因和周报
export DEEPSEEK_API_KEY="你的key"     # 或 OPENAI_API_KEY
python prompts.py
```

依赖只有 Python 3.8 以上的标准库，json、datetime、urllib 三个模块就够了。

仿真用 `mock_jira_data.json` 里二十条数据驱动引擎，命中十一条风险（高四、中五、低二），六类规则全部触发，干净样本零误报。真实接入这边，我已经直连自己的 Jira Cloud 站点跑过，走 `/search/jql` 端点拉取 issue，完成字段映射——中文状态名、`customfield_10016` 故事点、`progress` 对象、从 `changelog` 统计状态变更都在内，在四条真实 issue 上零误报，说明端到端链路和"规则前置不误报"这个判断是站得住的。

## 目录结构

| 文件 | 作用 |
|---|---|
| `risk_engine.py` | 规则引擎，六类风险，阈值集中在 `RULE_CONFIG`，中英文状态名都认 |
| `prompts.py` | 两套提示词（风险归因 + 周报）加一个大模型调用占位（urllib 调 DeepSeek 或通义，没 Key 只打印） |
| `jira_client.py` | 真实 Jira Cloud REST 客户端，认证加 `/search/jql` 拉取再加字段映射辅助，自动读 `.env` 凭证 |
| `jira_adapter.py` | 把真实 Jira issue 归一化成引擎输入，从 `changelog` 算状态变更、从 `progress` 算百分比 |
| `run_real.py` | 拉真实数据、归一化、跑引擎、出报告的一键脚本 |
| `mock_jira_data.json` | 仿真 Jira 返回 fixture，二十条，含六类风险样本 |
| `report.md` / `real_report.md` | 仿真和真实数据各自的验证报告 |
| `demo_seed.py` | 往真实 Jira 的 SCRUM 项目写一批覆盖五类规则的 demo issue，可重复跑，做现场演示 |
| `explore_meta.py` | 探测项目元数据，accountId、issuetype、可用状态流转，辅助接入 |
| `workbuddy_setup.md` | WorkBuddy 用法：对话式调用、每日自动化配置、三段提示词原文 |

## 风险判定规则

| 规则 | 判定条件 | 严重度 |
|---|---|---|
| 逾期 | 状态未完成且 due 早于今天 | 高 |
| 长期未更新 | 最近状态变更在五天前 | 中 |
| 反复流转 | 状态变更次数超过五次 | 中 |
| 故事点过大 | story_points 大于八 | 低 |
| 负载失衡 | 某人进行中任务超过五个 | 中 |
| 临近到期未完成 | due 在三天内且进度不到一半 | 高 |

状态名做了中英文兼容，done_statuses 和 in_progress_statuses 两套都认，适配中文 Jira。阈值集中在 `risk_engine.py` 的 `RULE_CONFIG` 里，按团队实际速率调。

## WorkBuddy 接入

细节都在 `workbuddy_setup.md`：两种用法，要么直接在对话里问，要么配一个每日自动化定时扫描。风险归因、周报、负载查询三段提示词原文也在那份文档里，WorkBuddy 可以直接套用 `prompts.py`，也可以自己写。规则引擎逻辑在 `risk_engine.py`，智能体直接调用即可。

## 数据来源说明

规则覆盖用 `mock_jira_data.json` 这个仿真 fixture 驱动引擎，六类风险全覆盖且零误报，证明规则逻辑本身是对的。真实接入这边，我直连了自己的 Jira Cloud 站点，端到端跑通并且零误报，证明 API 集成、字段映射、changelog 状态变更统计都有效。

现场演示用的真实数据，是 `demo_seed.py` 往真实 Jira 写进去的九条覆盖风险 issue——其中八条挂在同一个人名下做进行中任务，用来触发负载失衡，另一条做了七次状态跳转，用来触发反复流转。再用 `run_real.py` 跑批，命中八条风险，覆盖五类规则（逾期二、临近到期二、负载失衡一、反复流转一、故事点过大二，高四中二低二）。

说起来有个诚实的坑得讲清楚：长期未更新这条规则看的是"最近状态变更是否在五天前"，依赖历史时间戳，新建的 demo 数据造不出来。所以仓库里用仿真 fixture 完整覆盖它，mock 共命中十一条、六类全触发，真实环境里它会在历史数据上正常生效。要接真实团队数据调参，得等真实迭代跑满一个周期，再用 `run_real.py` 实测替换这里的占位结论。

## 免责声明

仓库里 `mock_jira_data.json` 是仿真数据，非生产用途。真实 Jira 接入那部分，`lastStatusChange` 和 `statusChangeCount` 由 `jira_adapter.py` 从 `changelog.histories` 统计，`progress` 由 `progress` 对象换算成百分比。

密钥只走 `.env` 或环境变量，绝不写进代码也绝不进仓库，见 `.gitignore`。本地运行和 WorkBuddy 自动化都读项目根目录的 `.env`。大模型输出受"不编造、需人工确认"约束，这个项目不构成任何生产系统保障。
