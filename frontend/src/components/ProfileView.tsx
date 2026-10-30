import { useEffect, useState } from "react";
import { api } from "../api";
import type { Profile } from "../types";

function shortDate(iso: string) {
  const value = new Date(iso);
  return `${value.getMonth() + 1}/${value.getDate()}`;
}

function signed(value: number) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

/** 总分随场次变化的折线。故意不引图表库——三个点、一条线，SVG 足够。 */
function TrendChart({ timeline }: { timeline: Profile["timeline"] }) {
  const width = 640;
  const height = 190;
  const pad = 34;
  const count = timeline.length;
  const x = (index: number) =>
    count === 1 ? width / 2 : pad + (index * (width - 2 * pad)) / (count - 1);
  const y = (value: number) =>
    height - pad - (Math.max(0, Math.min(10, value)) / 10) * (height - 2 * pad);

  return (
    <svg
      className="trend-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="历次面试总分变化"
    >
      {[0, 5, 10].map((value) => (
        <g key={value}>
          <line x1={pad} x2={width - pad} y1={y(value)} y2={y(value)} className="grid" />
          <text x={pad - 10} y={y(value) + 4} className="axis" textAnchor="end">
            {value}
          </text>
        </g>
      ))}
      {count > 1 && (
        <polyline
          points={timeline.map((entry, i) => `${x(i)},${y(entry.overall)}`).join(" ")}
          className="line"
        />
      )}
      {timeline.map((entry, i) => (
        <g key={entry.id}>
          <circle cx={x(i)} cy={y(entry.overall)} r="4.5" className="dot" />
          <text x={x(i)} y={y(entry.overall) - 13} className="value" textAnchor="middle">
            {entry.overall.toFixed(1)}
          </text>
          <text x={x(i)} y={height - 10} className="axis" textAnchor="middle">
            {shortDate(entry.created_at)}
          </text>
        </g>
      ))}
    </svg>
  );
}

export default function ProfileView() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Profile>("/profile")
      .then(setProfile)
      .catch((reason: Error) => setError(reason.message));
  }, []);

  if (error) {
    return (
      <div className="profile">
        <p className="profile-empty">没能读取练习记录：{error}</p>
      </div>
    );
  }
  if (!profile) return <div className="loading">正在整理练习记录…</div>;

  if (profile.sessions === 0 || !profile.overall) {
    return (
      <div className="profile">
        <span className="eyebrow">YOUR GROWTH</span>
        <h1>还没有可对比的记录。</h1>
        <p>完成至少一场面试并生成报告后，这里会出现你的能力曲线。</p>
      </div>
    );
  }

  const overall = profile.overall;
  const weakest = profile.weakest.map((item) => item.name);

  return (
    <div className="profile">
      <div className="profile-head">
        <div>
          <span className="eyebrow">YOUR GROWTH</span>
          <h1>看到自己在变好。</h1>
          <p>基于 {profile.sessions} 场已生成报告的面试。</p>
        </div>
        <div className="score">
          <strong>{overall.latest.toFixed(1)}</strong>
          <span>最近一场 · 综合评分</span>
        </div>
      </div>

      <div className="profile-stats">
        <div>
          <span>总场次</span>
          <strong>{profile.sessions}</strong>
        </div>
        <div>
          <span>平均分</span>
          <strong>{overall.average.toFixed(2)}</strong>
        </div>
        <div>
          <span>首场 → 最近</span>
          <strong>
            {overall.first.toFixed(1)} → {overall.latest.toFixed(1)}
          </strong>
        </div>
        <div className={overall.delta >= 0 ? "up" : "down"}>
          <span>变化</span>
          <strong>{signed(overall.delta)}</strong>
        </div>
      </div>

      <section className="profile-section">
        <h2>总分趋势</h2>
        <TrendChart timeline={profile.timeline} />
      </section>

      <section className="profile-section">
        <h2>四个维度</h2>
        <div className="profile-dims">
          {Object.entries(profile.dimensions).map(([name, trend]) => (
            <div key={name} className={weakest.includes(name) ? "weak" : ""}>
              <span>
                {name}
                {weakest.includes(name) && <i>相对最低</i>}
              </span>
              <strong>{trend.average.toFixed(2)}</strong>
              <small>
                {trend.first.toFixed(1)} → {trend.latest.toFixed(1)}（
                {signed(trend.delta)}）
              </small>
              <meter min="0" max="10" value={trend.average} />
            </div>
          ))}
        </div>
      </section>

      <section className="profile-section">
        <h2>不同岗位</h2>
        <div className="profile-roles">
          {profile.roles.map((item) => (
            <div key={item.role}>
              <strong>{item.role}</strong>
              <span>
                {item.sessions} 场 · 均分 {item.average.toFixed(2)}
              </span>
              <div className="role-bars">
                {Object.entries(item.dimensions)
                  .filter(([, value]) => typeof value === "number")
                  .map(([name, value]) => (
                    <small key={name}>
                      {name} {value.toFixed(1)}
                    </small>
                  ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      {(profile.recurring.length > 0 || profile.recent_weaknesses.length > 0) && (
        <section className="profile-section">
          <h2>
            {profile.recurring.length > 0 ? "反复出现的短板" : "最近两场的短板"}
          </h2>
          {profile.recurring.length > 0 && (
            <ul className="recurring">
              {profile.recurring.map((item) => (
                <li key={item.text}>
                  <b>{item.count} 次</b> {item.text}
                </li>
              ))}
            </ul>
          )}
          {profile.recent_weaknesses.length > 0 && (
            <div className="recent-weak">
              {profile.recent_weaknesses.map((item, index) => (
                <p key={index}>
                  <span>
                    {item.role} · {shortDate(item.created_at)}
                  </span>
                  {item.text}
                </p>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
