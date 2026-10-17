#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jira 项目风险规则引擎（零依赖，纯标准库）
================================================

输入：Jira /rest/api/3/search 返回的 JSON（结构 {"issues": [...]}），
      或本仓库的 mock_jira_data.json（结构一致）。
输出：结构化风险清单（JSON）+ 可读文本报告。

设计要点
--------
1. 「规则前置过滤」：先用确定性规则把客观风险筛出来，再交给大模型做
   归因与措辞（对应 JD 的「人机协同：机器算、人定标准、AI 润色」）。
2. 阈值可配置：下面 RULE_CONFIG 里的数字就是你在面试里说的
   「根据实际团队速率调出来的阈值」，改这里即可，不用动逻辑。
3. 字段说明：
   - duedate / status / assignee / storyPoints 贴近 Jira 原生字段。
   - lastStatusChange、statusChangeCount、progress 是「模拟/自定义字段」：
     真实 Jira 里 lastStatusChange 与 statusChangeCount 需要从
     changelog.histories 统计 status 类变更；progress 可用子任务完成
     比例或自定义进度字段替代。本引擎已留好接入位。

用法
----
    python risk_engine.py                      # 默认读 mock_jira_data.json
    python risk_engine.py data.json --today 2026-10-18
    python risk_engine.py data.json --out report.md
