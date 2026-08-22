import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import App, { JobJourney, RecruitingJourney, SnapshotPanel, WorkspaceControls, WriteConfirmationGate } from "./App";
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
  job_seeking: {
    goal: null,
    results: [],
    selected_job: null,
    shortlist: [],
  },
  recruiting: null,
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

  it("renders the complete read-only job journey and persisted shortlist", () => {
    const job = {
      reference: "job-1", title: "Python 后端工程师", company: "示例科技", location: "上海",
      salary: "20-40K", experience: "3-5年", education: "本科",
    };
    const markup = renderToStaticMarkup(
      <JobJourney
        snapshot={{
          ...baseSnapshot,
          platform_session: "connected",
          job_seeking: {
            goal: { objective: "寻找后端岗位", keyword: "Python", city: "上海", salary: "", experience: "", education: "" },
            results: [job],
            selected_job: {
              source: { job, description: "负责 Python 服务", company_stage: "", company_size: "", recruiter: "招聘者" },
              match_reasons: ["职位原文包含关键词“Python”"],
            },
            shortlist: [job],
          },
        }}
        busy={false}
        onSaveGoal={() => undefined}
        onSearch={() => undefined}
        onCancel={() => undefined}
        onInspect={() => undefined}
        onShortlist={() => undefined}
		onPrepareGreeting={() => undefined}
      />,
    );

    expect(markup).toContain("定义这次搜索");
    expect(markup).toContain("开始只读搜索");
    expect(markup).toContain("平台来源原文");
    expect(markup).toContain("本地匹配理由");
    expect(markup).toContain("1 个已收藏职位");
	expect(markup).toContain("准备发送招呼");
  });

	it("renders a complete pending write confirmation with explicit controls", () => {
		const markup = renderToStaticMarkup(
			<WriteConfirmationGate
				intent={{
					intent_id: "intent-1",
					workspace: "job-seeking",
					target_reference: "job-1",
					target_label: "Python 后端工程师 · 示例科技",
					context_label: "",
					destination_label: "BOSS 求职沟通会话",
					action: "发送 BOSS 招呼",
					payload_preview: "您好，我对这个岗位很感兴趣。",
					warnings: ["确认后将立即发送，且不会自动重试。"],
					expires_at: "2026-08-22T10:05:00+00:00",
					state: "pending",
					outcome_message: null,
				}}
				busy={false}
				onConfirm={() => undefined}
				onCancel={() => undefined}
			/>,
		);

		expect(markup).toContain("发送前确认");
		expect(markup).toContain("Python 后端工程师 · 示例科技");
		expect(markup).toContain("您好，我对这个岗位很感兴趣。");
		expect(markup).toContain("确认并发送一次");
		expect(markup).toContain("取消，不发送");
	});

	it("shows uncertain outcome with official verification and no retry control", () => {
		const markup = renderToStaticMarkup(
			<WriteConfirmationGate
				intent={{
					intent_id: "intent-1", workspace: "job-seeking", target_reference: "job-1",
					target_label: "目标职位", context_label: "", destination_label: "BOSS 求职沟通会话",
					action: "发送 BOSS 招呼", payload_preview: "您好",
					warnings: [], expires_at: "2026-08-22T10:05:00+00:00", state: "uncertain",
					outcome_message: "发送结果不确定。请到 BOSS 官方页面核对；系统不会自动重试。",
				}}
				busy={false}
				onConfirm={() => undefined}
				onCancel={() => undefined}
			/>,
		);

		expect(markup).toContain("发送结果不确定");
		expect(markup).toContain("BOSS 官方页面核对");
		expect(markup).not.toContain("确认并发送一次");
		expect(markup).not.toContain("重试发送");
	});

	it("renders applicants without loading private context implicitly", () => {
		const markup = renderToStaticMarkup(
			<RecruitingJourney snapshot={{ ...baseSnapshot, active_workspace: "recruiting", platform_session: "connected", job_seeking: null, recruiting: {
				openings: [{ reference: "opening-1", title: "Python 后端工程师", status: "招聘中" }],
				selected_opening: { reference: "opening-1", title: "Python 后端工程师", status: "招聘中" },
				applicants: [{ reference: "prospect-1", display_name: "招聘对象甲", headline: "5 年 Python 经验" }],
				selected_prospect: null,
			} }} busy={false} onLoadOpenings={() => undefined} onSelectOpening={() => undefined} onLoadApplicants={() => undefined} onCancel={() => undefined} onInspectProspect={() => undefined} onPrepareReply={() => undefined} />,
		);
		expect(markup).toContain("Python 后端工程师");
		expect(markup).toContain("招聘对象甲");
		expect(markup).toContain("明确查看简历与沟通");
		expect(markup).not.toContain("简历详情（仅内存）");
	});

	it("labels explicitly inspected resume and chat context as memory-only", () => {
		const prospect = { reference: "prospect-1", display_name: "招聘对象甲", headline: "5 年 Python 经验" };
		const markup = renderToStaticMarkup(
			<RecruitingJourney snapshot={{ ...baseSnapshot, active_workspace: "recruiting", platform_session: "connected", job_seeking: null, sensitive_content_present: true, recruiting: {
				openings: [], selected_opening: { reference: "opening-1", title: "Python 后端工程师", status: "招聘中" }, applicants: [prospect],
				selected_prospect: { prospect, resume_text: "负责 Python 服务", chat_messages: ["应聘者：您好"], contact_details: ["手机号已保护"] },
			} }} busy={false} onLoadOpenings={() => undefined} onSelectOpening={() => undefined} onLoadApplicants={() => undefined} onCancel={() => undefined} onInspectProspect={() => undefined} onPrepareReply={() => undefined} />,
		);
		expect(markup).toContain("简历详情（仅内存）");
		expect(markup).toContain("负责 Python 服务");
		expect(markup).toContain("应聘者：您好");
		expect(markup).toContain("手机号已保护");
		expect(markup).toContain("切换工作区、取消任务或退出时会清除");
		expect(markup).toContain("回复内容");
		expect(markup).toContain("这一步只在本机准备确认，不会向 BOSS 发送");
		expect(markup).toContain("准备确认回复");
	});

	it("shows the exact recruiting context and destination before confirmation", () => {
		const markup = renderToStaticMarkup(
			<WriteConfirmationGate
				intent={{
					intent_id: "intent-reply", workspace: "recruiting", target_reference: "prospect-1",
					target_label: "招聘对象甲", context_label: "Python 后端工程师",
					destination_label: "BOSS 招聘沟通会话", action: "回复 BOSS 招聘沟通",
					payload_preview: "您好，方便沟通一下项目经历吗？", warnings: [],
					expires_at: "2026-08-22T10:05:00+00:00", state: "pending", outcome_message: null,
				}}
				busy={false}
				onConfirm={() => undefined}
				onCancel={() => undefined}
			/>,
		);

		expect(markup).toContain("Python 后端工程师");
		expect(markup).toContain("招聘对象甲");
		expect(markup).toContain("BOSS 招聘沟通会话");
		expect(markup).toContain("您好，方便沟通一下项目经历吗？");
	});
});
