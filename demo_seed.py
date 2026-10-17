#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
往真实 Jira（SCRUM 项目）写入一批覆盖风险规则的 demo issue，
用于现场演示「规则引擎 + 真实 Jira」端到端跑批。

覆盖规则：
  R1 逾期        → 2 条（due 在过去，状态≠完成）
  R4 故事点过大  → 2 条（customfield_10016 > 8）
  R6 临近到期    → 2 条（due 在 3 天内，进度默认 0）
  R5 负载失衡    → 同一个人挂 >5 个「正在进行」任务（本脚本挂 8 个）
  R3 反复流转    → 1 条，做 7 次状态跳转（> 阈值 5）
  R2 长期未更新  → 依赖 5 天前的历史时间戳，新建数据无法真实制造，由 mock 验证

安全：删除旧 [Demo] 后重建，可重复运行；敏感信息全部走环境变量。
"""
import time
import sys
import jira_client as j

ACCOUNT = j.get_myself()["accountId"]
PROJECT = "SCRUM"
ISSUETYPE = "10004"          # 任务
SP_FIELD = "customfield_10016"


def _req_with_retry(fn, *args, **kw):
    for attempt in range(4):
        try:
            return fn(*args, **kw)
        except RuntimeError as e:
            if "HTTP 429" in str(e) and attempt < 3:
                wait = 3 * (attempt + 1)
                print(f"  [限流] 退避 {wait}s ...")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("重试后仍失败")


def cleanup():
    print("[清理] 删除已有 [Demo] issue ...")
    data = j.search_issues("project = SCRUM", max_results=100,
                           fields=["summary"])
    for it in data.get("issues", []):
        if (it.get("fields", {}).get("summary") or "").startswith("[Demo]"):
            try:
                _req_with_retry(j.api_delete, f"issue/{it['key']}")
                print("  删除", it["key"])
            except RuntimeError as e:
                print("  删除失败", it["key"], e)


def create_issue(summary, duedate=None, points=None, assignee=ACCOUNT):
    fields = {
        "project": {"key": PROJECT},
        "issuetype": {"id": ISSUETYPE},
        "summary": summary,
    }
    if assignee:
        fields["assignee"] = {"accountId": assignee}
    if duedate:
        fields["duedate"] = duedate
    if points is not None:
        fields[SP_FIELD] = points
    resp = _req_with_retry(j.api_post, "issue", {"fields": fields})
    return resp.get("key")


def get_transitions(key):
    return j.api_get(f"issue/{key}/transitions").get("transitions", [])


def transition_to(key, target):
    for t in get_transitions(key):
        if (t.get("to") or {}).get("name") == target:
            _req_with_retry(j.api_post, f"issue/{key}/transitions",
                            {"transition": {"id": t["id"]}})
            return True
    print(f"  [警告] {key} 找不到到『{target}』的流转")
    return False


def main():
    cleanup()
    time.sleep(1)

    # (summary, duedate, points, final_status)
    plan = [
        # R1 逾期（高）：due 已过去，状态≠完成
        ("[Demo] 支付网关联调（已逾期）", "2026-10-08", 5, "正在进行"),
        ("[Demo] 数据看板后端（已逾期）", "2026-10-12", 3, "待办"),
        # R6 临近到期（高）：due 在 3 天内，进度默认 0
        ("[Demo] 移动端登录改版（临近到期）", "2026-10-20", 5, "正在进行"),
        ("[Demo] 权限模型梳理（临近到期）", "2026-10-19", 3, "正在进行"),
        # R4 故事点过大（低）：> 8
        ("[Demo] 微服务拆分（故事点过大）", "2026-11-15", 13, "正在进行"),
        ("[Demo] 全链路压测方案（故事点过大）", "2026-11-20", 21, "正在进行"),
        # 负载失衡配套：再补 2 个进行中（连同上面共 8 个给 li-rayee）
        ("[Demo] 缓存命中率优化（负载演示）", "2026-11-10", 5, "正在进行"),
        ("[Demo] 日志采集链路改造（负载演示）", "2026-11-12", 3, "正在进行"),
        # R3 反复流转（中）：最终进行中，过程中做 7 次状态跳转
        ("[Demo] 需求反复变更A（反复流转）", "2026-11-05", 5, "CHURN"),
    ]

    created = []
    for summary, due, pts, final in plan:
        key = create_issue(summary, duedate=due, points=pts)
        print(f"  创建 {key}: {summary}")
        created.append((key, final))
        time.sleep(0.6)

    # 设最终状态 / 制造 churn
    churn_cycle = ["正在进行", "审查中", "待办",
                   "正在进行", "审查中", "待办", "正在进行"]
    for key, final in created:
        if final == "CHURN":
            print(f"  [churn] {key} 来回跳转 7 次 ...")
            for st in churn_cycle:
                transition_to(key, st)
                time.sleep(0.4)
        elif final != "待办":
            transition_to(key, final)
            time.sleep(0.4)

    print("\n[完成] 已写入 demo issue。运行 `python run_real.py` 查看风险扫描结果。")


if __name__ == "__main__":
    main()
