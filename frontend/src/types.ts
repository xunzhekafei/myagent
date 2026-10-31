export type InputMode = "text" | "voice";

export type Message = {
  id: string;
  turn_id: string;
  role: "user" | "assistant";
  text: string;
  status: "streaming" | "completed" | "interrupted";
  input_mode: InputMode;
};
export type Report = {
  overall: number;
  summary: string;
  dimension_scores: Record<string, number>;
  strengths: string[];
  weaknesses: string[];
  recommendations: string[];
  per_question?: ({
    question: string;
    evidence: string;
    suggestion: string;
    /** 证据是否通过了「必须是候选人逐字原话」的机器校验 */
    evidence_verified?: boolean;
  } & Record<string, unknown>)[];
};
export type SessionSummary = {
  id: string;
  candidate: string;
  role: string;
  created_at: string;
  status: string;
  /** 老场次没有这个字段，按文字处理 */
  mode?: SessionMode;
};
export type Pressure = "温和" | "标准" | "压力";
export type SessionMode = "text" | "voice";

export type Session = SessionSummary & {
  background: string;
  pressure?: Pressure;
  messages: Message[];
  report: Report | null;
  active_turn: string | null;
  error: string | null;
  seq: number;
  requests: string[];
};
export type Action = "start" | "answer" | "finish" | "cancel" | "retry";

/** 报告生成过程中逐题推来的结果；四个维度名也是这个对象的键。 */
export type ScoredQuestion = {
  question: string;
  evidence: string;
  suggestion: string;
  [key: string]: string | number;
};

export type DimensionTrend = {
  average: number;
  first: number;
  latest: number;
  delta: number;
  best: number;
  worst: number;
  samples: number;
};

export type WeakPoint = {
  text: string;
  theme: string | null;
  role: string;
  first_seen: string;
  last_seen: string;
  times_seen: number;
  improved: boolean;
  archived: boolean;
  interval_days: number;
  review_streak: number;
  next_review: string | null;
  due: boolean;
};

export type Profile = {
  sessions: number;
  range?: { from: string; to: string };
  overall?: {
    average: number;
    first: number;
    latest: number;
    delta: number;
    best: number;
    worst: number;
  };
  timeline: {
    id: string;
    created_at: string;
    role: string;
    pressure: Pressure;
    overall: number;
    dimensions: Record<string, number>;
    scoring_version?: number;
  }[];
  /** 出现多个版本说明评分口径变过，跨版本的分数不完全可比 */
  scoring_versions?: number[];
  dimensions: Record<string, DimensionTrend>;
  roles: {
    role: string;
    sessions: number;
    average: number;
    latest: number;
    dimensions: Record<string, number>;
  }[];
  weakest: ({ name: string } & DimensionTrend)[];
  recurring: { text: string; count: number }[];
  recent_weaknesses: { text: string; role: string; created_at: string }[];
  /** 跨场次聚合出的弱点，带生命周期与复习日期 */
  weak_points: WeakPoint[];
  /** 已到复习日的弱点 */
  due_reviews: WeakPoint[];
};

export type SpeechCapabilities = {
  asr: boolean; // 这台机器有没有语音识别能力（依赖装没装）
  ready: boolean; // 模型是否已加载（有能力 ≠ 已就绪）
  tts: boolean;
  input_modes: string[];
};

export type Health = {
  configured: boolean;
  speech: SpeechCapabilities;
};
