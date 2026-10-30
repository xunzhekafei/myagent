# -*- coding: utf-8 -*-
"""面试蓝图 + 结构化笔记。

解决的是「面试官只看最后一轮」的问题。它其实拿得到完整历史（reply 会带最近
约 6 万字符），但每一轮 system prompt 都是同一份技能，注意力自然锚在最新那句上。
超过 15 万字符还会触发压缩，早期项目细节会被摘要抹掉。

这里维护一份跨轮的状态：

    projects       候选人提到的项目，各自追问到哪一步、还有哪些没问清
    phase          intro / deep_dive / compare / technical / reverse
    compare_points 两个项目之间值得对比的角色差异

每轮回话结束后更新一次（一次快模型调用），下一轮注入 system prompt。
**不改变对话本身**——笔记只是给面试官的提示，不是他说的话。

渲染时用指数降权表达「越近越重要」：

    weight(age) = decay ** age

默认 decay=0.5：最新一轮 1.0、上一轮 0.5、再上一轮 0.25，更早的只保留
结构化事实（事实不衰减）。decay 可用 INTERVIEW_DECAY 调整。

任何一步失败都返回上一版笔记——**绝不能因为笔记挂了就让面试无法继续**。
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

MEMORY_VERSION = 1
DEFAULT_DECAY = 0.5
MEMORY_TIMEOUT = 30.0
TIMELINE_KEEP = 8

PHASES = ("intro", "deep_dive", "compare", "technical", "reverse")
DIMENSIONS = ("归属", "指标", "技术", "架构", "结果", "失败", "协作")

SYSTEM = """你在维护一场模拟面试的结构化笔记。根据「已有笔记」和「最新一轮问答」更新笔记。

规则：
1. projects 只记录候选人明确提到的项目，不要臆造。同一个项目用同一个名字。
2. role_claim 抄他在这个项目里的角色表述，尽量用原话短语。
3. dimensions_asked 只能取：归属、指标、技术、架构、结果、失败、协作。
   已经追问过的才记，没问过的不记。
4. open_threads 记「问到了但他没答清」的点，写成一句可直接再问的话。
5. turns 是这个项目累计追问的轮数。
6. done 表示这个项目已经问清至少两个维度、可以进入下一个项目。
7. phase 取值：intro / deep_dive / compare / technical / reverse。
   compare 只有在至少两个项目都 done 之后才允许。
8. compare_points 写两个项目之间值得对比的**角色或决策差异**，
   写成一句可以直接问出来的话。没有两个项目就留空。
9. answer_summary 用一句话总结候选人这一轮回答，不超过 60 字。
10. focus：这一轮正在追的主题，用来决定下一步的深度。
    - topic：主题名，例如「缓存一致性」；没有明确主题就留空
    - depth：当前深度，1-4（1 概念/背景，2 原理/实践，3 边界/细节，4 取舍/极限）
    - quality：本轮回答质量，0-1。有具体数据、取舍和边界 = 高；
      方向对但空泛 = 中等；答非所问、明显错误或基本没答 = 低
    不需要输出 next_action——系统会根据 quality、depth、阶段和压力档位决定。

