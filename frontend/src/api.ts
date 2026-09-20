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

export function connectSession(id: string) {
  return new WebSocket(
    `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/sessions/${id}/ws`,
  );
}
