# -*- coding: utf-8 -*-
"""面试官评测集运行器。

    python evals/run_eval.py                      # 追问 + 行为，默认 3 并发
    python evals/run_eval.py --repeat 3           # 每条跑 3 次，看稳定度
    python evals/run_eval.py --group scoring      # 评分质量（慢，会跑完整报告）
    python evals/run_eval.py --case attribution_vague --verbose
    python evals/run_eval.py --no-judge           # 只跑确定性检查，几乎零成本

结果写到 evals/results/latest.md 和 latest.json。
退出码：全部通过为 0，有失败为 1——可以接进 CI。
"""
import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import judge as judge_mod                                  # noqa: E402

CASES = Path(__file__).parent / "cases"
RESULTS = Path(__file__).parent / "results"


# ---------------- 用例加载 ----------------

def load_cases(group: str = "all", only: str = "") -> list[dict]:
    cases = []
    for path in sorted(CASES.glob("*.yaml")):
        for case in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            case.setdefault("group", "followup")
            if group != "all" and case["group"] != group:
                continue
            if only and only not in case["id"]:
                continue
            cases.append(case)
    return cases


# ---------------- 追问 / 行为 ----------------

def _session(case: dict) -> dict:
    return {
        "id": f"eval-{case['id']}",
        "candidate": "评测候选人",
        "role": case.get("role", "技术"),
        "background": case.get("background", ""),
        "pressure": case.get("pressure", "标准"),
        # 用例可以直接给一份笔记状态，用来测「跨轮上下文」的行为，
        # 而不必真的跑一场多轮面试
        "memory": case.get("memory"),
        "messages": [
            {"id": f"m{index}", "role": m["role"], "text": m["text"],
             "status": "completed", "input_mode": "text"}
            for index, m in enumerate(case.get("messages", []))
        ],
    }


def run_followup(case: dict) -> str:
    from backend.agent_adapter import InterviewAgent
    chunks = []

    def emit(event_type, data):
        if event_type == "reply.delta":
            chunks.append(data.get("text", ""))

    text = InterviewAgent().reply(_session(case), emit, threading.Event(), directory=None)
    return (text or "".join(chunks)).strip()


# ---------------- 评分 ----------------

def parse_transcript(text: str) -> list[dict]:
    """把 YAML 里的原始转录拆成消息列表。"""
    messages, role = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("面试官："):
            role = "assistant"
            messages.append({"role": role, "text": line[4:]})
        elif line.startswith("候选人："):
            role = "user"
            messages.append({"role": role, "text": line[4:]})
        elif messages:
            messages[-1]["text"] += "\n" + line
    for index, message in enumerate(messages):
        message.update(id=f"m{index}", status="completed", input_mode="text")
    return messages


def check_evidence(report: dict, messages: list[dict]) -> list[str]:
    """证据必须是候选人的原话，不能是模型自己的概括。

    和生产评分用同一个校验器（agent.validate_evidence_any），避免
    「评测里通过、真实报告里没校验」两套标准。多轮场景允许引用任意一轮
    候选人回答，但不允许跨轮拼接。
    """
    import agent

    answers = [m["text"] for m in messages if m["role"] == "user"]
    failures = []
    for index, question in enumerate(report.get("per_question", []), 1):
        passed, _, reason = agent.validate_evidence_any(
            question.get("evidence", ""), answers)
        if not passed:
            failures.append(f"第 {index} 题证据未通过原话校验（{reason}）")
    return failures


def run_scoring(case: dict) -> tuple[dict, list[str]]:
    import tempfile
    from pathlib import Path as P
    from backend.agent_adapter import InterviewAgent

    session = {
        "id": f"eval-{case['id']}",
        "candidate": "评测候选人",
        "role": case.get("role", "技术"),
        "background": "",
        "pressure": case.get("pressure", "标准"),
        "messages": parse_transcript(case["transcript"]),
    }
    with tempfile.TemporaryDirectory() as tmp:
        report = InterviewAgent().report(session, lambda *a, **k: None,
                                         threading.Event(), P(tmp))

    failures = []
    rules = case.get("assertions") or {}
    overall = report.get("overall")
    if "min_overall" in rules and not (overall is not None and overall >= rules["min_overall"]):
        failures.append(f"总分 {overall}，期望 ≥ {rules['min_overall']}")
    if "max_overall" in rules and not (overall is not None and overall <= rules["max_overall"]):
        failures.append(f"总分 {overall}，期望 ≤ {rules['max_overall']}")
    for dimension, limit in (rules.get("max_dimension") or {}).items():
        got = (report.get("dimension_scores") or {}).get(dimension)
        if got is None or got > limit:
            failures.append(f"{dimension} {got}，期望 ≤ {limit}")
    if rules.get("evidence_from_transcript"):
        failures += check_evidence(report, session["messages"])

    summary = (f"总分 {overall}；"
               + "、".join(f"{k} {v}" for k, v in (report.get("dimension_scores") or {}).items()))
    return {"reply": summary, "report": report}, failures


# ---------------- 单条执行 ----------------

def run_case(case: dict, *, use_judge: bool, judge_model: str | None) -> dict:
    started = time.time()
    record = {"id": case["id"], "group": case["group"], "title": case.get("title", ""),
              "failures": [], "hints": [], "judge": None, "reply": "", "seconds": 0}

    if case["group"] == "scoring":
        payload, failures = run_scoring(case)
        record["reply"] = payload["reply"]
        record["report"] = payload["report"]
        record["failures"] = failures
    else:
        reply = run_followup(case)
        record["reply"] = reply
        failures, hints = judge_mod.deterministic(reply, case.get("checks") or {})
        record["failures"] = failures
        record["hints"] = hints
        if use_judge and reply:
            verdict = judge_mod.judge(case, reply, model=judge_model)
            record["judge"] = verdict
            if verdict["pass"] is False:
                record["failures"].append(f"裁判不通过：{verdict['reason']}")

    record["seconds"] = round(time.time() - started, 1)
    return record