只返回 JSON，不要多余文字：
{"projects": [{"name": "", "role_claim": "", "metrics": [], "tech": [],
  "dimensions_asked": [], "open_threads": [], "turns": 0, "done": false}],
 "phase": "deep_dive", "compare_points": [], "answer_summary": "",
 "focus": {"topic": "", "depth": 1, "quality": 0.5}}"""

DEPTH_CAP = {"温和": 2, "标准": 3, "压力": 4}

# 动作由代码决定，模型只判断回答质量。文案放在这里，渲染时原样注入。
ACTION_TEXT = {
    "deeper": ("深入一层", "顺着概念 → 原理 → 边界 → 取舍再往下追一层",
               "换主题、换项目、问一个全新的问题"),
    "angle": ("同层换角度", "换个场景、换个规模或问代价，还是同一件事",
              "换主题、换项目、原样重复"),
    "switch_topic": ("换主题", "简短收尾这个点，转向同一项目里的另一个主题；项目问完了再换项目",
                     "继续在这个点上追问"),
    "switch_project": ("换项目", "转向另一个还没深挖的项目",
                       "继续追问已完成的项目"),
    "compare": ("跨项目对比", "对比两个项目里的角色、决策权或结果归属",
                "继续追问单个项目的技术细节"),
    "technical": ("进入技术问答", "从题库选题，一次一题", "继续深挖项目"),
    "wrap_up": ("进入反问/收尾", "问他有什么想问的", "继续出题"),
}


# ---------------- 基础 ----------------

def empty() -> dict:
    return {"version": MEMORY_VERSION, "projects": [], "phase": "intro",
            "compare_points": [], "focus": {}, "timeline": []}


def enabled() -> bool:
    """INTERVIEW_MEMORY=off 时完全退回旧行为（不注入、不更新、不额外调用模型）。"""
    return os.environ.get("INTERVIEW_MEMORY", "on").strip().lower() not in (
        "off", "0", "false", "no")


def decay() -> float:
    try:
        value = float(os.environ.get("INTERVIEW_DECAY", DEFAULT_DECAY))
    except ValueError:
        return DEFAULT_DECAY
    return value if 0.1 <= value <= 0.95 else DEFAULT_DECAY


def _weight(age: int, alpha: float) -> float:
    return alpha ** max(0, age)


def _last_qa(messages: list) -> tuple[str, str]:
    """取最近一轮问答：候选人最后一条回答，以及它前面面试官问的那句。"""
    answer_index = None
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user" and messages[index].get("status") == "completed":
            answer_index = index
            break
    if answer_index is None:
        return "", ""
    question = ""
    for index in range(answer_index - 1, -1, -1):
        if messages[index].get("role") == "assistant":
            question = messages[index].get("text", "")
            break
    return question, messages[answer_index].get("text", "")


# ---------------- 更新 ----------------

def _call(prompt: str):
    import agent
    client = agent.client
    if client is None:
        return None
    response = client.with_options(timeout=MEMORY_TIMEOUT).messages.create(
        model=agent.AUX_MODEL,
        max_tokens=1600,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return agent._extract_json_object(text)


def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _enforce(state: dict, previous: dict | None = None, pressure: str = "标准") -> dict:
    """模型会违反自己该守的规则，这里兜底。

    深度阶梯的动作在这里**由代码决定**，不交给模型：模型只判断回答质量，
    代码按 quality / depth / 阶段 / 压力档位算出下一步。
    """
    projects = []
    for raw in state.get("projects") or []:
        if not isinstance(raw, dict):
            continue
        name = _clean(raw.get("name"), 40)
        if not name:
            continue
        dimensions = [d for d in (raw.get("dimensions_asked") or []) if d in DIMENSIONS]
        projects.append({
            "name": name,
            "role_claim": _clean(raw.get("role_claim"), 120),
            "metrics": [_clean(m, 60) for m in (raw.get("metrics") or []) if _clean(m, 60)][:6],
            "tech": [_clean(t, 40) for t in (raw.get("tech") or []) if _clean(t, 40)][:8],
            "dimensions_asked": list(dict.fromkeys(dimensions)),
            "open_threads": [_clean(t, 120) for t in (raw.get("open_threads") or []) if _clean(t, 120)][:5],
            "turns": max(0, int(raw.get("turns") or 0)),
            "done": bool(raw.get("done")) and len(set(dimensions)) >= 2,
        })

    phase = state.get("phase") if state.get("phase") in PHASES else "deep_dive"
    ready = [p for p in projects if p["done"]]
    if not projects:
        phase = "intro"
    elif phase == "intro":
        phase = "deep_dive"
    elif phase == "compare" and len(ready) < 2:
        phase = "deep_dive"          # 只有一个项目就问不出对比，退回深挖

    points = [_clean(p, 160) for p in (state.get("compare_points") or []) if _clean(p, 160)]

    # ---- 追问深度阶梯：模型只给 quality，动作由代码算 ----
    raw_focus = state.get("focus") or {}
    topic = _clean(raw_focus.get("topic"), 40)
    try:
        depth = max(1, min(4, int(raw_focus.get("depth") or 1)))
    except (TypeError, ValueError):
        depth = 1
    try:
        quality = max(0.0, min(1.0, float(raw_focus.get("quality"))))
    except (TypeError, ValueError):
        quality = 0.5

    previous_focus = (previous or {}).get("focus") or {}
    try:
        previous_streak = max(0, int(previous_focus.get("weak_streak") or 0))
    except (TypeError, ValueError):
        previous_streak = 0
    weak_streak = min(3, previous_streak + 1) if quality < 0.40 else 0

    cap = DEPTH_CAP.get(pressure, 3)
    done_names = [p for p in projects if p["done"]]
    pending = [p for p in projects if not p["done"]]
    all_done = len(projects) >= 2 and not pending

    if phase == "compare" or (phase == "deep_dive" and all_done):
        action = "compare"
    elif phase == "technical":
        action = "technical"
    elif phase == "reverse":
        action = "wrap_up"
    elif len(done_names) >= 1 and pending:
        action = "switch_project"
    elif weak_streak >= 2:
        action = "switch_topic"
    elif depth >= cap:
        action = "switch_topic"
    elif quality >= 0.65:
        action = "deeper"
    else:
        action = "angle"

    focus = {"topic": topic, "depth": depth, "depth_cap": cap,
             "quality": round(quality, 2), "weak_streak": weak_streak,
             "next_action": action}

    return {"projects": projects[:4], "phase": phase,
            "compare_points": points[:3] if len(projects) >= 2 else [],
            "focus": focus}


def update(session: dict, reply_text: str = "") -> dict:
    """用最近一轮问答更新笔记。失败时原样返回旧笔记。"""
    previous = session.get("memory") or empty()
    messages = session.get("messages") or []
    question, answer = _last_qa(messages)
    if not answer:
        return previous

    pressure = session.get("pressure") or "标准"
    prompt = "\n".join([
        f"压力档位：{pressure}",
        "",
        "已有笔记：",
        json.dumps({k: previous.get(k) for k in
                    ("projects", "phase", "compare_points", "focus")},
                   ensure_ascii=False),
        "",
        "最新一轮：",
        f"  面试官问：{question}",
        f"  候选人答：{answer}",
        "",
        "请输出更新后的完整笔记。",
    ])

    try:
        state = _call(prompt)
    except Exception:
        logger.exception("面试笔记更新失败，沿用上一版")
        return previous
    if not isinstance(state, dict):
        return previous

    cleaned = _enforce(state, previous, pressure)
    timeline = list(previous.get("timeline") or [])
    summary = _clean(state.get("answer_summary"), 80) or _clean(answer, 80)
    timeline.append({"question": _clean(question, 80), "answer": summary})
    return {
        "version": MEMORY_VERSION,
        "projects": cleaned["projects"],
        "phase": cleaned["phase"],
        "compare_points": cleaned["compare_points"],
        "focus": cleaned["focus"],
        "timeline": timeline[-TIMELINE_KEEP:],
    }


# ---------------- 渲染 ----------------

def _phase_line(state: dict) -> str:
    phase = state.get("phase")
    if phase == "intro":
        return "开场——先请候选人做自我介绍。"
    if phase == "compare":
        return ("两个项目都深挖过了。现在做一次**跨项目对比**："
                "问他在这两个项目里的角色、决策权或结果归属有什么不同。")
    if phase == "technical":
        return "进入技术问答：从题库选题，一次一题。"
    if phase == "reverse":
        return "进入反问环节：问他有什么想问面试官的。"
    projects = state.get("projects") or []
    done = [p["name"] for p in projects if p["done"]]
    pending = [p["name"] for p in projects if not p["done"]]
    if len(projects) < 2:
        return "项目深挖中。候选人只提到一个项目，把它问透即可，不要硬凑对比。"
    if pending:
        # 「问完当前维度再换」会让模型再找一个新维度继续问；这里必须说死。
        return (f"已完成：{'、'.join(done) or '（无）'}；待深挖：{'、'.join(pending)}。"
                f"**下一个问题必须转向 {'、'.join(pending)}**，不要再追问已完成的项目。")
    return "两个项目都已深挖完，进入对比阶段。"


def render(memory: dict | None, messages: list, alpha: float | None = None) -> str:
    """把笔记渲染成注入 system prompt 的文本块。没有内容时返回空串。"""
    state = memory or {}
    projects = state.get("projects") or []
    if not projects and not state.get("timeline"):
        return ""

    alpha = decay() if alpha is None else alpha
    question, answer = _last_qa(messages)
    lines = ["", "【面试进度 · 系统维护的结构化笔记】",
             "下面的权重表示每轮内容的重要性：越近越高，但**更早说过的事实不会因为久远而失效**。"]

    if question or answer:
        lines += ["", f"■ 最近焦点（权重 {_weight(0, alpha):.2f}）",
                  f"  你问：{question.strip()[:300]}",
                  f"  他答：{answer.strip()[:700]}"]

    timeline = state.get("timeline") or []
    # timeline 里存的是**已经结束**的轮次；当前这一轮在 messages 里、上面已原文展示。
    # 所以最近的一条 timeline 就是「前一轮」，权重按 age=1 起算。
    for age, entry in enumerate(reversed(timeline[-3:]), start=1):
        lines += ["", f"■ 前一轮（权重 {_weight(age, alpha):.2f}）",
                  f"  问：{entry.get('question', '')[:120]}",
                  f"  答：{entry.get('answer', '')[:160]}"]

    if projects:
        lines += ["", "■ 已积累的事实（不衰减）"]
        for project in projects:
            bits = [f"· {project['name']}（已问 {project['turns']} 轮）"]
            if project.get("role_claim"):
                bits.append(f"角色：{project['role_claim']}")
            if project.get("metrics"):
                bits.append(f"指标：{'、'.join(project['metrics'])}")
            if project.get("tech"):
                bits.append(f"技术：{'、'.join(project['tech'])}")
            if project.get("dimensions_asked"):
                bits.append(f"已问维度：{'、'.join(project['dimensions_asked'])}")
            if project.get("open_threads"):
                bits.append(f"没答清：{'；'.join(project['open_threads'])}")
            lines.append("  " + " ｜ ".join(bits))

    # 深度阶梯：把代码算出的动作写死在提示里，并同时给出「要做 / 不要做」
    focus = state.get("focus") or {}
    action = focus.get("next_action")
    if action in ACTION_TEXT:
        title, do, avoid = ACTION_TEXT[action]
        lines += ["", f"■ 本轮动作（确定性规则，必须遵守）：{title}"]
        if focus.get("topic"):
            lines.append(
                f"  当前主题：{focus['topic']}"
                f"（第 {focus.get('depth', 1)}/{focus.get('depth_cap', 3)} 层）")
        quality = focus.get("quality")
        if isinstance(quality, (int, float)):
            lines.append(f"  上一轮回答质量：{quality:.2f}")
        lines += [f"  要做：{do}", f"  不要做：{avoid}"]

    if action not in ("switch_project", "compare", "technical", "wrap_up"):
        lines += ["", f"■ 下一步：{_phase_line(state)}"]

    points = state.get("compare_points") or []
    if points and state.get("phase") == "compare":
        lines += ["", "■ 可对比的点（挑一个问，不要一次全问）", *[f"  · {p}" for p in points]]

    lines += ["", "注意：以上是内部笔记，**不要向候选人复述**，也不要提到「笔记」「权重」这些词。"]
    return "\n".join(lines)
