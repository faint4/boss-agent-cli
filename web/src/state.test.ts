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
};

describe("deriveView", () => {
  it("renders an empty snapshot as the empty state", () => {
    expect(deriveView(emptySnapshot)).toBe("empty");
  });

  it("renders useful task data as ready", () => {
    expect(deriveView({ ...emptySnapshot, selected_reference: "job-42" })).toBe("ready");
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
});
