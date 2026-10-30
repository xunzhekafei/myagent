# -*- coding: utf-8 -*-
"""评测的两层判定：确定性检查 + LLM 裁判。

确定性检查便宜、稳定，但只能抓表面问题（一次问几个问句、有没有表扬）。
真正的「有没有问到点子上」交给 LLM 裁判——同一个意思有很多种问法，
关键词匹配一定会误判。
"""
import json
import os
import re

JUDGE_TIMEOUT = 90.0

# 明确的正面反馈。只收「直接评价候选人表现」的说法——
# 「不错」「很好」这类词太容易被转述候选人原话时误伤
#（实测把「你说性能提升很不错」判成了表扬），所以不收。
PRAISE = (
    "答得不错", "回答得不错", "答得很好", "回答得很好", "答得非常好",
    "回答得非常好", "这个回答很", "思路很清晰", "思路清晰",
    "非常棒", "太棒了", "很优秀", "回答得很到位",
)


def deterministic(reply: str, checks: dict) -> tuple[list[str], list[str]]:
    """返回 (failures, hints)。hints 只记录、不判失败。"""
    failures, hints = [], []
    if not reply.strip():
        return ["面试官没有输出任何内容"], []

    if checks.get("one_question"):
        # 两个问号常常是同一个追问的两种说法（「Recall@20 是多少？有没有漏？」），
        # 交给裁判判断是不是两个**独立**的问题；三个以上问号才确定性地判失败。
        marks = reply.count("？") + reply.count("?")
        limit = checks.get("max_questions", 2)
        if marks > limit:
            failures.append(f"一次问了 {marks} 个问句")
        elif marks == limit:
            hints.append(f"有 {marks} 个问号，需确认是否问了两个独立问题")

    if checks.get("no_praise"):
        for word in PRAISE:
            if word in reply:
                failures.append(f"出现正面反馈「{word}」")
                break

    for word in checks.get("forbid") or []:
        if word in reply:
            failures.append(f"出现禁止内容「{word}」")

    limit = checks.get("max_chars")
    if limit and len(reply) > limit:
        failures.append(f"回应过长（{len(reply)} 字，上限 {limit}）")

    require = checks.get("require_any") or []
    if require and not any(word in reply for word in require):
        hints.append(f"未命中提示词（之一即可）：{require}")

    return failures, hints


JUDGE_SYSTEM = """你是面试官行为的评测员，不是面试官本人。你的任务是判断一次「追问」是否达到评测期望。

判断原则：
1. 只有当面试官的回应**确实在向候选人索要**期望的那个信息时，才算达成。
   只是提到了相关词、复述了候选人的话、或者泛泛地说「展开讲讲」，都不算。
2. 如果面试官的回应做了期望里明确说「不该做」的事，判失败。
3. 如果面试官在同一轮里问了两个**相互独立**的问题（例如「团队有几个人？」+
   「哪部分是你做的？」），判失败。同一个问题换种说法、或对同一件事的递进
   说明（例如「测过 Recall@20 吗？取 20 有没有漏？」），不算两个问题。
4. 不要因为措辞风格、长度、语气温和与否扣分——只看是否问到了该问的点。
5. 拿不准时判失败：评测宁可漏掉好的，也不要放过坏的。

只返回一个 JSON 对象，不要任何多余文字：
{"pass": true 或 false, "reason": "一句话说明理由"}"""


def _judge_prompt(case: dict, reply: str) -> str:
    expect = case.get("expect") or {}
    lines = [
        f"岗位：{case.get('role', '技术')}",
        f"压力档位：{case.get('pressure', '标准')}",
        "",
        "之前的对话：",
    ]
    for message in case.get("messages", []):
        who = "面试官" if message["role"] == "assistant" else "候选人"
        lines.append(f"  {who}：{message['text']}")
    lines += [
        "",
        "面试官刚才的回应是：",
        f"「{reply.strip()}」",
        "",
        "这次回应的期望：",
    ]
    for item in expect.get("should", []):
        lines.append(f"  应该：{item}")
    for item in expect.get("should_not", []):
        lines.append(f"  不应该：{item}")
    lines += ["", "请判断这次回应是否达成期望。"]
    return "\n".join(lines)


def _extract_json(text: str):
    """从模型输出里取出第一个完整的 JSON 对象。"""
    start = text.find("{")
    while start != -1:
        depth, in_str, escape = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def judge(case: dict, reply: str, *, model: str | None = None) -> dict:
    """返回 {pass: bool|None, reason: str}。调用失败时 pass=None，不计入通过率。"""
    try:
        import agent
        client = agent.client
        if client is None:
            return {"pass": None, "reason": "未配置模型客户端，跳过裁判"}
        response = client.with_options(timeout=JUDGE_TIMEOUT).messages.create(
            model=model or agent.AUX_MODEL,
            max_tokens=800,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": _judge_prompt(case, reply)}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        value = _extract_json(text)
        if not isinstance(value, dict) or not isinstance(value.get("pass"), bool):
            return {"pass": None, "reason": f"裁判输出无法解析：{text[:80]}"}
        return {"pass": value["pass"], "reason": str(value.get("reason", "")).strip()}
    except Exception as error:                       # 裁判失败不能影响整轮评测
        return {"pass": None, "reason": f"裁判调用失败：{error}"}
