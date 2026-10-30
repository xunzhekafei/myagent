import type { ScoredQuestion } from "../types";

const DIMENSIONS = ["技术正确性", "深度与原理", "工程与场景思考", "表达与结构"];

type Entry = { index: number; total: number; item: ScoredQuestion };

/** 报告生成中的逐题预览：评完一题就显示一题，不用等整份报告。 */
export default function LiveReport({
  questions,
  progress,
}: {
  questions: Entry[];
  progress: string;
}) {
  const total = questions[0]?.total ?? 0;
  const percent = total ? Math.min(100, (questions.length / total) * 100) : 0;

  return (
    <div className="live-report">
      <div className="live-head">
        <span className="eyebrow">GENERATING REPORT</span>
        <h2>{progress || "正在逐题评分…"}</h2>
        <p>
          已评 {questions.length}
          {total ? ` / ${total}` : ""} 题 · 结果边评边显示
        </p>
        <div className="live-bar">
          <span style={{ width: `${percent}%` }} />
        </div>
      </div>

      <ol className="live-questions">
        {questions.map(({ index, item }) => (
          <li key={index}>
            <div className="live-q">
              <b>{String(index + 1).padStart(2, "0")}</b>
              <span>{item.question}</span>
            </div>
            <div className="live-dims">
              {DIMENSIONS.map((name) => {
                const value = item[name];
                return typeof value === "number" ? (
                  <span key={name}>
                    {name} <strong>{value.toFixed(1)}</strong>
                  </span>
                ) : null;
              })}
            </div>
            {item.evidence && (
              <p className="live-evidence">证据：{item.evidence}</p>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