# ---------------- 报告 ----------------

def write_report(results: list[dict], meta: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    judged = [r for r in results if r["judge"] and r["judge"]["pass"] is not None]
    passed = [r for r in results if not r["failures"]]
    rate = len(passed) / len(results) * 100 if results else 0

    lines = [
        "# 面试官评测报告", "",
        f"- 时间：{meta['time']}",
        f"- 模型：{meta['model']}",
        f"- 用例：{len(results)} 条" + (f"，重复 {meta['repeat']} 次" if meta["repeat"] > 1 else ""),
        f"- 通过：**{len(passed)}/{len(results)}（{rate:.0f}%）**",
    ]
    if judged:
        jp = sum(1 for r in judged if r["judge"]["pass"])
        lines.append(f"- 裁判判定：{jp}/{len(judged)} 通过")
    skipped = len(results) - len(judged) - sum(1 for r in results if r["group"] == "scoring")
    if skipped:
        lines.append(f"- 裁判未参与：{skipped} 条")
    lines += ["", "## 逐条结果", "",
              "| 用例 | 组 | 结果 | 耗时 | 说明 |", "| --- | --- | --- | --- | --- |"]
    for r in results:
        mark = "✅" if not r["failures"] else "❌"
        note = "；".join(r["failures"]) or "—"
        note = note.replace("|", "／")[:120]
        lines.append(f"| {r['id']} | {r['group']} | {mark} | {r['seconds']}s | {note} |")

    failed = [r for r in results if r["failures"]]
    if failed:
        lines += ["", "## 失败详情"]
        for r in failed:
            lines += ["", f"### {r['id']} — {r['title']}", "",
                      "**问题**", *[f"- {f}" for f in r["failures"]]]
            if r["hints"]:
                lines += ["", "**提示**", *[f"- {h}" for h in r["hints"]]]
            # 裁判理由已经拼进 failures 了，这里不再重复一遍
            lines += ["", "**面试官实际回应**", "", "```", r["reply"], "```"]

    path = RESULTS / "latest.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    (RESULTS / "latest.json").write_text(
        json.dumps({"meta": meta, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return path


# ---------------- 入口 ----------------

def main():
    parser = argparse.ArgumentParser(description="面试官评测集")
    parser.add_argument("--group", default="followup",
                        choices=["followup", "behavior", "scoring", "all"])
    parser.add_argument("--case", default="", help="按 id 子串筛选")
    parser.add_argument("--repeat", type=int, default=1, help="每条重复几次（看稳定性）")
    parser.add_argument("--jobs", type=int, default=3, help="并发数")
    parser.add_argument("--timeout", type=int, default=900, help="整轮超时（秒）")
    parser.add_argument("--no-judge", action="store_true", help="只跑确定性检查")
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--list", action="store_true", help="只列出用例")
    args = parser.parse_args()

    cases = load_cases(args.group, args.case)
    if not cases:
        print("没有匹配的用例")
        return 2
    if args.list:
        for case in cases:
            print(f"  [{case['group']:<8}] {case['id']:<26} {case.get('title', '')}")
        return 0

    import agent
    if not agent.client:
        print("需要 ANTHROPIC_API_KEY 才能跑评测（--no-judge 也至少要能调面试官）")
        return 2

    tasks = [case for case in cases for _ in range(max(1, args.repeat))]
    print(f"跑 {len(tasks)} 次调用（{len(cases)} 条用例 × {max(1, args.repeat)}），"
          f"并发 {args.jobs}…\n")

    started = time.time()
    records = []
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(run_case, case, use_judge=not args.no_judge,
                               judge_model=args.judge_model): case for case in tasks}
        done, pending = wait(futures, timeout=args.timeout)
        for future in done:
            case = futures[future]
            try:
                records.append(future.result())
            except Exception as error:
                records.append({"id": case["id"], "group": case["group"],
                                "title": case.get("title", ""),
                                "failures": [f"执行失败：{error}"], "hints": [],
                                "judge": None, "reply": "", "seconds": 0})
        for future in pending:
            case = futures[future]
            records.append({"id": case["id"], "group": case["group"],
                            "title": case.get("title", ""),
                            "failures": ["超时未完成"], "hints": [],
                            "judge": None, "reply": "", "seconds": args.timeout})

    order = {case["id"]: index for index, case in enumerate(cases)}
    records.sort(key=lambda r: (order.get(r["id"], 999), r["id"]))

    if args.verbose:
        for record in records:
            mark = "PASS" if not record["failures"] else "FAIL"
            print(f"[{mark}] {record['id']}  {record['seconds']}s")
            print(f"       {record['reply'][:160]}")
            for failure in record["failures"]:
                print(f"       × {failure}")
            print()

    meta = {"time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "model": getattr(agent, "MODEL", "?"),
            "repeat": max(1, args.repeat), "seconds": round(time.time() - started, 1)}
    path = write_report(records, meta)

    passed = sum(1 for record in records if not record["failures"])
    print(f"通过 {passed}/{len(records)}  ({passed / len(records) * 100:.0f}%)"
          f"  用时 {meta['seconds']}s")
    print(f"报告：{path}")
    return 0 if passed == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
