import { describe, expect, it } from "vitest";
import { deriveView, type ApplicationSnapshot } from "./state";

const emptySnapshot: ApplicationSnapshot = {
  schema_version: "1",
  active_workspace: "job-seeking",
  platform_session: "disconnected",
  active_run: null,
  selected_reference: null,
  local_decision: null,
  sensitive_content_present: false,
  pending_write_intent: null,
  last_transition: null,
  error: null,
  job_seeking: null,
  recruiting: null,
};

describe("deriveView", () => {
  it("renders an empty snapshot as the empty state", () => {
    expect(deriveView(emptySnapshot)).toBe("empty");
  });

  it("renders useful task data as ready", () => {
    expect(deriveView({ ...emptySnapshot, selected_reference: "job-42" })).toBe("ready");
  });

  it("renders loaded recruiting openings as ready before one is selected", () => {
    expect(deriveView({
      ...emptySnapshot,
      active_workspace: "recruiting",
      recruiting: {
        openings: [{ reference: "opening-1", title: "后端工程师", status: "招聘中" }],
        selected_opening: null,
        applicants: [],
        selected_prospect: null,
      },
    })).toBe("ready");
  });

  it("renders a server-owned recoverable state as recovery", () => {
    expect(deriveView({ ...emptySnapshot, platform_session: "recovery" })).toBe("recovery");
    expect(
      deriveView({
        ...emptySnapshot,
        error: {
          code: "RATE_LIMITED",
          message: "平台暂时限制了请求",
          recoverable: true,
          recovery_action: "稍后手动恢复",
          correlation_id: "request-1",
        },
      }),
    ).toBe("recovery");
  });

  it("renders a non-recoverable server error as error", () => {
    expect(
      deriveView({
        ...emptySnapshot,
        error: {
          code: "STORAGE_UNAVAILABLE",
          message: "无法读取本地状态",
          recoverable: false,
          recovery_action: null,
          correlation_id: "request-2",
        },
      }),
    ).toBe("error");
  });

  it("keeps a terminal write outcome visible instead of hiding it behind recovery", () => {
	const intent = {
		intent_id: "intent-1", workspace: "job-seeking" as const, target_reference: "job-1",
		target_label: "目标职位", context_label: "", destination_label: "BOSS 求职沟通会话",
		action: "发送 BOSS 招呼", payload_preview: "您好", warnings: [],
		expires_at: "2026-08-22T10:05:00+00:00", state: "uncertain" as const,
		outcome_message: "请到 BOSS 官方页面核对。",
	};
	expect(deriveView({
		...emptySnapshot,
		pending_write_intent: intent,
		error: {
			code: "UNCERTAIN_REMOTE_OUTCOME", message: "结果不确定", recoverable: true,
			recovery_action: "官方核对", correlation_id: "request-write",
		},
	})).toBe("ready");
  });
});
