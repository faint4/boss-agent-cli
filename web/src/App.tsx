import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  cancelRun,
  cancelWriteIntent,
  clearWorkspace,
  connectPlatformSession,
  confirmWriteIntent,
  discardAISuggestion,
  discardRun,
  exportWorkspace,
  inspectJob,
  inspectRecruitingProspect,
  loadSnapshot,
  loadRunUpdates,
  loadRecruitingOpenings,
  logoutPlatformSession,
  prepareJobGreeting,
  prepareRecruitingReply,
  requestAIAssistance,
  resumeRun,
  setShortlisted,
  selectRecruitingOpening,
  startInboundApplicants,
  startJobSearch,
  switchWorkspace,
  updateJobSearchGoal,
} from "./api";
import {
  deriveView,
  type AIAssistanceKind,
  type ApplicationSnapshot,
  type JobSearchGoal,
  type SnapshotView,
  type WriteIntent,
} from "./state";

type ScreenState = "loading" | SnapshotView;
const stages = ["目标", "结果", "对象", "来源事实", "本地收藏", "结果回执"];

function StatusChrome({ snapshot }: { snapshot: ApplicationSnapshot }) {
  const workspace = snapshot.active_workspace === "job-seeking" ? "求职工作区" : "招聘工作区";
  const session = {
    disconnected: "未连接",
    connecting: "连接中",
    connected: "已连接",
    stopping: "正在停止",
    recovery: "需要恢复",
  }[snapshot.platform_session];
  return <header className="status-chrome"><div><p className="eyebrow">本机专属 · 显式确认写入</p><h1>BOSS 本地工作台</h1></div><dl className="status-grid"><div><dt>当前空间</dt><dd>{workspace}</dd></div><div><dt>平台会话</dt><dd>{session}</dd></div><div><dt>搜索运行</dt><dd>{snapshot.active_run?.state ?? "暂无"}</dd></div></dl></header>;
}

function Pipeline() {
  return <ol className="pipeline" aria-label="任务流水线">{stages.map((stage, index) => <li key={stage}><span>{index + 1}</span>{stage}</li>)}</ol>;
}

