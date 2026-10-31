# -*- coding: utf-8 -*-
"""评分校准：把「AI 打的分靠不靠谱」变成一个可以算出来的数。

做法参考 echo-interview：
1. 从已生成报告的场次里导出评测包（问题 + 候选人回答 + AI 各维度分）
2. 两个以上的人**盲评**（只看问题和回答，不看 AI 分数），每维度 1-5 分
3. 算三件事：
   · 人-人一致性（天花板）：真人评委两两 Spearman ρ 的均值
   · AI-人一致性：AI 分 vs 人类均分的 Spearman ρ
   · AI/人比值：AI 达到人类一致性的百分比
4. 小样本一律报 bootstrap 95% 区间，不裸报点估计

这个脚本不调用模型——AI 分数直接读报告里已经存好的。

用法：
    python evals/calibration.py template --session <场次ID>
    python evals/calibration.py report
"""
import argparse
import csv
import json
import random
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from interview_spec import DIMENSION_NAMES  # noqa: E402

DATA = ROOT / "evals" / "calibration-data"
PACKETS = DATA / "packets.json"
RATINGS = DATA / "ratings.csv"
REPORT = DATA / "calibration.md"
RUBRIC = DATA / "rubric.md"

MIN_PACKETS = 10
MIN_RATERS = 2


# ---------------- 统计 ----------------

