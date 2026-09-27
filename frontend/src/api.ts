export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : "请求失败，请检查输入或服务状态",
    );
  }
  return response.json();
}

/** 上传一段录音，拿回转写文字。**不写入会话**——文字填进输入框，由用户确认后再走 answer 提交。
 *
 * 不能复用上面的 `api()`：它固定发 JSON 并把 body 序列化。
 */
export async function transcribe(
  sessionId: string,
  audio: Blob,
  mimeType: string,
  signal?: AbortSignal,
): Promise<string> {
  const response = await fetch(`/api/sessions/${sessionId}/transcribe`, {
    method: "POST",
    headers: { "Content-Type": mimeType || "application/octet-stream" },
    body: audio,
    signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.detail === "string" ? error.detail : "语音识别失败，请重试",
    );
  }
  return (await response.json()).text as string;
}

export function connectSession(id: string) {
  return new WebSocket(
    `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/sessions/${id}/ws`,
  );
}