const privacyLabels: Record<string, string> = {
  "search-goal-and-filters": "搜索目标与筛选条件",
  "job-shortlist": "本地职位收藏",
  "recoverable-runs": "可恢复任务记录",
  "protected-platform-session": "受保护的平台会话",
  "selected-opening": "已选择的招聘职位",
  "recoverable-run-checkpoints": "非敏感任务检查点",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function WorkspacePrivacyPanel({ snapshot, busy, onExport, onClear }: {
  snapshot: ApplicationSnapshot;
  busy: boolean;
  onExport: (workspace: ApplicationSnapshot["active_workspace"]) => void;
  onClear: (workspace: ApplicationSnapshot["active_workspace"], confirmation: string) => void;
}) {
  const [confirmation, setConfirmation] = useState("");
  const activePhrase = `clear:${snapshot.active_workspace}`;
  return <section className="workspace-panel privacy-panel"><div><p className="eyebrow">本地数据控制</p><h2>数据、导出与清除</h2><p>默认导出不会包含凭据、Cookie、Token、简历、联系方式或聊天内容。</p></div><div className="privacy-grid">{snapshot.workspace_privacy.map((item) => { const active = item.workspace === snapshot.active_workspace; return <article key={item.workspace} className={active ? "privacy-card active" : "privacy-card"}><h3>{item.workspace === "job-seeking" ? "求职工作区" : "招聘工作区"} · 约 {formatBytes(item.approximate_bytes)}</h3><p>本地保留：{item.retained_categories.map((name) => privacyLabels[name] ?? name).join("、")}</p><p>默认导出：{item.default_export_includes.map((name) => privacyLabels[name] ?? name).join("、") || "无"}</p><button className="secondary-button" disabled={busy || !active} onClick={() => onExport(item.workspace)}>{active ? "导出当前工作区 JSON" : "切换后可导出"}</button></article>; })}</div><div className="clear-zone"><p><strong>清除范围：</strong>当前 {snapshot.active_workspace === "job-seeking" ? "求职工作区" : "招聘工作区"} 的本地数据、缓存、任务记录和平台会话；不会影响另一工作区。</p><label>输入 <code>{activePhrase}</code> 确认<input value={confirmation} disabled={busy} onChange={(event) => setConfirmation(event.target.value)} /></label><button className="danger-button" disabled={busy || confirmation !== activePhrase} onClick={() => onClear(snapshot.active_workspace, confirmation)}>清除当前工作区</button></div></section>;
}

export function WorkspaceControls({ snapshot, busy, commandError, onSwitch, onConnect, onLogout }: {
  snapshot: ApplicationSnapshot;
  busy: boolean;
  commandError: string | null;
  onSwitch: (workspace: ApplicationSnapshot["active_workspace"]) => void;
  onConnect: () => void;
  onLogout: () => void;
}) {
  const jobSeekingActive = snapshot.active_workspace === "job-seeking";
  const sessionControl = {
    disconnected: <button disabled={busy} onClick={onConnect}>连接 BOSS</button>,
    connecting: <button disabled>正在打开官方登录窗口…</button>,
    connected: <button className="secondary-button" disabled={busy} onClick={onLogout}>退出并清除凭据</button>,
    stopping: <button disabled>正在安全退出…</button>,
    recovery: <button disabled={busy} onClick={onConnect}>重新连接 BOSS</button>,
  }[snapshot.platform_session];
  return <section className="workspace-controls" aria-labelledby="workspace-controls-title"><div><p className="eyebrow" id="workspace-controls-title">工作区与平台会话</p><div className="workspace-buttons"><button className={jobSeekingActive ? "workspace-active" : "secondary-button"} aria-pressed={jobSeekingActive} disabled={busy || jobSeekingActive} onClick={() => onSwitch("job-seeking")}>{jobSeekingActive ? "求职工作区（当前）" : "切换到求职工作区"}</button><button className={!jobSeekingActive ? "workspace-active" : "secondary-button"} aria-pressed={!jobSeekingActive} disabled={busy || !jobSeekingActive} onClick={() => onSwitch("recruiting")}>{!jobSeekingActive ? "招聘工作区（当前）" : "切换到招聘工作区"}</button></div></div><div className="session-control"><span>当前工作区的独立会话</span>{sessionControl}</div>{commandError ? <p className="command-error" role="alert">{commandError}</p> : null}</section>;
}

export function JobJourney({ snapshot, busy, onSaveGoal, onSearch, onCancel, onInspect, onShortlist, onPrepareGreeting }: {
  snapshot: ApplicationSnapshot;
  busy: boolean;
  onSaveGoal: (goal: JobSearchGoal) => void;
  onSearch: () => void;
  onCancel: (runId: string) => void;
  onInspect: (reference: string) => void;
  onShortlist: (reference: string, shortlisted: boolean) => void;
  onPrepareGreeting: (reference: string, message: string) => void;
}) {
  const state = snapshot.job_seeking;
  if (snapshot.active_workspace !== "job-seeking" || !state) {
    return <section className="task-card"><p className="eyebrow">招聘工作区</p><h2>求职旅程已隔离</h2><p>切换回求职工作区即可继续，目标和收藏不会丢失。</p></section>;
  }
  const running = snapshot.active_run && ["running", "stopping"].includes(snapshot.active_run.state);
  const shortlisted = new Set(state.shortlist.map((item) => item.reference));
  const submitGoal = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onSaveGoal({
      objective: String(form.get("objective") ?? ""), keyword: String(form.get("keyword") ?? ""),
      city: String(form.get("city") ?? ""), salary: String(form.get("salary") ?? ""),
      experience: String(form.get("experience") ?? ""), education: String(form.get("education") ?? ""),
    });
  };
  return <div className="journey-grid">
    <section className="panel"><p className="eyebrow">1 · 求职目标</p><h2>定义这次搜索</h2><form className="goal-form" onSubmit={submitGoal}><label>目标<input name="objective" required defaultValue={state.goal?.objective ?? ""} placeholder="例如：寻找后端工程师岗位" /></label><label>关键词<input name="keyword" required defaultValue={state.goal?.keyword ?? ""} placeholder="Python" /></label><label>城市<input name="city" defaultValue={state.goal?.city ?? ""} placeholder="上海" /></label><label>薪资<input name="salary" defaultValue={state.goal?.salary ?? ""} placeholder="20-40K" /></label><label>经验<input name="experience" defaultValue={state.goal?.experience ?? ""} placeholder="3-5年" /></label><label>学历<input name="education" defaultValue={state.goal?.education ?? ""} placeholder="本科" /></label><div className="form-actions"><button disabled={busy}>保存目标</button><button type="button" disabled={busy || !state.goal || snapshot.platform_session !== "connected" || Boolean(running)} onClick={onSearch}>开始只读搜索</button>{running && snapshot.active_run ? <button type="button" className="danger-button" disabled={busy || snapshot.active_run.state === "stopping"} onClick={() => onCancel(snapshot.active_run!.run_id)}>取消搜索</button> : null}</div></form>{snapshot.active_run ? <div className="run-progress" aria-live="polite"><strong>{snapshot.active_run.state === "recovery_required" ? "搜索已暂停" : `搜索 ${snapshot.active_run.state}`}</strong><progress max="100" value={snapshot.active_run.progress ?? 0} /><span>{snapshot.active_run.progress ?? 0}% · 已找到 {state.results.length} 个职位</span></div> : null}</section>
    <section className="panel"><p className="eyebrow">2 · 搜索结果</p><h2>{state.results.length ? `${state.results.length} 个职位` : snapshot.active_run?.state === "completed" ? "没有匹配结果" : "等待搜索"}</h2><div className="job-list">{state.results.map((job) => <article className="job-card" key={job.reference}><div><h3>{job.title}</h3><p>{job.company} · {job.location || "地点未提供"}</p><small>{[job.salary, job.experience, job.education].filter(Boolean).join(" · ")}</small></div><div className="card-actions"><button className="secondary-button" disabled={busy} onClick={() => onInspect(job.reference)}>查看来源事实</button><button disabled={busy} onClick={() => onShortlist(job.reference, !shortlisted.has(job.reference))}>{shortlisted.has(job.reference) ? "移出收藏" : "加入收藏"}</button></div></article>)}</div></section>
    {state.selected_job ? <section className="panel detail-panel"><p className="eyebrow">3 · 职位详情</p><h2>{state.selected_job.source.job.title}</h2><dl className="facts"><div><dt>公司</dt><dd>{state.selected_job.source.job.company}</dd></div><div><dt>地点</dt><dd>{state.selected_job.source.job.location || "未提供"}</dd></div><div><dt>薪资</dt><dd>{state.selected_job.source.job.salary || "未提供"}</dd></div><div><dt>招聘者</dt><dd>{state.selected_job.source.recruiter || "未提供"}</dd></div></dl><h3>平台来源原文</h3><p className="source-copy">{state.selected_job.source.description || "平台未提供职位描述。"}</p><h3>本地匹配理由</h3>{state.selected_job.match_reasons.length ? <ul>{state.selected_job.match_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <p>没有足够的来源事实可生成匹配理由。</p>}<form className="greeting-form" key={state.selected_job.source.job.reference} onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); onPrepareGreeting(state.selected_job!.source.job.reference, String(form.get("message") ?? "")); }}><label htmlFor="greeting-message">招呼内容</label><textarea id="greeting-message" name="message" required maxLength={1000} defaultValue="您好，我对这个岗位很感兴趣，希望进一步沟通。" /><p>这一步只在本机准备确认，不会向 BOSS 发送。</p><button disabled={busy || ["pending", "executing"].includes(snapshot.pending_write_intent?.state ?? "")}>准备发送招呼</button></form></section> : null}
    <section className="panel shortlist-panel"><p className="eyebrow">4 · 本地收藏</p><h2>{state.shortlist.length ? `${state.shortlist.length} 个已收藏职位` : "收藏为空"}</h2><p>收藏只保存在当前求职工作区，重启后仍会保留。</p>{state.shortlist.map((job) => <article className="shortlist-row" key={job.reference}><span><strong>{job.title}</strong><small>{job.company}</small></span><button className="secondary-button" disabled={busy} onClick={() => onShortlist(job.reference, false)}>移出</button></article>)}</section>
  </div>;
}