"""

import json
import sys
import argparse
from datetime import datetime, date, timedelta


# ---------------------------------------------------------------------------
# 风险阈值配置（这就是你「调出来」的判断标准，集中放一处方便调参）
# ---------------------------------------------------------------------------
RULE_CONFIG = {
    "overdue_days": 0,        # 状态≠Done 且 due < 今天 + overdue_days  → 逾期（高）
    "stale_days": 5,          # 最近状态变更早于 今天-stale_days         → 长期未更新（中）
    "churn_count": 5,         # 状态变更次数 > churn_count              → 反复流转（中）
    "big_story_points": 8,    # 故事点 > big_story_points               → 故事点过大（低）
    "load_threshold": 5,      # 某人进行中任务数 > load_threshold        → 负载失衡（中）
    "due_soon_window": 3,     # due 落在 [今天, 今天+window] 且进度<50%  → 临近到期（高）
    "due_soon_progress": 50,  # 临近到期未完成的进度阈值（%）
    # 状态名做了中英文兼容：真实 Jira 可能是「正在进行」「待办」「已完成」等
    "done_statuses": ["Done", "已完成", "完成", "Closed", "关闭",
                      "Resolved", "已解决", "Done (已交付)"],
    "in_progress_statuses": ["In Progress", "正在进行", "进行中",
                             "处理中", "In Review", "评审中", "开发中"],
}

SEVERITY_RANK = {"高": 3, "中": 2, "低": 1}
SEVERITY_ORDER = ["高", "中", "低"]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _parse_date(value):
    """解析 Jira 日期：'YYYY-MM-DD' 或 ISO 带时区字符串，返回 date 或 None。"""
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.date()
        except ValueError:
            continue
    # 退路：取前 10 位
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _get(issue, *keys, default=None):
    """安全取值，支持嵌套（fields.status.name）。"""
    cur = issue
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def _issue_status(issue):
    return (_get(issue, "fields", "status", "name") or "").strip()


def _issue_assignee(issue):
    return _get(issue, "fields", "assignee", "displayName") or "未分配"


def _issue_points(issue):
    v = _get(issue, "fields", "storyPoints")
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _issue_progress(issue):
    v = _get(issue, "fields", "progress")
    if v is None:
        return 0.0
    # 真实 Jira 的 progress 是 {"progress": 已完成量, "total": 总量} 对象
    if isinstance(v, dict):
        done = v.get("progress") or 0
        total = v.get("total") or 0
        return round(done / total * 100, 1) if total > 0 else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _issue_due(issue):
    return _parse_date(_get(issue, "fields", "duedate"))


def _issue_last_change(issue):
    return _parse_date(_get(issue, "fields", "lastStatusChange"))


def _issue_churn(issue):
    v = _get(issue, "fields", "statusChangeCount")
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# 规则引擎
# ---------------------------------------------------------------------------
class RiskEngine:
    def __init__(self, config=None, reference_date=None):
        self.cfg = dict(RULE_CONFIG)
        if config:
            self.cfg.update(config)
        self.today = reference_date or date.today()

    def _is_done(self, status):
        return status in self.cfg["done_statuses"]

    def _is_in_progress(self, status):
        return status in self.cfg["in_progress_statuses"]

    def analyze(self, issues):
        risks = []
        # 先统计负载（规则5需要全局视角）
        in_progress_by_person = {}
        for it in issues:
            if self._is_in_progress(_issue_status(it)):
                name = _issue_assignee(it)
                in_progress_by_person.setdefault(name, []).append(
                    _get(it, "key")
                )

        for it in issues:
            key = _get(it, "key") or "?"
            summary = _get(it, "fields", "summary") or ""
            status = _issue_status(it)
            due = _issue_due(it)
            last_change = _issue_last_change(it)
            churn = _issue_churn(it)
            points = _issue_points(it)
            progress = _issue_progress(it)
            assignee = _issue_assignee(it)

            # 规则1：逾期（状态≠Done 且 due < 今天）
            if not self._is_done(status) and due is not None:
                if due < self.today + timedelta(days=self.cfg["overdue_days"]):
                    risks.append(self._mk(key, summary, "逾期", "高",
                        f"状态[{status}]未完成，到期日 {due.isoformat()} 已过去 "
                        f"{(self.today - due).days} 天"))

            # 规则2：长期未更新（最近状态变更 > stale_days 天前）
            if last_change is not None:
                idle_days = (self.today - last_change).days
                if idle_days > self.cfg["stale_days"]:
                    risks.append(self._mk(key, summary, "长期未更新", "中",
                        f"最近一次状态变更在 {last_change.isoformat()}，"
                        f"已 {idle_days} 天未动，可能卡住"))

            # 规则3：反复流转（状态变更次数 > churn_count）
            if churn > self.cfg["churn_count"]:
                risks.append(self._mk(key, summary, "反复流转", "中",
                    f"状态变更 {churn} 次（> {self.cfg['churn_count']}），"
                    f"需求可能不清晰或存在阻塞"))

            # 规则4：故事点过大（> big_story_points）
            if points > self.cfg["big_story_points"]:
                risks.append(self._mk(key, summary, "故事点过大", "低",
                    f"故事点 {points:.0f}（> {self.cfg['big_story_points']}），"
                    f"建议拆分为更小粒度"))

            # 规则6：临近到期未完成（due 在窗口内 且 进度 < 阈值）
            if not self._is_done(status) and due is not None:
                window_end = self.today + timedelta(days=self.cfg["due_soon_window"])
                if self.today <= due <= window_end and progress < self.cfg["due_soon_progress"]:
                    risks.append(self._mk(key, summary, "临近到期未完成", "高",
                        f"到期日 {due.isoformat()}（{ (due - self.today).days } 天内），"
                        f"进度仅 {progress:.0f}%"))

        # 规则5：负载失衡（某人进行中任务数 > 阈值）
        for name, keys in in_progress_by_person.items():
            if len(keys) > self.cfg["load_threshold"]:
                risks.append({
                    "issue_key": None,
                    "assignee": name,
                    "rule": "负载失衡",
                    "severity": "中",
                    "detail": (
                        f"{name} 当前进行中任务 {len(keys)} 个"
                        f"（> {self.cfg['load_threshold']}）：{', '.join(keys)}"
                    ),
                    "raw_facts": {"in_progress_count": len(keys), "keys": keys},
                })

        # 排序：严重度降序，再按 key
        risks.sort(key=lambda r: (-SEVERITY_RANK.get(r["severity"], 0),
                                  r.get("issue_key") or ""))
        return self._build_result(issues, risks)

    def _mk(self, key, summary, rule, severity, detail):
        return {
            "issue_key": key,
            "summary": summary,
            "rule": rule,
            "severity": severity,
            "detail": detail,
            "raw_facts": {
                "status": None, "due": None, "progress": None,
                "churn": None, "assignee": None,
            },
        }

    def _build_result(self, issues, risks):
        by_severity = {s: 0 for s in SEVERITY_ORDER}
        by_rule = {}
        for r in risks:
            by_severity[r["severity"]] = by_severity.get(r["severity"], 0) + 1
            by_rule[r["rule"]] = by_rule.get(r["rule"], 0) + 1
        return {
            "scan_date": self.today.isoformat(),
            "total_issues": len(issues),
            "risk_count": len(risks),
            "summary": {"by_severity": by_severity, "by_rule": by_rule},
            "risks": risks,
        }


# ---------------------------------------------------------------------------
# 文本报告
# ---------------------------------------------------------------------------
def format_text_report(result):
    lines = []
    lines.append(f"# 迭代风险扫描报告（{result['scan_date']}）")
    lines.append("")
    lines.append(f"- 扫描 issue 总数：{result['total_issues']}")
    lines.append(f"- 命中风险条目：{result['risk_count']}")
    s = result["summary"]["by_severity"]
    lines.append(f"- 严重度分布：高 {s.get('高',0)} / 中 {s.get('中',0)} / 低 {s.get('低',0)}")
    lines.append("")
    for sev in SEVERITY_ORDER:
        group = [r for r in result["risks"] if r["severity"] == sev]
        if not group:
            continue
        lines.append(f"## {sev}危（{len(group)} 项）")
        for r in group:
            target = r.get("issue_key") or r.get("assignee") or "—"
            lines.append(f"- **[{r['rule']}]** {target}：{r['detail']}")
        lines.append("")
    lines.append("> 以上为规则引擎的客观筛选结果，归因分析与建议请交由大模型节点处理。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def load_issues(source):
    if isinstance(source, (dict, list)):
        data = source
    else:
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)
    if isinstance(data, dict) and "issues" in data:
        return data["issues"]
    if isinstance(data, list):
        return data
    raise ValueError("无法识别的 issue 数据结构")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", default="mock_jira_data.json")
    ap.add_argument("--today", default=None, help="参考日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--out", default=None, help="文本报告输出路径")
    args = ap.parse_args()

    ref = _parse_date(args.today) if args.today else None
    engine = RiskEngine(reference_date=ref)
    issues = load_issues(args.data)
    result = engine.analyze(issues)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    report = format_text_report(result)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n[已写出文本报告] {args.out}")
    else:
        print("\n" + report)


if __name__ == "__main__":
    main()
