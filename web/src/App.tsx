import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  cancelRun,
  connectPlatformSession,
  inspectJob,
  loadSnapshot,
  logoutPlatformSession,
  setShortlisted,
  startJobSearch,
  switchWorkspace,
  updateJobSearchGoal,
} from "./api";
import {
  deriveView,
  type ApplicationSnapshot,
  type JobSearchGoal,
  type SnapshotView,
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
  return <header className="status-chrome"><div><p className="eyebrow">本机专属 · 只读搜索</p><h1>BOSS 本地工作台</h1></div><dl className="status-grid"><div><dt>当前空间</dt><dd>{workspace}</dd></div><div><dt>平台会话</dt><dd>{session}</dd></div><div><dt>搜索运行</dt><dd>{snapshot.active_run?.state ?? "暂无"}</dd></div></dl></header>;
}

function Pipeline() {
  return <ol className="pipeline" aria-label="任务流水线">{stages.map((stage, index) => <li key={stage}><span>{index + 1}</span>{stage}</li>)}</ol>;
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

export function JobJourney({ snapshot, busy, onSaveGoal, onSearch, onCancel, onInspect, onShortlist }: {
  snapshot: ApplicationSnapshot;
  busy: boolean;
  onSaveGoal: (goal: JobSearchGoal) => void;
  onSearch: () => void;
  onCancel: (runId: string) => void;
  onInspect: (reference: string) => void;
  onShortlist: (reference: string, shortlisted: boolean) => void;
}) {
  const state = snapshot.job_seeking;
  if (snapshot.active_workspace !== "job-seeking" || !state) {
    return <section className="task-card"><p className="eyebrow">招聘工作区</p><h2>求职旅程已隔离</h2><p>切换回求职工作区即可继续，目标和收藏不会丢失。</p></section>;
  }
  const running = snapshot.active_run && ["running", "cancelling"].includes(snapshot.active_run.state);
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
    <section className="panel"><p className="eyebrow">1 · 求职目标</p><h2>定义这次搜索</h2><form className="goal-form" onSubmit={submitGoal}><label>目标<input name="objective" required defaultValue={state.goal?.objective ?? ""} placeholder="例如：寻找后端工程师岗位" /></label><label>关键词<input name="keyword" required defaultValue={state.goal?.keyword ?? ""} placeholder="Python" /></label><label>城市<input name="city" defaultValue={state.goal?.city ?? ""} placeholder="上海" /></label><label>薪资<input name="salary" defaultValue={state.goal?.salary ?? ""} placeholder="20-40K" /></label><label>经验<input name="experience" defaultValue={state.goal?.experience ?? ""} placeholder="3-5年" /></label><label>学历<input name="education" defaultValue={state.goal?.education ?? ""} placeholder="本科" /></label><div className="form-actions"><button disabled={busy}>保存目标</button><button type="button" disabled={busy || !state.goal || snapshot.platform_session !== "connected" || Boolean(running)} onClick={onSearch}>开始只读搜索</button>{running && snapshot.active_run ? <button type="button" className="danger-button" disabled={busy || snapshot.active_run.state === "cancelling"} onClick={() => onCancel(snapshot.active_run!.run_id)}>取消搜索</button> : null}</div></form>{snapshot.active_run ? <div className="run-progress" aria-live="polite"><strong>{snapshot.active_run.state === "recovery" ? "搜索已暂停" : `搜索 ${snapshot.active_run.state}`}</strong><progress max="100" value={snapshot.active_run.progress ?? 0} /><span>{snapshot.active_run.progress ?? 0}% · 已找到 {state.results.length} 个职位</span></div> : null}</section>
    <section className="panel"><p className="eyebrow">2 · 搜索结果</p><h2>{state.results.length ? `${state.results.length} 个职位` : snapshot.active_run?.state === "completed" ? "没有匹配结果" : "等待搜索"}</h2><div className="job-list">{state.results.map((job) => <article className="job-card" key={job.reference}><div><h3>{job.title}</h3><p>{job.company} · {job.location || "地点未提供"}</p><small>{[job.salary, job.experience, job.education].filter(Boolean).join(" · ")}</small></div><div className="card-actions"><button className="secondary-button" disabled={busy} onClick={() => onInspect(job.reference)}>查看来源事实</button><button disabled={busy} onClick={() => onShortlist(job.reference, !shortlisted.has(job.reference))}>{shortlisted.has(job.reference) ? "移出收藏" : "加入收藏"}</button></div></article>)}</div></section>
    {state.selected_job ? <section className="panel detail-panel"><p className="eyebrow">3 · 职位详情</p><h2>{state.selected_job.source.job.title}</h2><dl className="facts"><div><dt>公司</dt><dd>{state.selected_job.source.job.company}</dd></div><div><dt>地点</dt><dd>{state.selected_job.source.job.location || "未提供"}</dd></div><div><dt>薪资</dt><dd>{state.selected_job.source.job.salary || "未提供"}</dd></div><div><dt>招聘者</dt><dd>{state.selected_job.source.recruiter || "未提供"}</dd></div></dl><h3>平台来源原文</h3><p className="source-copy">{state.selected_job.source.description || "平台未提供职位描述。"}</p><h3>本地匹配理由</h3>{state.selected_job.match_reasons.length ? <ul>{state.selected_job.match_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <p>没有足够的来源事实可生成匹配理由。</p>}</section> : null}
    <section className="panel shortlist-panel"><p className="eyebrow">4 · 本地收藏</p><h2>{state.shortlist.length ? `${state.shortlist.length} 个已收藏职位` : "收藏为空"}</h2><p>收藏只保存在当前求职工作区，重启后仍会保留。</p>{state.shortlist.map((job) => <article className="shortlist-row" key={job.reference}><span><strong>{job.title}</strong><small>{job.company}</small></span><button className="secondary-button" disabled={busy} onClick={() => onShortlist(job.reference, false)}>移出</button></article>)}</section>
  </div>;
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
  const refresh = useCallback(async (showLoading = false) => { if (showLoading) setScreen("loading"); setClientError(null); try { const next = await loadSnapshot(); setSnapshot(next); setScreen(deriveView(next)); } catch (error) { setClientError(error instanceof Error ? error.message : "本地工作台发生未知错误。"); setScreen("error"); } }, []);
  useEffect(() => { void refresh(true); }, [refresh]);
  useEffect(() => { if (!snapshot || (!['connecting', 'stopping'].includes(snapshot.platform_session) && !['running', 'cancelling'].includes(snapshot.active_run?.state ?? ''))) return undefined; const timer = window.setInterval(() => void refresh(), 500); return () => window.clearInterval(timer); }, [refresh, snapshot]);
  const perform = useCallback(async (action: () => Promise<ApplicationSnapshot>) => { setCommandPending(true); setCommandError(null); try { const next = await action(); setSnapshot(next); setScreen(deriveView(next)); } catch (error) { setCommandError(error instanceof Error ? error.message : "操作未完成，请重试。"); } finally { setCommandPending(false); } }, []);
  if (screen === "loading") return <main className="center-card" aria-live="polite"><div className="spinner" /><h1>正在安全启动本地工作台</h1><p>正在建立一次性本机会话并读取服务端状态。</p></main>;
  if (!snapshot) return <main className="center-card error-card"><p className="eyebrow">启动失败</p><h1>无法进入本地工作台</h1><p>{clientError}</p><button onClick={() => void refresh()}>重试</button></main>;
  return <main className="app-shell"><StatusChrome snapshot={snapshot} /><WorkspaceControls snapshot={snapshot} busy={commandPending} commandError={commandError} onSwitch={(workspace) => void perform(() => switchWorkspace(workspace))} onConnect={() => void perform(connectPlatformSession)} onLogout={() => void perform(logoutPlatformSession)} /><Pipeline /><SnapshotPanel screen={screen} snapshot={snapshot} clientError={clientError} onRefresh={() => void refresh()} />{screen !== "error" && screen !== "recovery" ? <JobJourney snapshot={snapshot} busy={commandPending} onSaveGoal={(goal) => void perform(() => updateJobSearchGoal(goal))} onSearch={() => void perform(startJobSearch)} onCancel={(runId) => void perform(() => cancelRun(runId))} onInspect={(reference) => void perform(() => inspectJob(reference))} onShortlist={(reference, value) => void perform(() => setShortlisted(reference, value))} /> : null}<footer>仅监听 127.0.0.1 · 求职目标与收藏保存在本机独立工作区</footer></main>;
}
