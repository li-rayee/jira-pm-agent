"""
把「真实 Jira Cloud」返回的原始 issue 适配成 risk_engine 能吃的标准格式。

真实 Jira 与标准结构的关键差异（本适配器统一处理）：
- 状态名是中文：fields.status.name = "正在进行" / "待办" / "已完成" ...
- 故事点在自定义字段：默认 customfield_10016（不同站点可能不同，可用
  JIRA_STORYPOINT_FIELD 环境变量覆盖）。
- 进度是对象：fields.progress = {"progress": 已完成量, "total": 总量}。
- 没有 lastStatusChange / statusChangeCount 字段 → 必须从
  changelog.histories 里统计「status 类型变更」得到。

用法：
    from jira_adapter import fetch_and_normalize
    issues = fetch_and_normalize("project = SCRUM ORDER BY updated DESC")
"""
import os
from jira_client import search_issues, get_projects


def _story_point_field():
    return os.environ.get("JIRA_STORYPOINT_FIELD", "customfield_10016")


def normalize_issue(raw, sp_field=None):
    """单个真实 Jira issue -> 标准归一化 issue。"""
    sp_field = sp_field or _story_point_field()
    f = raw.get("fields", {}) or {}
    histories = (raw.get("changelog") or {}).get("histories", []) or []

    # 统计 status 类变更
    status_histories = [
        h for h in histories
        for item in h.get("items", [])
        if item.get("field") == "status"
    ]
    last_status_change = None
    if status_histories:
        last_status_change = max(h.get("created") for h in status_histories)
    status_change_count = len(status_histories)

    # 进度百分比
    prog = f.get("progress") or {}
    if isinstance(prog, dict):
        done = prog.get("progress") or 0
        total = prog.get("total") or 0
        progress_pct = round(done / total * 100, 1) if total > 0 else 0.0
    else:
        progress_pct = 0.0

    return {
        "key": raw.get("key"),
        "fields": {
            "summary": f.get("summary"),
            "status": {"name": (f.get("status") or {}).get("name")},
            "assignee": {"displayName": (f.get("assignee") or {}).get("displayName")},
            "duedate": f.get("duedate"),
            "storyPoints": f.get(sp_field),
            "progress": progress_pct,
            "lastStatusChange": last_status_change,
            "statusChangeCount": status_change_count,
        },
    }


def fetch_and_normalize(jql, max_results=50):
    """
    拉取真实 Jira 并归一化。不传 jql 时自动用「账号下全部项目」作为边界查询
    （新版 /search/jql 要求 JQL 必须有边界）。
    """
    if not jql:
        projs = get_projects()
        keys = [p["key"] for p in projs.get("values", [])]
        jql = f"project IN ({', '.join(keys)}) ORDER BY updated DESC" if keys \
              else "ORDER BY updated DESC"
    sp_field = _story_point_field()
    data = search_issues(jql, max_results=max_results)
    raw_issues = data.get("issues", [])
    return [normalize_issue(it, sp_field) for it in raw_issues]
