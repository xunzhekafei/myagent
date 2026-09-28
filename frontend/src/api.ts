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

/** 预热语音模型。按麦克风时先调一次——否则第一轮滚动重转会在录音期间触发模型加载，
 *  而加载全程握着识别器的锁，候选人此时按停止录音，自己的转写会被卡住。 */
export function warmupSpeech(): Promise<void> {
  return fetch("/api/speech/warmup", { method: "POST" })
    .then(() => undefined)
    .catch(() => undefined); // 预热失败不阻塞录音，最多是打断功能不生效
}

/** 开始一段「边录边听」。返回 listen_id，之后每块音频推给 pushAudio。 */
export async function startListening(sessionId: string, signal?: AbortSignal): Promise<string> {
  const response = await fetch(`/api/sessions/${sessionId}/listen`, { method: "POST", signal });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.detail === "string" ? error.detail : "无法开始监听",
    );
  }
  return (await response.json()).listen_id as string;
}

/** 追加一块录音。块是 webm 流的片段、不是独立文件，后端只做追加。 */
export async function pushAudio(
  sessionId: string,
  listenId: string,
  chunk: Blob,
  signal?: AbortSignal,
): Promise<void> {
  await fetch(`/api/sessions/${sessionId}/listen/${listenId}/audio`, {
    method: "POST",
    headers: { "Content-Type": chunk.type || "application/octet-stream" },
    body: chunk,
    signal,
  });
}

/** 结束监听。尽力而为——失败也不影响本地那份录音的转写。 */
export function stopListening(sessionId: string, listenId: string): void {
  void fetch(`/api/sessions/${sessionId}/listen/${listenId}`, { method: "DELETE" }).catch(
    () => {},
  );
}

export function connectSession(id: string) {
  return new WebSocket(
    `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/sessions/${id}/ws`,
  );
}