export function RecruitingJourney({ snapshot, busy, onLoadOpenings, onSelectOpening, onLoadApplicants, onCancel, onInspectProspect, onPrepareReply }: {
  snapshot: ApplicationSnapshot;
  busy: boolean;
  onLoadOpenings: () => void;
  onSelectOpening: (reference: string) => void;
  onLoadApplicants: () => void;
  onCancel: (runId: string) => void;
  onInspectProspect: (reference: string) => void;
  onPrepareReply: (reference: string, message: string) => void;
}) {
  const state = snapshot.recruiting;
  if (snapshot.active_workspace !== "recruiting" || !state) return null;
  const running = snapshot.active_run && ["running", "stopping"].includes(snapshot.active_run.state);
  return <div className="journey-grid recruiting-journey">
    <section className="panel"><p className="eyebrow">1 · 招聘职位</p><h2>选择待处理职位</h2><div className="form-actions"><button disabled={busy || snapshot.platform_session !== "connected" || Boolean(running)} onClick={onLoadOpenings}>{state.openings.length ? "刷新职位" : "读取招聘职位"}</button>{state.selected_opening ? <button className="secondary-button" disabled={busy || snapshot.platform_session !== "connected" || Boolean(running)} onClick={onLoadApplicants}>读取新招呼与投递</button> : null}{running && snapshot.active_run ? <button className="danger-button" disabled={busy || snapshot.active_run.state === "stopping"} onClick={() => onCancel(snapshot.active_run!.run_id)}>取消读取并清除</button> : null}</div><div className="job-list opening-list">{state.openings.map((opening) => <article className={`job-card ${state.selected_opening?.reference === opening.reference ? "selected-card" : ""}`} key={opening.reference}><div><h3>{opening.title}</h3><p>{opening.status || "状态未提供"}</p></div><div className="card-actions"><button className="secondary-button" disabled={busy || Boolean(running)} onClick={() => onSelectOpening(opening.reference)}>{state.selected_opening?.reference === opening.reference ? "当前职位" : "选择职位"}</button></div></article>)}</div>{snapshot.active_run ? <div className="run-progress" aria-live="polite"><strong>{snapshot.active_run.state === "recovery_required" ? "读取已安全暂停" : `读取 ${snapshot.active_run.state}`}</strong><progress max="100" value={snapshot.active_run.progress ?? 0} /><span>{snapshot.active_run.progress ?? 0}% · 已载入 {state.applicants.length} 位候选人</span></div> : null}</section>
    <section className="panel"><p className="eyebrow">2 · 新招呼与投递</p><h2>{state.selected_opening ? state.selected_opening.title : "先选择职位"}</h2>{state.applicants.length === 0 ? <p>候选列表尚未读取。简历与沟通内容不会随列表自动加载。</p> : <div className="job-list">{state.applicants.map((applicant) => <article className="job-card" key={applicant.reference}><div><h3>{applicant.display_name}</h3><p>{applicant.headline || "候选人未提供摘要"}</p></div><div className="card-actions"><button disabled={busy || snapshot.platform_session !== "connected"} onClick={() => onInspectProspect(applicant.reference)}>明确查看简历与沟通</button></div></article>)}</div>}{snapshot.sensitive_content_present ? <p className="memory-notice">当前候选数据仅保存在内存；切换工作区、取消任务或退出时会清除。</p> : null}</section>
    {state.selected_prospect ? <section className="panel detail-panel sensitive-panel"><p className="eyebrow">3 · 明确查看的候选上下文</p><h2>{state.selected_prospect.prospect.display_name}</h2><p className="memory-notice"><strong>简历详情（仅内存）</strong> · 切换工作区、取消任务或退出时会清除。</p><h3>简历</h3><p className="source-copy">{state.selected_prospect.resume_text || "平台未提供简历文本。"}</p><h3>沟通记录</h3>{state.selected_prospect.chat_messages.length ? <ul className="context-list">{state.selected_prospect.chat_messages.map((message, index) => <li key={`${index}-${message}`}>{message}</li>)}</ul> : <p>平台未提供沟通记录。</p>}<h3>联系方式</h3>{state.selected_prospect.contact_details.length ? <ul className="context-list">{state.selected_prospect.contact_details.map((detail) => <li key={detail}>{detail}</li>)}</ul> : <p>平台未提供联系方式。</p>}<form className="greeting-form" key={state.selected_prospect.prospect.reference} onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); onPrepareReply(state.selected_prospect!.prospect.reference, String(form.get("message") ?? "")); }}><label htmlFor="recruiting-reply-message">回复内容</label><textarea id="recruiting-reply-message" name="message" required maxLength={1000} placeholder="输入要回复给这位候选人的完整消息" /><p>这一步只在本机准备确认，不会向 BOSS 发送。</p><button disabled={busy || ["pending", "executing"].includes(snapshot.pending_write_intent?.state ?? "")}>准备确认回复</button></form></section> : null}
  </div>;
}

