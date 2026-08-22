import { useCallback, useEffect, useState } from "react";
import { loadSnapshot } from "./api";
import { deriveView, type ApplicationSnapshot, type SnapshotView } from "./state";

type ScreenState = "loading" | SnapshotView;

const stages = ["目标", "结果", "对象", "内容", "逐项确认", "结果回执"];

function StatusChrome({ snapshot }: { snapshot: ApplicationSnapshot }) {
  const workspace = snapshot.active_workspace === "job-seeking" ? "求职工作区" : "招聘工作区";
  const session = {
    disconnected: "未连接",
    connecting: "连接中",
    connected: "已连接",
    stopping: "正在停止",
    recovery: "需要恢复",
  }[snapshot.platform_session];

  return (
    <header className="status-chrome">
      <div>
        <p className="eyebrow">本机专属 · 不会自动执行平台写入</p>
        <h1>BOSS 本地工作台</h1>
      </div>
      <dl className="status-grid">
        <div><dt>当前空间</dt><dd>{workspace}</dd></div>
        <div><dt>平台会话</dt><dd>{session}</dd></div>
        <div><dt>任务运行</dt><dd>{snapshot.active_run?.state ?? "暂无"}</dd></div>
      </dl>
    </header>
  );
}

function Pipeline() {
  return (
    <ol className="pipeline" aria-label="任务流水线">
      {stages.map((stage, index) => <li key={stage}><span>{index + 1}</span>{stage}</li>)}
    </ol>
  );
}

export function SnapshotPanel({
  screen,
  snapshot,
  clientError,
  onRefresh,
}: {
  screen: SnapshotView;
  snapshot: ApplicationSnapshot;
  clientError: string | null;
  onRefresh: () => void;
}) {
  if (screen === "empty") {
    return (
      <section className="task-card empty-card">
        <p className="eyebrow">安全外壳已就绪</p>
        <h2>从一个明确目标开始</h2>
        <p>当前没有运行中的任务，也没有待确认的平台写入。后续工作流会沿上方六个阶段逐项推进。</p>
        <p className="boundary">Browser Bridge 未连接，也不属于这条启动路径。</p>
      </section>
    );
  }
  if (screen === "ready") {
    return (
      <section className="task-card">
        <p className="eyebrow">任务状态已同步</p>
        <h2>{snapshot.selected_reference ?? snapshot.active_run?.run_id ?? "当前任务"}</h2>
        <p>{snapshot.last_transition ?? "服务端状态已准备好，等待你的下一步操作。"}</p>
      </section>
    );
  }
  if (screen === "recovery") {
    return (
      <section className="task-card recovery-card" role="alert">
        <p className="eyebrow">任务已安全暂停</p>
        <h2>{snapshot.error?.message ?? "平台会话需要恢复"}</h2>
        <p>{snapshot.error?.recovery_action ?? "处理平台会话后，请手动恢复任务；系统不会自动继续写入。"}</p>
        <button onClick={onRefresh}>重新读取状态</button>
      </section>
    );
  }
  return (
    <section className="task-card error-card" role="alert">
      <p className="eyebrow">状态读取失败</p>
      <h2>{snapshot.error?.message ?? clientError}</h2>
      <p>未执行任何平台写入。你可以重新读取服务端状态。</p>
      <button onClick={onRefresh}>重试</button>
    </section>
  );
}

export default function App() {
  const [screen, setScreen] = useState<ScreenState>("loading");
  const [snapshot, setSnapshot] = useState<ApplicationSnapshot | null>(null);
  const [clientError, setClientError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setScreen("loading");
    setClientError(null);
    try {
      const nextSnapshot = await loadSnapshot();
      setSnapshot(nextSnapshot);
      setScreen(deriveView(nextSnapshot));
    } catch (error) {
      setClientError(error instanceof Error ? error.message : "本地工作台发生未知错误。");
      setScreen("error");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (screen === "loading") {
    return <main className="center-card" aria-live="polite"><div className="spinner" /><h1>正在安全启动本地工作台</h1><p>正在建立一次性本机会话并读取服务端状态。</p></main>;
  }

  if (!snapshot) {
    return <main className="center-card error-card"><p className="eyebrow">启动失败</p><h1>无法进入本地工作台</h1><p>{clientError}</p><button onClick={() => void refresh()}>重试</button></main>;
  }

  return (
    <main className="app-shell">
      <StatusChrome snapshot={snapshot} />
      <Pipeline />
      <SnapshotPanel
        screen={screen}
        snapshot={snapshot}
        clientError={clientError}
        onRefresh={() => void refresh()}
      />
      <footer>仅监听 127.0.0.1 · 会话凭据只保存在内存中</footer>
    </main>
  );
}
