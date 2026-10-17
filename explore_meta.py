"""探索 SCRUM 项目元数据：accountId、issuetype、可用 status 流转。"""
import json
import jira_client as j

me = j.get_myself()
print("账号:", me.get("displayName"), "| accountId:", me.get("accountId"))

# 任务类型
meta = j.api_get("issue/createmeta", {"projectKeys": "SCRUM", "expand": "projects.issuetypes"})
for p in meta.get("projects", []):
    print("项目:", p.get("key"), p.get("name"))
    for it in p.get("issuetypes", []):
        print("  issuetype:", it.get("id"), it.get("name"))

# 拿一个现有 issue 看可用 transitions
data = j.search_issues("project = SCRUM", max_results=1, fields=["summary"])
issues = data.get("issues", [])
if issues:
    key = issues[0]["key"]
    print("\n样本 issue:", key)
    trans = j.api_get(f"issue/{key}/transitions")
    for t in trans.get("transitions", []):
        to = (t.get("to") or {}).get("name")
        print(f"  transition id={t.get('id')} name={t.get('name')!r} -> {to!r}")
else:
    print("当前 SCRUM 无 issue，可跳过 transitions 探测")
