import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import {
  api,
  connectSession,
  pushAudio,
  startListening,
  stopListening,
  transcribe,
  warmupSpeech,
} from "./api";
import type { Action, Health, InputMode, Pressure, Session, SessionSummary } from "./types";
import ReportView from "./components/ReportView";

// 录音上限：opus 大约 24–32 kbps，2 分钟还不到 1MB，远低于后端 5MB 的兜底上限
const MAX_RECORD_MS = 120_000;
// 每 2 秒切一块，块既进本地 parts（最后一次转写用），也实时传给后端（边录边听）。
// 不带这个参数的话 ondataavailable 只在停止时触发一次，后端就永远是「录完才知道说了什么」。
const RECORD_TIMESLICE_MS = 2_000;
// 别假设 audio/webm;codecs=opus——Firefox 给 ogg、Safari 给 mp4。让浏览器挑一个它支持的。
const AUDIO_MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
];

function micErrorMessage(error: unknown): string {
  const name = (error as { name?: string } | null)?.name;
  if (name === "NotAllowedError")
    return "麦克风权限被拒绝。请在地址栏的站点设置里允许麦克风后重试。";
  if (name === "NotFoundError") return "没有找到麦克风设备。";
  if (name === "NotReadableError")
    return "麦克风被其他程序占用了（Windows 上很常见）。关掉占用的程序后重试。";
  return `打不开麦克风：${error instanceof Error ? error.message : String(error)}`;
}

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
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const [draft, setDraft] = useState("");
  const [candidate, setCandidate] = useState("");
  const [role, setRole] = useState("AI / ML 工程师");
  const [background, setBackground] = useState("");
  const [pressure, setPressure] = useState<Pressure>("标准");
  const [creating, setCreating] = useState(false);
  const [pending, setPending] = useState(false);
  const [view, setView] = useState<"chat" | "report">("chat");
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [micNote, setMicNote] = useState("");
  // 草稿里有没有语音转写来的内容。提交时告诉后端，好让评分对术语拼写宽容些
  // （识别错的不该算候选人说错）。草稿清空时由下面的 effect 复位。
  const [voiceOrigin, setVoiceOrigin] = useState(false);
  // 「边录边听」是否已建立。只在提示语里用，所以放 state（ref 变化不触发重渲染）
  const [listening, setListening] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const micStream = useRef<MediaStream | null>(null);
  const micTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const micAbort = useRef<AbortController | null>(null);
  // 边录边听：listenRef 同时记住场次和监听 id，收尾时不用再依赖外部的 selected。
  // 块的 POST 用**自己**的 AbortController——micAbort 是转写用的，别混。
  const listenRef = useRef<{ sessionId: string; listenId: string } | null>(null);
  const chunkChain = useRef<Promise<unknown>>(Promise.resolve());
  const chunkAbort = useRef<AbortController | null>(null);
  const beepCtx = useRef<AudioContext | null>(null);
  const pendingCommand = useRef<{
    action: Action;
    text: string;
    request_id: string;
    input_mode: InputMode;
  } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  /** 释放麦克风。先摘掉回调再停，免得 teardown 反而触发一次转写。
   *  定义在 effect 之前——下面切换场次的 cleanup 要用它。 */
  const releaseMic = useCallback(() => {
    if (micTimer.current) {
      clearTimeout(micTimer.current);
      micTimer.current = null;
    }
    const active = recorder.current;
    recorder.current = null;
    if (active) {
      active.onstop = null;
      active.ondataavailable = null;
      if (active.state !== "inactive") active.stop();
    }
    // 不 stop 每一路轨道的话，系统的麦克风指示灯不会灭
    micStream.current?.getTracks().forEach((track) => track.stop());
    micStream.current = null;
  }, []);

  /** 收掉「边录边听」：停掉在途的块上传，并告诉后端别再判断了。 */
  const endListening = useCallback(() => {
    const current = listenRef.current;
    listenRef.current = null;
    chunkAbort.current?.abort();
    chunkAbort.current = null;
    setListening(false);
    if (current) stopListening(current.sessionId, current.listenId);
  }, []);

  /** 面试官插话时的提示音。
   *  没有 TTS，打断只有屏幕上的气泡——而候选人正在说话，多半没在看屏幕。 */
  const playInterruptCue = useCallback(() => {
    const context = beepCtx.current;
    if (!context) return;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.value = 660;
    gain.gain.value = 0.08;
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.18);
  }, []);

  const refresh = useCallback(
    () =>
      api<SessionSummary[]>("/sessions")
        .then(setSessions)
        .catch((e) => setError(e.message)),
    [],
  );
  useEffect(() => {
    void refresh();
    api<Health>("/health")
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
        if (message.type === "interview.interrupted") {
          // 面试官在候选人说到一半时插话了。**不转写**——后端已经把那半截存成了回答，
          // 再转一遍会把它当成新回答提交到插话下面。
          playInterruptCue();                   // 候选人正交着，多半没看屏幕
          endListening();
          micAbort.current?.abort();            // 在途的转写作废，它属于被打断的那条
          releaseMic();
          setRecording(false);
          setTranscribing(false);
          setMicNote("");
          setVoiceOrigin(false);
          setDraft("");                         // 草稿是给原问题的，别留到新问题下面
          pendingCommand.current = null;
          setPending(false);
        }
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
      // 切换场次或卸载时要停下录音并中止在途转写，
      // 否则转写结果会落进另一场次的输入框、麦克风也不会释放
      micAbort.current?.abort();
      endListening();                           // 别忘了告诉后端别再判断这个场次了
      releaseMic();
      setRecording(false);
      setTranscribing(false);
    };
  }, [selected, refresh, releaseMic, endListening, playInterruptCue]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [session?.messages.at(-1)?.text, view]);
  // 草稿空了就说明这一条已经交出去（或被清掉），来源标记跟着复位
  useEffect(() => {
    if (!draft) setVoiceOrigin(false);
  }, [draft]);

  const send = (action: Action) => {
    if (socket.current?.readyState !== WebSocket.OPEN || pending) return;
    const text = action === "answer" ? draft : "";
    const inputMode: InputMode =
      action === "answer" && voiceOrigin ? "voice" : "text";
    const previous = pendingCommand.current;
    const command =
      previous?.action === action && previous.text === text
        ? previous
        : { action, text, request_id: crypto.randomUUID(), input_mode: inputMode };
    pendingCommand.current = command;
    setPending(true);
    setError("");
    socket.current.send(JSON.stringify(command));
  };
  const stopRecording = () => {
    const active = recorder.current;
    if (active && active.state !== "inactive") active.stop();
  };

  const runTranscribe = async (blob: Blob, mimeType: string) => {
    if (!selected) return;
    if (blob.size < 1024) {
      setMicNote("录得太短了，再说一次？");
      return;
    }
    setTranscribing(true);
    setMicNote("正在识别…");
    const controller = new AbortController();
    micAbort.current = controller;
    try {
      const text = await transcribe(selected, blob, mimeType, controller.signal);
      if (controller.signal.aborted) return;
      if (!text.trim()) {
        setMicNote("没有听清，请重试");
        return;
      }
      setMicNote("");
      setVoiceOrigin(true); // 这条草稿含语音转写内容，提交时带上标记
      setDraft((current) => {
        const merged = current.trim()
          ? `${current.trimEnd()}\n${text.trim()}`
          : text.trim();
        return merged.slice(0, 12000); // maxLength 管不住 setState，超了服务端会拒
      });
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      if (micAbort.current === controller) micAbort.current = null;
      setTranscribing(false);
    }
  };

  const startRecording = async () => {
    if (!selected || recording || transcribing) return;
    setError("");
    setMicNote("");
    // 非安全上下文下 mediaDevices 直接不存在（用局域网 IP 访问时就是这样）
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("这个地址打不开麦克风：浏览器要求安全上下文，请用 127.0.0.1 或 localhost 访问。");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      setError(micErrorMessage(e));
      return;
    }
    micStream.current = stream;
    chunkAbort.current = new AbortController();
    // AudioContext 要在用户手势里创建，否则被自动播放策略拦掉
    if (!beepCtx.current) {
      try {
        beepCtx.current = new AudioContext();
      } catch {
        beepCtx.current = null;                 // 没有提示音也能用
      }
    }
    const mimeType =
      AUDIO_MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type)) || "";
    const active = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const parts: Blob[] = [];
    active.ondataavailable = (event) => {
      if (!event.data.size) return;
      parts.push(event.data);                   // 本地留一份，停止时整体转写
      const current = listenRef.current;
      if (!current) return;
      // **必须串行**：fetch 不保证顺序，中间丢一块会让整个 webm 流后面全废
      const signal = chunkAbort.current?.signal;
      chunkChain.current = chunkChain.current
        .then(() => pushAudio(current.sessionId, current.listenId, event.data, signal))
        .catch(() => {});                       // 尽力而为——本地那份还在，最终转写不受影响
    };
    active.onstop = () => {
      const type = active.mimeType || mimeType || "audio/webm";
      const blob = new Blob(parts, { type });
      endListening();
      releaseMic();
      setRecording(false);
      void runTranscribe(blob, type);
    };
    recorder.current = active;
    active.start(RECORD_TIMESLICE_MS);
    setRecording(true);
    micTimer.current = setTimeout(stopRecording, MAX_RECORD_MS);

    // 开局「边录边听」：先预热模型（否则第一轮重转会在录音期间触发加载，
    // 而加载全程握着识别器的锁——用户此时按停止，自己的转写会被卡住），再开监听。
    // 整条链路失败也不影响录音本身，最多是面试官不插话。
    const sessionId = selected;
    void (async () => {
      try {
        await warmupSpeech();
        const listenId = await startListening(sessionId);
        if (recorder.current !== active) {      // 等预热的时候用户已经停了
          stopListening(sessionId, listenId);
          return;
        }
        listenRef.current = { sessionId, listenId };
        setListening(true);
      } catch {
        listenRef.current = null;               // 监听没起来也不影响录音和转写
      }
    })();
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
        pressure,
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
                面试压力
                <select value={pressure} onChange={(e) => setPressure(e.target.value as Pressure)}>
                  <option value="温和">温和 — 适合练习，卡住时会给方向提示</option>
                  <option value="标准">标准 — 质疑数字和空话，但不逼问（默认）</option>
                  <option value="压力">压力面 — 连珠追问、质疑前提、不让喘</option>
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
                          : recording
                            ? listening
                              ? "正在录音 · 面试官在听…（说完点「停止录音」）"
                              : "正在录音…（最长 2 分钟，说完点「停止录音」）"
                            : transcribing
                              ? "正在识别…"
                              : micNote || "Ctrl / ⌘ + Enter 发送 · Enter 换行"}
                      </span>
                      {/* 包一层：.composer-bottom 是 space-between，直接加第三个子元素会把按钮拉散 */}
                      <div className="composer-actions">
                        {health?.speech.asr && (
                          <button
                            className={recording ? "secondary mic active" : "secondary mic"}
                            // 录音中不能被禁用，否则用户停不下来
                            disabled={
                              !connected ||
                              transcribing ||
                              !session.messages.length ||
                              (!recording && (busy || !available))
                            }
                            onClick={recording ? stopRecording : () => void startRecording()}
                          >
                            {recording ? "停止录音" : transcribing ? "识别中…" : "语音输入"}
                          </button>
                        )}
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