export function AIAssistancePanel({ snapshot, busy, onRequest, onDiscard, onUseDraft }: {
  snapshot: ApplicationSnapshot;
  busy: boolean;
  onRequest: (kind: AIAssistanceKind) => void;
  onDiscard: () => void;
  onUseDraft: (kind: AIAssistanceKind, reference: string, message: string) => void;
}) {
  const ai = snapshot.ai_assistance;
  const selectedJob = snapshot.job_seeking?.selected_job?.source.job ?? null;
  const selectedProspect = snapshot.recruiting?.selected_prospect?.prospect ?? null;
  const hasTarget = snapshot.active_workspace === "job-seeking" ? Boolean(selectedJob) : Boolean(selectedProspect);
  const dataCategories = snapshot.active_workspace === "job-seeking"
    ? ["求职目标与筛选条件", "当前职位的来源事实"]
    : ["当前招聘职位", "招聘对象摘要与简历", "当前沟通内容（不包含联系方式）"];

  if (!ai?.configured) {
    return <section className="ai-panel ai-unconfigured" aria-labelledby="ai-title"><p className="eyebrow">可选能力 · AI 建议</p><h2 id="ai-title">未配置 AI 提供方</h2><p>求职和招聘核心流程仍可完整使用；未配置密钥时不会发送任何数据。</p></section>;
  }

  const suggestion = ai.suggestion && (
    ai.suggestion.target_reference === selectedJob?.reference || ai.suggestion.target_reference === selectedProspect?.reference
  ) ? ai.suggestion : null;
  const isDraft = suggestion?.kind === "job-greeting-draft" || suggestion?.kind === "recruiting-reply-draft";

  return <section className="ai-panel" aria-labelledby="ai-title">
    <div className="ai-heading"><div><p className="eyebrow">可选能力 · AI 建议（非来源事实）</p><h2 id="ai-title">需要时再请求，不会自动操作平台</h2></div><dl className="ai-provider"><div><dt>提供方</dt><dd>{ai.provider}</dd></div><div><dt>模型</dt><dd>{ai.model}</dd></div><div><dt>端点</dt><dd>{ai.endpoint}</dd></div></dl></div>
    <div className="ai-disclosure"><h3>发送前披露</h3><p>{ai.disclosure}</p><p>只有点击下方“同意披露并请求”后，才会把这些最少必要数据发送给上述提供方：</p><ul>{dataCategories.map((item) => <li key={item}>{item}</li>)}</ul></div>
    <div className="form-actions">{snapshot.active_workspace === "job-seeking" ? <><button disabled={busy || !hasTarget} onClick={() => onRequest("job-match")}>同意披露并请求匹配解释</button><button className="secondary-button" disabled={busy || !hasTarget} onClick={() => onRequest("job-greeting-draft")}>同意披露并请求招呼草稿</button></> : <button disabled={busy || !hasTarget} onClick={() => onRequest("recruiting-reply-draft")}>同意披露并请求回复草稿</button>}</div>
    {!hasTarget ? <p className="ai-hint">先明确查看一个对象，AI 请求按钮才会启用。</p> : null}
    {suggestion ? <article className="ai-suggestion" aria-label="AI 建议，不是来源事实"><p className="eyebrow">AI 建议 · 不是平台来源事实</p><p className="ai-provenance">由 {suggestion.provider} / {suggestion.model} 生成 · 已发送：{suggestion.data_sent.join("、")}</p>{isDraft ? <form onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); onUseDraft(suggestion.kind, suggestion.target_reference, String(form.get("ai-draft") ?? "")); }}><label htmlFor="ai-draft">可编辑草稿</label><textarea id="ai-draft" name="ai-draft" required maxLength={1000} defaultValue={suggestion.content} /><p>编辑后仍不会自动发送；下一步只会创建独立的发送前确认。</p><div className="form-actions"><button disabled={busy}>使用编辑后的草稿，进入发送前确认</button><button type="button" className="secondary-button" disabled={busy} onClick={onDiscard}>丢弃 AI 草稿</button></div></form> : <><p className="ai-copy">{suggestion.content}</p><button className="secondary-button" disabled={busy} onClick={onDiscard}>丢弃 AI 建议</button></>}</article> : null}
  </section>;
}

