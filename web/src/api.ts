import type { ApplicationSnapshot, JobSearchGoal } from "./state";

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

function nextRequestId(): string {
  return crypto.randomUUID();
}

async function sendCommand(path: string, payload: Record<string, unknown>): Promise<ApplicationSnapshot> {
  const token = await authenticate();
  const response = await fetch(path, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ request_id: nextRequestId(), ...payload }),
  });
  if (!response.ok) {
	const body = (await response.json().catch(() => null)) as { error?: { message?: string } } | null;
	throw new Error(body?.error?.message ?? "操作未完成，当前工作区未执行后续动作。请重试。");
  }
  const body = (await response.json()) as { snapshot: ApplicationSnapshot };
  return body.snapshot;
}

export function switchWorkspace(workspace: ApplicationSnapshot["active_workspace"]): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/switch-workspace", { workspace });
}

export function connectPlatformSession(): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/connect-platform-session", {});
}

export function logoutPlatformSession(): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/logout-platform-session", {});
}

export function updateJobSearchGoal(goal: JobSearchGoal): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/update-job-search-goal", goal);
}

export function startJobSearch(): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/start-job-search", {});
}

export function cancelRun(runId: string): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/cancel-run", { run_id: runId });
}

export function inspectJob(reference: string): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/inspect-job", { reference });
}

export function setShortlisted(reference: string, shortlisted: boolean): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/set-shortlisted", { reference, shortlisted });
}
