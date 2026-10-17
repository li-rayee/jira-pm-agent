#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
拉取真实 Jira Cloud 数据并运行风险引擎，输出报告。

运行（敏感信息走环境变量，绝不写死）：
    export JIRA_SITE="https://xxx.atlassian.net"
    export JIRA_EMAIL="you@example.com"
    export JIRA_TOKEN="ATATT3xF..."
    python run_real.py                       # 跑账号下全部项目
    python run_real.py "project = SCRUM"      # 指定 JQL
    python run_real.py "" --out real_report.md
"""
import sys
import json
from datetime import date

from jira_adapter import fetch_and_normalize
from risk_engine import RiskEngine, format_text_report, _parse_date


def main():
    jql = sys.argv[1] if len(sys.argv) > 1 else ""
    out = None
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    today_arg = None
    if "--today" in sys.argv:
        today_arg = sys.argv[sys.argv.index("--today") + 1]

    ref = _parse_date(today_arg) if today_arg else date.today()
    print(f"[参考日期] {ref.isoformat()}")

    print("[正在拉取真实 Jira ...]")
    issues = fetch_and_normalize(jql)
    print(f"[拉取到] {len(issues)} 条 issue")

    engine = RiskEngine(reference_date=ref)
    result = engine.analyze(issues)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    report = format_text_report(result)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n[已写出报告] {out}")
    else:
        print("\n" + report)


if __name__ == "__main__":
    main()
