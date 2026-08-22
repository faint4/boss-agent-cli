export type PlatformSession = "disconnected" | "connecting" | "connected" | "stopping" | "recovery";

export type DomainError = {
  code: string;
  message: string;
  recoverable: boolean;
  recovery_action: string | null;
  correlation_id: string;
};

export type RunSummary = {
  run_id: string;
  state: string;
  progress: number | null;
  wait_reason: string | null;
};

export type JobSearchGoal = {
  objective: string;
  keyword: string;
  city: string;
  salary: string;
  experience: string;
  education: string;
};

export type JobSummary = {
  reference: string;
  title: string;
  company: string;
  location: string;
  salary: string;
  experience: string;
  education: string;
};

export type JobDetailView = {
  source: {
    job: JobSummary;
    description: string;
    company_stage: string;
    company_size: string;
    recruiter: string;
  };
  match_reasons: string[];
};

export type JobSeekingState = {
  goal: JobSearchGoal | null;
  results: JobSummary[];
  selected_job: JobDetailView | null;
  shortlist: JobSummary[];
};

export type ApplicationSnapshot = {
  schema_version: string;
  active_workspace: "job-seeking" | "recruiting";
  platform_session: PlatformSession;
  active_run: RunSummary | null;
  selected_reference: string | null;
  local_decision: string | null;
  sensitive_content_present: boolean;
  pending_write_intent: object | null;
  last_transition: string | null;
  error: DomainError | null;
  job_seeking: JobSeekingState | null;
};

export type SnapshotView = "ready" | "empty" | "error" | "recovery";

export function deriveView(snapshot: ApplicationSnapshot): SnapshotView {
  if (snapshot.platform_session === "recovery" || snapshot.error?.recoverable) {
    return "recovery";
  }
  if (snapshot.error) {
    return "error";
  }
  if (
    (snapshot.job_seeking === null || snapshot.job_seeking.goal === null) &&
    snapshot.active_run === null &&
    snapshot.selected_reference === null &&
    snapshot.local_decision === null &&
    snapshot.pending_write_intent === null
  ) {
    return "empty";
  }
  return "ready";
}
