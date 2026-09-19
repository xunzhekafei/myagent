import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import { api, connectSession } from "./api";
import type { Action, Session, SessionSummary } from "./types";
import ReportView from "./components/ReportView";

const labels: Record<string, string> = {
  ready: "待继续",
  running: "面试中",
  scoring: "评分中",
  completed: "已完成",
};
const toolLabels: Record<string, string> = {
  search_questions: "正在选择适合你的问题",
  load_skill: "正在准备面试",
};
const date = (value: string) =>
  new Date(value).toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  });

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(() =>
    localStorage.getItem("interview.session"),
  );
  const [session, setSession] = useState<Session | null>(null);
  const [connected, setConnected] = useState(false);
  const [health, setHealth] = useState<{ configured: boolean } | null>(null);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const [draft, setDraft] = useState("");
  const [candidate, setCandidate] = useState("");
  const [role, setRole] = useState("AI / ML 工程师");
  const [background, setBackground] = useState("");
  const [creating, setCreating] = useState(false);
  const [pending, setPending] = useState(false);
  const [view, setView] = useState<"chat" | "report">("chat");
  const socket = useRef<WebSocket | null>(null);
  const pendingCommand = useRef<{
    action: Action;
    text: string;
    request_id: string;
  } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const refresh = useCallback(
    () =>
      api<SessionSummary[]>("/sessions")
        .then(setSessions)
        .catch((e) => setError(e.message)),
    [],
  );
  useEffect(() => {
    void refresh();
    api<{ configured: boolean }>("/health")
      .then(setHealth)
      .catch((e) => setError(e.message));
  }, [refresh]);
  useEffect(() => {
    setSession(null);
    setDraft("");
    setError("");
    setProgress("");
    setView("chat");
    setPending(false);
    pendingCommand.current = null;
    if (!selected) {
      localStorage.removeItem("interview.session");
      return;
    }
    localStorage.setItem("interview.session", selected);
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let ws: WebSocket;
    let lastSeq = -1;
    const acknowledge = () => {
      const command = pendingCommand.current;
      if (command?.action === "answer")
        setDraft((current) => (current === command.text ? "" : current));
      pendingCommand.current = null;
      setPending(false);
    };
    const open = () => {
      ws = connectSession(selected);
      socket.current = ws;
      ws.onmessage = (event) => {
        if (disposed) return;
        const message = JSON.parse(event.data);
        if (message.type === "session.snapshot" && message.seq >= lastSeq) {
          lastSeq = message.seq;
          setSession(message.data);
          setConnected(true);
          if (
            pendingCommand.current &&
            message.data.requests.includes(pendingCommand.current.request_id)
          )
            acknowledge();
          if (!message.data.active_turn) {
            setProgress("");
            void refresh();
          }
        }
        if (message.type === "command.accepted") acknowledge();
        if (message.type === "command.error") {
          setError(message.data.message);
          setPending(false);
          pendingCommand.current = null;
        }
        if (message.type === "tool.started")
          setProgress(toolLabels[message.data.name] || "正在处理");
        if (message.type === "report.progress")
          setProgress(`正在生成报告 · ${message.data.stage}`);
        if (message.type === "report.completed") setView("report");
      };
      ws.onclose = (event) => {
        if (disposed) return;
        setConnected(false);
        setPending(false);
        if (event.code === 1008) {
          setError("无法连接此场次，请返回并新建面试。");
          return;
        }
        timer = setTimeout(open, 1500);
      };
      ws.onerror = () => ws.close();
    };
    setConnected(false);
    open();
    return () => {
      disposed = true;
      clearTimeout(timer);
      ws?.close();
      socket.current = null;
      setConnected(false);
    };
  }, [selected, refresh]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [session?.messages.at(-1)?.text, view]);

  const send = (action: Action) => {
    if (socket.current?.readyState !== WebSocket.OPEN || pending) return;
    const text = action === "answer" ? draft : "";
    const previous = pendingCommand.current;
    const command =
      previous?.action === action && previous.text === text
        ? previous
        : { action, text, request_id: crypto.randomUUID() };
    pendingCommand.current = command;
    setPending(true);
    setError("");
    socket.current.send(JSON.stringify(command));
  };
  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setCreating(true);
    setError("");
    try {
      const s = await api<Session>("/sessions", {
        candidate,
        role,
        background,
      });
      await refresh();
      setSelected(s.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCreating(false);
    }
  };
  const busy = !!session?.active_turn;
  const available = connected && !pending && !!health?.configured;

  return (
    <div className="app">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setSelected(null);
          }}
        >
          <span className="brand-mark">i.</span>
          <div>
            面试练习室<small>INTERVIEW STUDIO</small>
          </div>
        </a>
        <button className="new-session" onClick={() => setSelected(null)}>
          <span>＋</span> 新建面试
        </button>
        <div className="sidebar-label">
          练习记录 <span>{sessions.length}</span>
        </div>
        <nav aria-label="面试历史">
          {sessions.length === 0 ? (
            <p className="no-history">
              你的第一场练习，
              <br />
              从这里开始。
            </p>
          ) : (
            sessions.map((s) => (
              <button
                className={`history-item ${selected === s.id ? "selected" : ""}`}
                key={s.id}
                onClick={() => setSelected(s.id)}
              >
                <strong>{s.role}</strong>
                <span>
                  {s.candidate} · {date(s.created_at)}
                  <i className={s.status === "completed" ? "done" : ""}>
                    {labels[s.status]}
                  </i>
                </span>
              </button>
            ))
          )}
        </nav>
        <div className="sidebar-foot">
          <span className="small-dot" /> 本地练习空间<p>认真练习，从容表达。</p>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>
            练习 / <b>{selected ? "模拟面试" : "新的开始"}</b>
          </span>
          <span className="tag">
            {selected
              ? connected
                ? "● 已连接"
                : "○ 正在连接"
              : "技术岗位 · 中文面试"}
          </span>
        </header>
        {error && (
          <div role="alert" className="alert">
            {error}
            <button aria-label="关闭错误提示" onClick={() => setError("")}>
              ×
            </button>
          </div>
        )}
        {health && !health.configured && (
          <div className="alert">
            尚未配置模型密钥。请在后端环境设置 ANTHROPIC_API_KEY 后重启服务。
          </div>
        )}
        {!selected ? (
          <div className="setup">
            <div className="intro">
              <span className="eyebrow">
                A LITTLE PRACTICE. A LOT MORE CONFIDENCE.
              </span>
              <h1>
                让下一次面试，
                <br />
                <em>更有准备。</em>
              </h1>
              <p>
                从真实的项目经历出发，一次一个问题。
                <br />
                在追问中梳理思路，在复盘中找到下一步。
              </p>
              <div className="intro-features">
                <div>
                  <span>01</span>
                  <strong>贴近你的背景</strong>
                  <p>围绕项目与目标岗位展开</p>
                </div>
                <div>
                  <span>02</span>
                  <strong>追问式对话</strong>
                  <p>深入原理，也关注工程实践</p>
                </div>
                <div>
                  <span>03</span>
                  <strong>有依据的反馈</strong>
                  <p>四个维度，逐题回顾与建议</p>
                </div>
              </div>
              <div className="quote">
                “准备不是记住所有答案，
                <br />
                而是学会把自己的思考讲清楚。”
              </div>
            </div>
            <form className="setup-card" onSubmit={create}>
              <div className="card-heading">
                <span className="eyebrow">SET UP YOUR SESSION</span>
                <h2>准备好，开始一场练习</h2>
                <p>告诉面试官一点关于你的信息。</p>
              </div>
              <label>
                怎么称呼你
                <input
                  required
                  maxLength={80}
                  value={candidate}
                  onChange={(e) => setCandidate(e.target.value)}
                  placeholder="输入你的名字或昵称"
                  autoComplete="given-name"
                />
              </label>
              <label>
                目标岗位
                <select value={role} onChange={(e) => setRole(e.target.value)}>
                  <option>AI / ML 工程师</option>
                  <option>数据科学家</option>
                  <option>数据分析师</option>
                  <option>后端开发工程师</option>
                  <option>前端开发工程师</option>
                </select>
              </label>
              <label>
                经历与练习重点 <span className="optional">选填</span>
                <textarea
                  maxLength={8000}
                  value={background}
                  onChange={(e) => setBackground(e.target.value)}
                  placeholder="例如：两年后端经验，做过 RAG 知识库项目，希望重点练习检索优化与系统设计。"
                  rows={5}
                />
              </label>
              <div className="mode">
                <span className="mode-icon">Aa</span>
                <div>
                  <strong>文字面试</strong>
                  <small>按自己的节奏组织回答，支持多行输入</small>
                </div>
                <span className="mode-check">✓</span>
              </div>
              <button
                className="primary start-button"
                disabled={creating || !health?.configured}
              >
                {creating ? "正在创建…" : "进入面试室"} <span>↗</span>
              </button>
              <p className="form-note">结束后生成报告 · 问答自动保存到本机</p>
            </form>
          </div>
        ) : !session ? (
          <div className="loading">正在恢复面试记录…</div>
        ) : (
          <div className="workspace">
            <div className="session-heading">
              <div>
                <span className="eyebrow">YOUR PRACTICE SESSION</span>
                <h1>{session.role}</h1>
                <p>
                  {session.candidate} <span>·</span> {date(session.created_at)}{" "}
                  <span>·</span> {labels[session.status]}
                </p>
              </div>
              <div className="session-actions">
                {session.report && (
                  <div className="tabs">
                    <button
                      className={view === "chat" ? "active" : ""}
                      onClick={() => setView("chat")}
                    >
                      对话
                    </button>
                    <button
                      className={view === "report" ? "active" : ""}
                      onClick={() => setView("report")}
                    >
                      报告
                    </button>
                  </div>
                )}
                {session.status !== "completed" &&
                  session.messages.some((m) => m.role === "user") && (
                    <button
                      className="secondary"
                      disabled={busy || !available}
                      onClick={() => send("finish")}
                    >
                      结束并生成报告 ↗
                    </button>
                  )}
              </div>
            </div>
            {view === "report" && session.report ? (
              <ReportView report={session.report} />
            ) : (
              <div className="conversation">
                <div className="conversation-top">
                  <span>
                    <span className="small-dot" /> 模拟面试官
                  </span>
                  <small>一次一题，留出思考的空间</small>
                </div>
                <div
                  className="messages"
                  aria-live="polite"
                  aria-relevant="additions text"
                >
                  {session.messages.length === 0 && (
                    <div className="welcome">
                      <div className="welcome-symbol">i.</div>
                      <h2>这里是你的练习时间。</h2>
                      <p>
                        先从自我介绍开始。准备好后，
                        <br />
                        面试官会根据你的经历逐步提问。
                      </p>
                      <button
                        className="primary"
                        disabled={!available}
                        onClick={() => send("start")}
                      >
                        开始面试 →
                      </button>
                    </div>
                  )}
                  {session.messages.map((message) => (
                    <article
                      key={message.id}
                      className={`message ${message.role}`}
                    >
                      <div className="avatar">
                        {message.role === "assistant"
                          ? "i."
                          : session.candidate.slice(0, 1)}
                      </div>
                      <div className="message-body">
                        <div className="message-name">
                          {message.role === "assistant"
                            ? "面试官"
                            : session.candidate}
                          <span>
                            {message.status === "interrupted"
                              ? "未完成 · 不计入评分"
                              : message.status === "streaming"
                                ? "正在回复"
                                : ""}
                          </span>
                        </div>
                        <div className="bubble">
                          {message.text ? (
                            <Markdown>{message.text}</Markdown>
                          ) : message.status === "streaming" ? (
                            <span className="thinking">
                              正在思考<span>•••</span>
                            </span>
                          ) : (
                            <span className="thinking">本轮未生成完整回复</span>
                          )}
                        </div>
                      </div>
                    </article>
                  ))}
                  {session.error && (
                    <div className="turn-error" role="status">
                      {session.error}
                      <button
                        className="secondary"
                        disabled={!available}
                        onClick={() => send("retry")}
                      >
                        重试本轮
                      </button>
                    </div>
                  )}
                  {session.status === "scoring" && (
                    <div className="scoring">
                      <span className="spinner" />
                      <div>
                        <strong>正在认真复盘你的回答</strong>
                        <p>
                          {progress || "解析问答、逐题评分、汇总建议，请稍候…"}
                        </p>
                      </div>
                    </div>
                  )}
                  <div ref={bottom} />
                </div>
                {session.status === "completed" ? (
                  <div className="finished">
                    本场面试已完成。
                    <button
                      className="primary"
                      onClick={() => setView("report")}
                    >
                      查看我的报告 ↗
                    </button>
                  </div>
                ) : (
                  <div className="composer">
                    <textarea
                      aria-label="你的回答"
                      placeholder={
                        session.messages.length
                          ? "写下你的思考，不必急于给出完美答案…"
                          : "开始面试后，在这里回答"
                      }
                      maxLength={12000}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      disabled={!session.messages.length}
                      onKeyDown={(e) => {
                        if (
                          (e.ctrlKey || e.metaKey) &&
                          e.key === "Enter" &&
                          !e.nativeEvent.isComposing &&
                          draft.trim() &&
                          available &&
                          !busy
                        ) {
                          e.preventDefault();
                          send("answer");
                        }
                      }}
                    />
                    <div className="composer-bottom">
                      <span>
                        {busy
                          ? progress || "面试官正在处理本轮…"
                          : "Ctrl / ⌘ + Enter 发送 · Enter 换行"}
                      </span>
                      {busy ? (
                        <button
                          className="secondary"
                          disabled={!available}
                          onClick={() => send("cancel")}
                        >
                          停止本轮
                        </button>
                      ) : (
                        <button
                          className="primary"
                          disabled={
                            !draft.trim() ||
                            !available ||
                            !session.messages.length
                          }
                          onClick={() => send("answer")}
                        >
                          发送回答 ↑
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
        <footer>
          INTERVIEW STUDIO <span>每一场练习，都更接近理想的自己。</span>
        </footer>
      </main>
    </div>
  );
}
