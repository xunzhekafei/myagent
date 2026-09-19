import type { Report } from "../types";

export default function ReportView({ report }: { report: Report }) {
  return (
    <div className="report">
      <div className="report-lead">
        <div>
          <span className="eyebrow">YOUR INTERVIEW REVIEW</span>
          <h2>每一次练习，都有收获。</h2>
          <p>{report.summary}</p>
        </div>
        <div className="score">
          <strong>{report.overall}</strong>
          <span>综合评分</span>
        </div>
      </div>
      <div className="dimensions">
        {Object.entries(report.dimension_scores).map(([name, value]) => (
          <div key={name}>
            <span>{name}</span>
            <strong>
              {value.toFixed(1)} <small>/ 10</small>
            </strong>
            <meter min="0" max="10" value={value} />
          </div>
        ))}
      </div>
      <div className="review-grid">
        {[
          ["你的优势", report.strengths],
          ["值得加强", report.weaknesses],
          ["下一步练习", report.recommendations],
        ].map(([title, items]) => (
          <section key={title as string}>
            <h3>{title}</h3>
            <ul>
              {(items as string[]).map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <h3>逐题复盘</h3>
      {report.per_question?.map((item, i) => (
        <details key={i}>
          <summary>
            <span>{String(i + 1).padStart(2, "0")}</span>
            {item.question}
          </summary>
          <div className="question-scores">
            {Object.keys(report.dimension_scores).map((name) => (
              <span key={name}>
                {name}：{String(item[name] ?? "—")}
              </span>
            ))}
          </div>
          <blockquote>{item.evidence}</blockquote>
          <p>{item.suggestion}</p>
        </details>
      ))}
      <button
        className="secondary"
        onClick={() => {
          const url = URL.createObjectURL(
            new Blob([JSON.stringify(report, null, 2)], {
              type: "application/json",
            }),
          );
          const a = document.createElement("a");
          a.href = url;
          a.download = "interview-report.json";
          a.click();
          URL.revokeObjectURL(url);
        }}
      >
        下载完整报告 ↓
      </button>
    </div>
  );
}
