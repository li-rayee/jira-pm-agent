"""
Jira REST API 客户端（零依赖）。

安全：所有敏感信息（站点/邮箱/Token）从环境变量读取，绝不写死在代码里。
用法：
    export JIRA_SITE="https://xxx.atlassian.net"
    export JIRA_EMAIL="you@example.com"
    export JIRA_TOKEN="ATATT3xF..."
    python jira_client.py
"""
import os
import base64
import json
import urllib.request
import urllib.error
from urllib.parse import urlencode


def _load_dotenv(path=None):
    """读取项目根目录 .env（若存在）注入环境变量（不覆盖已设值）。"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


def _env():
    _load_dotenv()
    site = os.environ.get("JIRA_SITE", "").rstrip("/")
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_TOKEN", "")
    if not (site and email and token):
        raise RuntimeError("缺少环境变量 JIRA_SITE / JIRA_EMAIL / JIRA_TOKEN")
    return site, email, token


def _auth_header():
    _, email, token = _env()
    raw = f"{email}:{token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def api_get(path, params=None):
    """GET 一个 Jira Cloud REST 端点，返回解析后的 JSON。"""
    site, _, _ = _env()
    url = f"{site}/rest/api/3/{path}"
    if params:
        url += "?" + urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": _auth_header(),
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTP {e.code} {e.reason} -> {body[:500]}")


def api_post(path, body):
    """POST JSON 到 Jira Cloud REST 端点（用于 /search/jql 等新接口）。"""
    site, _, _ = _env()
    url = f"{site}/rest/api/3/{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": _auth_header(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8")
            if not body.strip():
                return {}   # 204 No Content 等空响应
            return json.loads(body)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTP {e.code} {e.reason} -> {err_body[:500]}")


def get_myself():
    return api_get("myself")


def get_field_map():
    """返回 {字段名: fieldId}，用于定位故事点等自定义字段。"""
    fields = api_get("field")
    return {f["name"]: f["id"] for f in fields}


def search_issues(jql, max_results=50, fields=None):
    # 注意：新版 Jira Cloud 已废弃 /rest/api/3/search（GET），改用 /search/jql（POST）。
    # expand 在新接口为逗号分隔的字符串（非列表）。
    body = {
        "jql": jql,
        "maxResults": max_results,
        "expand": "changelog",
    }
    if fields:
        body["fields"] = fields
    return api_post("search/jql", body)


def api_delete(path):
    """DELETE 一个 Jira Cloud REST 端点（如删除 issue）。"""
    site, _, _ = _env()
    url = f"{site}/rest/api/3/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": _auth_header(),
        "Accept": "application/json",
    }, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTP {e.code} {e.reason} -> {body[:500]}")


def get_projects(max_results=50):
    return api_get("project/search", {"maxResults": max_results})


if __name__ == "__main__":
    try:
        me = get_myself()
        print("认证成功：", me.get("displayName"), me.get("accountId"))
    except RuntimeError as e:
        print("认证失败：", e)
        raise SystemExit(1)

    fmap = get_field_map()
    sp_field = next((fid for name, fid in fmap.items() if "story point" in name.lower()), None)
    print("故事点字段 ID：", sp_field)

    projs = get_projects()
    pkeys = [p["key"] for p in projs.get("values", [])]
    print("项目列表：", pkeys)
    if not pkeys:
        print("该账号下没有可见项目，无法继续。")
        raise SystemExit(0)

    jql = f"project IN ({', '.join(pkeys)}) ORDER BY updated DESC"
    FIELDS = ["summary", "status", "assignee", "duedate", "customfield_10016",
              "issuetype", "resolutiondate", "created", "updated", "project", "progress"]
    data = search_issues(jql, max_results=50, fields=FIELDS)
    issues = data.get("issues", [])
    print(f"拉取到 {len(issues)} 条 issue（total={data.get('total')}）")
    print("\n--- 每条 issue 取值 ---")
    for it in issues:
        f = it.get("fields", {})
        ch = it.get("changelog", {}).get("histories", [])
        status_changes = sum(
            1 for h in ch for i in h.get("items", [])
            if i.get("field") == "status"
        )
        print({
            "key": it.get("key"),
            "summary": f.get("summary"),
            "status": (f.get("status") or {}).get("name"),
            "assignee": (f.get("assignee") or {}).get("displayName"),
            "duedate": f.get("duedate"),
            "story_points": f.get("customfield_10016"),
            "resolutiondate": f.get("resolutiondate"),
            "updated": f.get("updated"),
            "progress": f.get("progress"),
            "status_changes": status_changes,
            "changelog_count": len(ch),
        })