export function WriteConfirmationGate({ intent, busy, onConfirm, onCancel }: {
  intent: WriteIntent;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const stateLabel = {
    pending: "等待你的明确确认",
    executing: "正在执行一次发送",
    succeeded: "发送成功",
    rejected: "平台已拒绝",
    expired: "确认已过期",
    cancelled: "确认已取消",
    uncertain: "发送结果不确定",
  }[intent.state];
  return <section className={`confirmation-gate confirmation-${intent.state}`} aria-labelledby="confirmation-title" aria-live="polite">
    <p className="eyebrow">5 · Platform Write</p>
    <h2 id="confirmation-title">发送前确认</h2>
    <p className="intent-state"><strong>{stateLabel}</strong></p>
    <dl className="confirmation-facts">
      {intent.context_label ? <div><dt>招聘职位</dt><dd>{intent.context_label}</dd></div> : null}
      <div><dt>目标</dt><dd>{intent.target_label}</dd></div>
      {intent.destination_label ? <div><dt>发送去向</dt><dd>{intent.destination_label}</dd></div> : null}
      <div><dt>动作</dt><dd>{intent.action}</dd></div>
      <div><dt>有效期至</dt><dd>{new Date(intent.expires_at).toLocaleString("zh-CN")}</dd></div>
    </dl>
    <h3>将发送的内容</h3>
    <p className="payload-preview">{intent.payload_preview}</p>
    {intent.warnings.length ? <ul className="intent-warnings">{intent.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}
    {intent.outcome_message ? <p className="outcome-message" role={intent.state === "uncertain" ? "alert" : undefined}>{intent.outcome_message}</p> : null}
    {intent.state === "pending" ? <div className="confirmation-actions"><button disabled={busy} onClick={onConfirm}>确认并发送一次</button><button className="secondary-button" disabled={busy} onClick={onCancel}>取消，不发送</button></div> : null}
    {intent.state === "executing" ? <p>确认已经消耗。请等待本次结果，不要刷新后重复操作。</p> : null}
    {intent.state === "uncertain" ? <p><a href="https://www.zhipin.com/" target="_blank" rel="noreferrer">打开 BOSS 官网核对结果</a>。这里不会提供一键重试；核对后请重新准备一次新的确认。</p> : null}
  </section>;
}

export function RunRecoveryPanel({ snapshot, busy, onResume, onDiscard }: {
  snapshot: ApplicationSnapshot;
  busy: boolean;
  onResume: (runId: string) => void;
  onDiscard: (runId: string) => void;
}) {
  const run = snapshot.active_run;
  if (!run || !["recovery_required", "stopped"].includes(run.state)) return null;
  const actions = new Set(run.permitted_next_actions ?? []);
  const error = run.error ?? snapshot.error;
  return <section className="task-card recovery-card" role="alert" aria-live="polite">
    <p className="eyebrow">可恢复 Run · {run.run_id}</p>
    <h2>{run.state === "stopped" ? "任务已按要求停止" : "任务已安全暂停"}</h2>
    <p>{error?.message ?? "安全检查停止了后续远程操作。"}</p>
    <p>{error?.recovery_action ?? "检查已保存的进度后，明确选择恢复或丢弃。"}</p>
    <p>已保留进度 {run.progress ?? 0}%；恢复只会重新读取远程状态，旧的发送确认不会恢复。</p>
    <div className="form-actions">
      {actions.has("reconnect") ? <span>请先使用上方“重新连接 BOSS”。</span> : null}
      {actions.has("wait") ? <span>请等待平台限流窗口结束后再恢复。</span> : null}
      {actions.has("open-official-boss") ? <a href="https://www.zhipin.com/" target="_blank" rel="noreferrer">打开 BOSS 官网处理验证</a> : null}
      {actions.has("resume") ? <button disabled={busy || snapshot.platform_session !== "connected"} onClick={() => onResume(run.run_id)}>恢复只读任务</button> : null}
      {actions.has("discard") ? <button className="secondary-button" disabled={busy} onClick={() => onDiscard(run.run_id)}>丢弃此 Run</button> : null}
    </div>
  </section>;
}

export function SnapshotPanel({ screen, snapshot, clientError, onRefresh }: { screen: SnapshotView; snapshot: ApplicationSnapshot; clientError: string | null; onRefresh: () => void; }) {
  if (screen === "recovery") return <section className="task-card recovery-card" role="alert"><p className="eyebrow">任务已安全暂停</p><h2>{snapshot.error?.message ?? "平台会话需要恢复"}</h2><p>{snapshot.error?.recovery_action ?? "处理平台会话后，请手动恢复任务；系统不会自动继续写入。"}</p><button onClick={onRefresh}>重新读取状态</button></section>;
  if (screen === "error") return <section className="task-card error-card" role="alert"><p className="eyebrow">状态读取失败</p><h2>{snapshot.error?.message ?? clientError}</h2><p>未执行任何平台写入。你可以重新读取服务端状态。</p><button onClick={onRefresh}>重试</button></section>;
  if (screen === "empty") return <section className="task-card empty-card"><p className="eyebrow">安全外壳已就绪</p><h2>从一个明确目标开始</h2><p>填写并保存目标后，即可连接 BOSS 进行一次有界的只读搜索。</p><p className="boundary">Browser Bridge 未连接，也不属于这条启动路径。</p></section>;
  return <section className="status-note"><strong>任务状态已同步</strong><span>{snapshot.selected_reference ?? snapshot.active_run?.run_id ?? "本地工作区已准备好"}</span></section>;
}

export default function App() {
  const [screen, setScreen] = useState<ScreenState>("loading");
  const [snapshot, setSnapshot] = useState<ApplicationSnapshot | null>(null);
  const [clientError, setClientError] = useState<string | null>(null);
  const [commandPending, setCommandPending] = useState(false);
  const [commandError, setCommandError] = useState<string | null>(null);
	const eventCursor = useRef({ runId: "", cursor: 0 });
  const refresh = useCallback(async (showLoading = false) => { if (showLoading) setScreen("loading"); setClientError(null); try { const next = await loadSnapshot(); setSnapshot(next); setScreen(deriveView(next)); } catch (error) { setClientError(error instanceof Error ? error.message : "本地工作台发生未知错误。"); setScreen("error"); } }, []);
  useEffect(() => { void refresh(true); }, [refresh]);
  useEffect(() => { if (!snapshot || !['connecting', 'stopping'].includes(snapshot.platform_session)) return undefined; const timer = window.setInterval(() => void refresh(), 500); return () => window.clearInterval(timer); }, [refresh, snapshot]);
  useEffect(() => {
	 const run = snapshot?.active_run;
	 if (!run || !["running", "stopping"].includes(run.state)) return undefined;
	 let disposed = false;
	 let timer: number | undefined;
	 if (eventCursor.current.runId !== run.run_id) eventCursor.current = { runId: run.run_id, cursor: 0 };
	 const continueStream = async () => {
		 try {
			 const update = await loadRunUpdates(run.run_id, eventCursor.current.cursor);
			 if (disposed) return;
			 eventCursor.current = { runId: run.run_id, cursor: update.cursor };
			 setSnapshot(update.snapshot);
			 setScreen(deriveView(update.snapshot));
		 } catch {
			 if (!disposed) await refresh();
		 } finally {
			 if (!disposed) timer = window.setTimeout(() => void continueStream(), 500);
		 }
	 };
	 void continueStream();
	 return () => { disposed = true; if (timer !== undefined) window.clearTimeout(timer); };
  }, [refresh, snapshot?.active_run?.run_id, snapshot?.active_run?.state]);
  const perform = useCallback(async (action: () => Promise<ApplicationSnapshot>) => { setCommandPending(true); setCommandError(null); try { const next = await action(); setSnapshot(next); setScreen(deriveView(next)); } catch (error) { setCommandError(error instanceof Error ? error.message : "操作未完成，请重试。"); await refresh(); } finally { setCommandPending(false); } }, [refresh]);
  if (screen === "loading") return <main className="center-card" aria-live="polite"><div className="spinner" /><h1>正在安全启动本地工作台</h1><p>正在建立一次性本机会话并读取服务端状态。</p></main>;
  if (!snapshot) return <main className="center-card error-card"><p className="eyebrow">启动失败</p><h1>无法进入本地工作台</h1><p>{clientError}</p><button onClick={() => void refresh()}>重试</button></main>;
  return <main className="app-shell"><StatusChrome snapshot={snapshot} /><WorkspaceControls snapshot={snapshot} busy={commandPending} commandError={commandError} onSwitch={(workspace) => void perform(() => switchWorkspace(workspace))} onConnect={() => void perform(connectPlatformSession)} onLogout={() => void perform(logoutPlatformSession)} /><Pipeline /><SnapshotPanel screen={screen} snapshot={snapshot} clientError={clientError} onRefresh={() => void refresh()} /><RunRecoveryPanel snapshot={snapshot} busy={commandPending} onResume={(runId) => void perform(() => resumeRun(runId))} onDiscard={(runId) => void perform(() => discardRun(runId))} />{screen !== "error" ? <AIAssistancePanel snapshot={snapshot} busy={commandPending} onRequest={(kind) => void perform(() => requestAIAssistance(kind))} onDiscard={() => void perform(discardAISuggestion)} onUseDraft={(kind, reference, message) => void perform(() => kind === "recruiting-reply-draft" ? prepareRecruitingReply(reference, message) : prepareJobGreeting(reference, message))} /> : null}{snapshot.pending_write_intent ? <WriteConfirmationGate intent={snapshot.pending_write_intent} busy={commandPending} onConfirm={() => void perform(() => confirmWriteIntent(snapshot.pending_write_intent!.intent_id))} onCancel={() => void perform(() => cancelWriteIntent(snapshot.pending_write_intent!.intent_id))} /> : null}{screen !== "error" && snapshot.active_workspace === "job-seeking" ? <JobJourney snapshot={snapshot} busy={commandPending} onSaveGoal={(goal) => void perform(() => updateJobSearchGoal(goal))} onSearch={() => void perform(startJobSearch)} onCancel={(runId) => void perform(() => cancelRun(runId))} onInspect={(reference) => void perform(() => inspectJob(reference))} onShortlist={(reference, value) => void perform(() => setShortlisted(reference, value))} onPrepareGreeting={(reference, message) => void perform(() => prepareJobGreeting(reference, message))} /> : null}{screen !== "error" && snapshot.active_workspace === "recruiting" ? <RecruitingJourney snapshot={snapshot} busy={commandPending} onLoadOpenings={() => void perform(loadRecruitingOpenings)} onSelectOpening={(reference) => void perform(() => selectRecruitingOpening(reference))} onLoadApplicants={() => void perform(startInboundApplicants)} onCancel={(runId) => void perform(() => cancelRun(runId))} onInspectProspect={(reference) => void perform(() => inspectRecruitingProspect(reference))} onPrepareReply={(reference, message) => void perform(() => prepareRecruitingReply(reference, message))} /> : null}<WorkspacePrivacyPanel snapshot={snapshot} busy={commandPending} onExport={(workspace) => { setCommandError(null); void exportWorkspace(workspace).catch((error) => setCommandError(error instanceof Error ? error.message : "导出失败。")); }} onClear={(workspace, confirmation) => void perform(() => clearWorkspace(workspace, confirmation))} /><footer>仅监听 127.0.0.1 · 敏感招聘内容仅在内存停留 · Browser Bridge 不在本流程内</footer></main>;
}
