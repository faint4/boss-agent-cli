import type { ApplicationSnapshot } from "./state";

let sessionToken: string | null = null;

function consumeBootstrapToken(): string | null {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const bootstrapToken = fragment.get("bootstrap");
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  return bootstrapToken;
}

async function authenticate(): Promise<string> {
  if (sessionToken) {
    return sessionToken;
  }
  const bootstrapToken = consumeBootstrapToken();
  if (!bootstrapToken) {
    throw new Error("启动凭据已缺失或过期，请重新启动本地工作台。");
  }
  const response = await fetch("/api/v1/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bootstrap_token: bootstrapToken }),
  });
  if (!response.ok) {
    throw new Error("本地会话认证失败，请重新启动本地工作台。");
  }
  const body = (await response.json()) as { session_token: string };
  sessionToken = body.session_token;
  return sessionToken;
}

export async function loadSnapshot(): Promise<ApplicationSnapshot> {
  const token = await authenticate();
  const response = await fetch("/api/v1/state", {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error("无法读取本地工作台状态，请稍后重试。");
  }
  const body = (await response.json()) as { snapshot: ApplicationSnapshot };
  return body.snapshot;
}
