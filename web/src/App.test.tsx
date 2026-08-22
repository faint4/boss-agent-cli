import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import App, { SnapshotPanel, WorkspaceControls } from "./App";
import type { ApplicationSnapshot, SnapshotView } from "./state";

const baseSnapshot: ApplicationSnapshot = {
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

function renderPanel(screen: SnapshotView, snapshot: ApplicationSnapshot): string {
  return renderToStaticMarkup(
    <SnapshotPanel screen={screen} snapshot={snapshot} clientError="网络错误" onRefresh={() => undefined} />,
  );
}

describe("local Web shell states", () => {
  it("renders loading before a server snapshot arrives", () => {
    expect(renderToStaticMarkup(<App />)).toContain("正在安全启动本地工作台");
  });

  it("renders the empty server snapshot and Browser Bridge boundary", () => {
    const markup = renderPanel("empty", baseSnapshot);
    expect(markup).toContain("安全外壳已就绪");
    expect(markup).toContain("Browser Bridge 未连接");
  });

  it("renders a ready server snapshot", () => {
    const markup = renderPanel("ready", { ...baseSnapshot, selected_reference: "job-42" });
    expect(markup).toContain("任务状态已同步");
    expect(markup).toContain("job-42");
  });

  it("renders a recovery server snapshot without automatic continuation", () => {
    const markup = renderPanel("recovery", { ...baseSnapshot, platform_session: "recovery" });
    expect(markup).toContain("任务已安全暂停");
    expect(markup).toContain("系统不会自动继续写入");
  });

  it("renders a server error with an explicit retry", () => {
    const markup = renderPanel("error", {
      ...baseSnapshot,
      error: {
        code: "STORAGE_UNAVAILABLE",
        message: "无法读取本地状态",
        recoverable: false,
        recovery_action: null,
        correlation_id: "request-2",
      },
    });
    expect(markup).toContain("无法读取本地状态");
    expect(markup).toContain("重试");
  });

  it("keeps the active workspace visible and offers only explicit workspace switching", () => {
    const markup = renderToStaticMarkup(
      <WorkspaceControls
        snapshot={baseSnapshot}
        busy={false}
        commandError={null}
        onSwitch={() => undefined}
        onConnect={() => undefined}
        onLogout={() => undefined}
      />,
    );

    expect(markup).toContain("工作区与平台会话");
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain("求职工作区（当前）");
    expect(markup).toContain("切换到招聘工作区");
    expect(markup).toContain("连接 BOSS");
  });

  it("shows stopping and recovery as visible session controls", () => {
    const stopping = renderToStaticMarkup(
      <WorkspaceControls
        snapshot={{ ...baseSnapshot, platform_session: "stopping" }}
        busy={false}
        commandError={null}
        onSwitch={() => undefined}
        onConnect={() => undefined}
        onLogout={() => undefined}
      />,
    );
    const recovery = renderToStaticMarkup(
      <WorkspaceControls
        snapshot={{ ...baseSnapshot, platform_session: "recovery" }}
        busy={false}
        commandError={null}
        onSwitch={() => undefined}
        onConnect={() => undefined}
        onLogout={() => undefined}
      />,
    );

    expect(stopping).toContain("正在安全退出");
    expect(recovery).toContain("重新连接 BOSS");
  });
});
