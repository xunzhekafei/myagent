export type Message = {
  id: string;
  turn_id: string;
  role: "user" | "assistant";
  text: string;
  status: "streaming" | "completed" | "interrupted";
  input_mode: "text" | "voice";
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
export type Session = SessionSummary & {
  background: string;
  messages: Message[];
  report: Report | null;
  active_turn: string | null;
  error: string | null;
  seq: number;
  requests: string[];
};
export type Action = "start" | "answer" | "finish" | "cancel" | "retry";