def _rank(values: list) -> list:
    """平均秩（ties 取平均），用于 Spearman 的 tie 修正。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        rank = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[order[position]] = rank
        index = end + 1
    return ranks


def spearman(x: list, y: list) -> float | None:
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = _rank(x), _rank(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy) ** 0.5


def bootstrap_ci(pairs: list, iterations: int = 2000, seed: int = 42) -> tuple:
    """对 (ai, human) 有放回重采样，返回 ρ 的百分位 95% 区间。"""
    if len(pairs) < 3:
        return (None, None)
    rng = random.Random(seed)
    values = []
    for _ in range(iterations):
        sample = [pairs[rng.randrange(len(pairs))] for _ in range(len(pairs))]
        rho = spearman([a for a, _ in sample], [h for _, h in sample])
        if rho is not None:
            values.append(rho)
    if not values:
        return (None, None)
    values.sort()
    return (round(values[int(0.025 * len(values))], 3),
            round(values[int(0.975 * len(values)) - 1], 3))


def normalize_ai(score) -> float | None:
    """AI 的 0-10 分映射到人评的 1-5 分。"""
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return None
    return round(1.0 + max(0.0, min(10.0, float(score))) * 0.4, 3)


def _human_ratings(ratings: list) -> dict:
    """rater -> packet_id -> {dimension: score}"""
    humans = {}
    for row in ratings:
        rater = str(row.get("rater") or "").strip()
        packet = str(row.get("packet_id") or "").strip()
        if not rater or not packet:
            continue
        scores = {}
        for dim in DIMENSION_NAMES:
            value = row.get(dim)
            try:
                scores[dim] = float(value)
            except (TypeError, ValueError):
                continue
        if scores:
            humans.setdefault(rater, {})[packet] = scores
    return humans


def compute(packets: list, ratings: list) -> dict:
    packet_ids = [p["id"] for p in packets]
    ai = {p["id"]: p.get("ai_scores") or {} for p in packets}
    humans = _human_ratings(ratings)

    human_mean = {}
    for packet_id in packet_ids:
        human_mean[packet_id] = {}
        for dim in DIMENSION_NAMES:
            values = [humans[r][packet_id][dim] for r in humans
                      if dim in humans[r].get(packet_id, {})]
            if values:
                human_mean[packet_id][dim] = mean(values)

    result = {
        "packets": len(packet_ids),
        "raters": len(humans),
        "warnings": [],
        "dimensions": {},
    }
    if len(packet_ids) < MIN_PACKETS:
        result["warnings"].append(f"评测包只有 {len(packet_ids)} 个，建议至少 {MIN_PACKETS} 个")
    if len(humans) < MIN_RATERS:
        result["warnings"].append(f"评委只有 {len(humans)} 位，建议至少 {MIN_RATERS} 位")

    for dim in DIMENSION_NAMES:
        pairs = []
        for packet_id in packet_ids:
            ai_value = normalize_ai(ai[packet_id].get(dim))
            human_value = human_mean.get(packet_id, {}).get(dim)
            if ai_value is not None and human_value is not None:
                pairs.append((ai_value, human_value))

        human_rhos = []
        raters = list(humans)
        for i in range(len(raters)):
            for j in range(i + 1, len(raters)):
                left, right = raters[i], raters[j]
                common = [pid for pid in packet_ids
                          if dim in humans[left].get(pid, {})
                          and dim in humans[right].get(pid, {})]
                if len(common) >= 3:
                    rho = spearman([humans[left][pid][dim] for pid in common],
                                   [humans[right][pid][dim] for pid in common])
                    if rho is not None:
                        human_rhos.append(rho)

        baseline = round(mean(human_rhos), 3) if human_rhos else None
        rho = spearman([a for a, _ in pairs], [h for _, h in pairs]) if len(pairs) >= 3 else None
        lo, hi = bootstrap_ci(pairs)
        ratio = (round(rho / baseline, 3)
                 if rho is not None and baseline not in (None, 0) else None)
        same = sum(1 for a, h in pairs if (a >= 3) == (h >= 3))
        result["dimensions"][dim] = {
            "n": len(pairs),
            "ai_human_rho": round(rho, 3) if rho is not None else None,
            "ci95": [lo, hi],
            "human_baseline_rho": baseline,
            "ai_vs_human": ratio,
            "direction_rate": round(same / len(pairs), 3) if pairs else None,
        }

    valid = [v["ai_human_rho"] for v in result["dimensions"].values()
             if v["ai_human_rho"] is not None]
    baselines = [v["human_baseline_rho"] for v in result["dimensions"].values()
                 if v["human_baseline_rho"] is not None]
    if valid and baselines and mean(baselines) != 0:
        result["overall"] = {
            "ai_human_rho_mean": round(mean(valid), 3),
            "human_rho_mean": round(mean(baselines), 3),
            "headline": f"AI-人一致性是人-人一致性的 "
                        f"{round(100 * mean(valid) / mean(baselines))}%",
        }
    else:
        result["overall"] = {"headline": "样本不足，无法给出总体一致性"}
    return result


# ---------------- 模板 ----------------

def _pairs(session: dict) -> list:
    """把一场面试的问答配对出来（面试官的话配下一条候选人回答）。"""
    pairs, question = [], None
    for message in session.get("messages") or []:
        if message.get("status") != "completed":
            continue
        if message["role"] == "assistant":
            question = message.get("text", "")
        elif question is not None:
            pairs.append((question, message.get("text", "")))
            question = None
    return pairs


def build_packets(sessions: list) -> list:
    packets = []
    for session in sessions:
        report = session.get("report")
        if session.get("status") != "completed" or not isinstance(report, dict):
            continue
        per_question = report.get("per_question") or []
        for index, (question, answer) in enumerate(_pairs(session)):
            if index >= len(per_question):
                break
            scores = {dim: per_question[index].get(dim) for dim in DIMENSION_NAMES
                      if isinstance(per_question[index].get(dim), (int, float))}
            if not scores:
                continue
            packets.append({
                "id": f"{session['id'][:8]}-{index + 1:02d}",
                "session": session["id"],
                "role": session.get("role") or "技术",
                "question": question,
                "answer": answer,
                "ai_scores": scores,
            })
    return packets


def write_template(sessions: list, out: Path = DATA):
    out.mkdir(parents=True, exist_ok=True)
    packets = build_packets(sessions)
    if not packets:
        raise SystemExit("没有可导出的问答对——先完成至少一场面试并生成报告")
    (out / "packets.json").write_text(
        json.dumps(packets, ensure_ascii=False, indent=2), encoding="utf-8")

    with (out / "ratings.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["packet_id", "rater", *DIMENSION_NAMES])
        for packet in packets:
            writer.writerow([packet["id"], "", *([""] * len(DIMENSION_NAMES))])

    (out / "rubric.md").write_text(
        "# 盲评说明\n\n"
        "只看「问题 + 候选人回答」，**不要看 AI 分数和报告**。\n"
        "每个维度按 1–5 分打：\n\n"
        "| 分数 | 含义 |\n| --- | --- |\n"
        "| 1 | 基本没答到，或核心概念错误 |\n"
        "| 2 | 方向沾边，但空泛、缺证据 |\n"
        "| 3 | 基本对题，有部分细节，但边界或取舍说不清 |\n"
        "| 4 | 结论正确，原理清楚，有证据，细节略欠 |\n"
        "| 5 | 结论正确，讲清取舍与边界，有量化或具体场景 |\n\n"
        "填 `ratings.csv`：每行一位评委对一个评测包的打分；"
        "不同评委用不同的 `rater` 名字，重复行即可。\n",
        encoding="utf-8")

    print(f"已生成 {len(packets)} 个评测包：{out}")
    print("  1. 把 packets.json 发给评委（只发这个文件，不要发报告）")
    print("  2. 评委按 rubric.md 填 ratings.csv")
    print("  3. python evals/calibration.py report")


def write_report(out: Path = DATA):
    packets = json.loads((out / "packets.json").read_text(encoding="utf-8"))
    with (out / "ratings.csv").open(encoding="utf-8-sig") as handle:
        ratings = list(csv.DictReader(handle))
    result = compute(packets, ratings)

    lines = ["# 评分校准报告", "",
             f"- 评测包：{result['packets']} 个",
             f"- 评委：{result['raters']} 位", ""]
    for warning in result["warnings"]:
        lines.append(f"> ⚠️ {warning}")
    if result["warnings"]:
        lines.append("")

    lines += ["## 结果", "",
              "| 维度 | AI-人 ρ | 95% CI | 人-人 ρ | AI / 人 | 同向率 |",
              "| --- | ---: | --- | ---: | ---: | ---: |"]
    for dim, data in result["dimensions"].items():
        ci = data["ci95"]
        ci_text = f"[{ci[0]}, {ci[1]}]" if ci[0] is not None else "—"
        ratio = f"{data['ai_vs_human']:.0%}" if data["ai_vs_human"] is not None else "—"
        direction = f"{data['direction_rate']:.0%}" if data["direction_rate"] is not None else "—"
        lines.append(
            f"| {dim} | {data['ai_human_rho'] if data['ai_human_rho'] is not None else '—'} "
            f"| {ci_text} | {data['human_baseline_rho'] if data['human_baseline_rho'] is not None else '—'} "
            f"| {ratio} | {direction} |")

    lines += ["", f"**{result['overall']['headline']}**", "",
              "## 怎么读", "",
              "- `AI / 人` 是 AI-人 ρ 与人-人 ρ 的比值：1.0 表示和人类评委一样一致；"
              "**超过 1.0 是可能的**——人类均值的噪声比单个评委小，不要把它当成分数更高的证据。"
              "0.8 以上通常算好，0.6 以下值得回头查评分 prompt。",
              "- 人-人 ρ 是天花板：如果真人之间也只有 0.5，就不要要求 AI 更高。",
              "- 样本小的时候区间会很宽；看区间，不要只看点估计。",
              "- 同向率只看「两侧是否都在量表上半区/下半区」，用来看 AI 有没有整体偏松/偏严。", ""]

    target = out / "calibration.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    (out / "calibration.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(target.read_text(encoding="utf-8"))


# ---------------- CLI ----------------

def main():
    parser = argparse.ArgumentParser(description="评分校准")
    sub = parser.add_subparsers(dest="command", required=True)

    template = sub.add_parser("template", help="从已生成报告的场次导出评测包")
    template.add_argument("--session", default="", help="只导出指定场次；不填则导出全部")
    template.add_argument("--db", default=str(ROOT / ".web-data" / "interviews.sqlite3"))
    template.add_argument("--out", default=str(DATA))

    sub.add_parser("report", help="根据 packets.json 和 ratings.csv 生成校准报告")

    args = parser.parse_args()
    if args.command == "template":
        from backend.sessions import SessionStore
        store = SessionStore(Path(args.db))
        sessions = store.list()
        store.close()
        if args.session:
            sessions = [s for s in sessions if s.get("id", "").startswith(args.session)]
        write_template(sessions, Path(args.out))
    else:
        write_report()


if __name__ == "__main__":
    main()
