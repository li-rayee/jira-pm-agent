#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提示词模板 + 大模型调用占位
================================================

本文件把 risk_engine 输出的「结构化风险清单」组装成两套可直接发给
大模型的提示词：
  1) build_risk_analysis_prompt  —— 风险归因分析（排序 / 归因 / 建议）
  2) build_weekly_report_prompt   —— 周报生成（Markdown）

关键约束（写进提示词本体，防止 AI 编造）：
  - 只基于给定数据回答，不编造字段或数字；
  - 数据不足以判断时，明确写「需人工确认」；
  - 严重度以规则引擎判定为准，不要自行升降级。

call_llm() 是 OpenAI 兼容接口的占位实现：
  - 优先读环境变量 DEEPSEEK_API_KEY（DeepSeek），其次 OPENAI_API_KEY；
  - 没有 key 时不会联网，只打印组装好的 prompt，方便你先在本地检查效果。
真实上 Coze 时，这一步由 Coze 的「大模型节点」完成，本文件仅供本地验证。
"""

import json
import os
import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
# 提示词模板
# ---------------------------------------------------------------------------
RISK_ANALYSIS_SYSTEM = """你是项目风险分析师，服务于敏捷软件开发团队。
你的输入是「规则引擎」预先筛选出的结构化风险清单（JSON）。
规则引擎已完成客观判定，你只负责三件事：归因、建议、措辞。

硬性约束：
1. 只基于给定数据回答，绝不编造 issue、负责人、日期或数字。
2. 若某条风险的数据不足以判断归因，必须明确写「需人工确认」，不要猜测。
3. 严重度（高/中/低）以风险清单中的字段为准，不要自行调整。
4. 输出结构清晰，先总览再逐条，控制在适读长度。"""

RISK_ANALYSIS_USER = """以下是本迭代的结构化风险清单（JSON）：
{risks_json}

请完成三件事：
1. 按严重程度（高/中/低）对风险排序并做总览。
2. 每条风险给出可能的归因（一句话，基于状态/变更/进度等客观事实）。
3. 给出具体建议动作（谁、做什么、什么时间前）。

注意：只基于给定数据回答，不要编造。如果数据不足以判断，明确说「需人工确认」。"""


WEEKLY_REPORT_SYSTEM = """你是项目经理的周报助手。
你的输入是本周迭代的结构化数据（风险清单 + 关键指标）。
请把信息整理成一份发给团队/上级的 Markdown 周报。

硬性约束：
1. 只基于给定数据撰写，不虚构进展、不夸大完成度。
2. 数据缺失时写「待补充」，不要编一个数字填进去。
3. 风险部分直接引用规则引擎结论，不擅自修改严重度。
4. 语气专业、简洁，适合工作群或邮件发送。"""

WEEKLY_REPORT_USER = """迭代信息：
- 迭代名称：{sprint_name}
- 统计周期：{period}
- 扫描 issue 总数：{total}
- 风险条目总数：{risk_count}

结构化风险清单（JSON）：
{risks_json}

请生成一份 Markdown 周报，包含以下板块：
1. 本周概览（进度、风险总数、关键数字）
2. 风险清单与建议（按严重度排序，引用上面的判定）
3. 下周重点（基于临近到期与高危及负载失衡项）
4. 需人工确认的事项（数据不足或需 PM 判断的点）

只基于给定数据，不要编造。"""


# ---------------------------------------------------------------------------
# 组装函数
# ---------------------------------------------------------------------------
def build_risk_analysis_prompt(result):
    risks_json = json.dumps(result.get("risks", []), ensure_ascii=False, indent=2)
    return {
        "system": RISK_ANALYSIS_SYSTEM,
        "user": RISK_ANALYSIS_USER.format(risks_json=risks_json),
    }


def build_weekly_report_prompt(result, sprint_name="Sprint 23", period="本周"):
    risks_json = json.dumps(result.get("risks", []), ensure_ascii=False, indent=2)
    return {
        "system": WEEKLY_REPORT_SYSTEM,
        "user": WEEKLY_REPORT_USER.format(
            sprint_name=sprint_name,
            period=period,
            total=result.get("total_issues", 0),
            risk_count=result.get("risk_count", 0),
            risks_json=risks_json,
        ),
    }


# ---------------------------------------------------------------------------
# 大模型调用占位（OpenAI 兼容）
# ---------------------------------------------------------------------------
def call_llm(prompt, model="deepseek-chat", max_tokens=1500):
    """
    调用 OpenAI 兼容接口。优先 DeepSeek，其次 OpenAI。
    没有 API key 时返回 None 并打印 prompt（不联网）。
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("\n[call_llm] 未检测到 API key（DEEPSEEK_API_KEY / OPENAI_API_KEY），"
              "仅打印 prompt，不联网调用。\n")
        print("===== SYSTEM =====\n" + prompt["system"])
        print("\n===== USER =====\n" + prompt["user"])
        return None

    base_url = (
        "https://api.deepseek.com/v1/chat/completions"
        if os.environ.get("DEEPSEEK_API_KEY")
        else "https://api.openai.com/v1/chat/completions"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": prompt["user"]},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    req = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, IndexError) as e:
        print(f"[call_llm] 调用失败：{e}")
        return None


# ---------------------------------------------------------------------------
# 本地演示：组装两套提示词并打印（不联网）
# ---------------------------------------------------------------------------
def demo(result):
    print("=" * 60)
    print("提示词模板 ①：风险归因分析")
    print("=" * 60)
    call_llm(build_risk_analysis_prompt(result))

    print("\n" + "=" * 60)
    print("提示词模板 ②：周报生成")
    print("=" * 60)
    call_llm(build_weekly_report_prompt(result, sprint_name="Sprint 23", period="2026-10-13 ~ 2026-10-18"))


if __name__ == "__main__":
    # 单独运行时会自动加载 mock 数据做一次组装演示
    import risk_engine as re
    issues = re.load_issues("mock_jira_data.json")
    engine = re.RiskEngine(reference_date=re._parse_date("2026-10-18"))
    result = engine.analyze(issues)
    demo(result)
