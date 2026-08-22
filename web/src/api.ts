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

export async function loadRunUpdates(runId: string, afterCursor: number): Promise<{
  snapshot: ApplicationSnapshot;
  cursor: number;
}> {
  const token = await authenticate();
  const response = await fetch(`/api/v1/runs/${encodeURIComponent(runId)}/events?after_cursor=${afterCursor}`, {
    headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
  });
  if (!response.ok) throw new Error("无法继续读取任务进度，请重新读取服务端状态。");
  const body = await response.text();
  let cursor = afterCursor;
  let snapshot: ApplicationSnapshot | null = null;
  for (const frame of body.split("\n\n")) {
    const id = frame.split("\n").find((line) => line.startsWith("id: "))?.slice(4);
    const data = frame.split("\n").find((line) => line.startsWith("data: "))?.slice(6);
    if (id !== undefined) cursor = Math.max(cursor, Number(id));
    if (data !== undefined) {
      const decoded = JSON.parse(data) as { snapshot?: ApplicationSnapshot };
      if (decoded.snapshot) snapshot = decoded.snapshot;
    }
  }
  if (!snapshot) throw new Error("任务事件流缺少权威状态，请重新读取。");
  return { snapshot, cursor };
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

export function loadRecruitingOpenings(): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/load-recruiting-openings", {});
}

export function selectRecruitingOpening(reference: string): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/select-recruiting-opening", { reference });
}

export function startInboundApplicants(): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/start-inbound-applicants", {});
}

export function inspectRecruitingProspect(reference: string): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/inspect-recruiting-prospect", { reference });
}

export function cancelRun(runId: string): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/cancel-run", { run_id: runId });
}

export function resumeRun(runId: string): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/resume-run", { run_id: runId });
}

export function discardRun(runId: string): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/discard-run", { run_id: runId });
}

export function inspectJob(reference: string): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/inspect-job", { reference });
}

export function setShortlisted(reference: string, shortlisted: boolean): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/set-shortlisted", { reference, shortlisted });
}

export function prepareJobGreeting(reference: string, message: string): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/prepare-job-greeting", { reference, message });
}

export function prepareRecruitingReply(reference: string, message: string): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/commands/prepare-recruiting-reply", { reference, message });
}

export function confirmWriteIntent(intentId: string): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/write-intents/confirm", { intent_id: intentId });
}

export function cancelWriteIntent(intentId: string): Promise<ApplicationSnapshot> {
  return sendCommand("/api/v1/write-intents/cancel", { intent_id: intentId });
}
