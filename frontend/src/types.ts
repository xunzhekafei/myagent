export type InputMode = "text" | "voice";

export type Message = {
  id: string;
  turn_id: string;
  role: "user" | "assistant";
  text: string;
  status: "streaming" | "completed" | "interrupted";
  input_mode: InputMode;
  /** 被面试官中途打断的回答：天生不完整，评分时不因「没答完」扣分 */
  interrupted?: boolean;
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
  } & Record<string, unknown>)[];
};
export type SessionSummary = {
  id: string;
  candidate: string;
  role: string;
  created_at: string;
  status: string;
};
export type Pressure = "温和" | "标准" | "压力";

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
