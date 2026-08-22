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

export type RecruitingOpening = {
  reference: string;
  title: string;
  status: string;
};

export type InboundApplicant = {
  reference: string;
  display_name: string;
  headline: string;
};

export type RecruitingProspectContext = {
  prospect: InboundApplicant;
  resume_text: string;
  chat_messages: string[];
  contact_details: string[];
};

export type RecruitingState = {
  openings: RecruitingOpening[];
  selected_opening: RecruitingOpening | null;
  applicants: InboundApplicant[];
  selected_prospect: RecruitingProspectContext | null;
};

export type WriteIntentState =
  | "pending"
  | "executing"
  | "succeeded"
  | "rejected"
  | "expired"
  | "cancelled"
  | "uncertain";

export type WriteIntent = {
  intent_id: string;
  workspace: "job-seeking" | "recruiting";
  target_reference: string;
  target_label: string;
  context_label: string;
  destination_label: string;
  action: string;
  payload_preview: string;
  warnings: string[];
  expires_at: string;
  state: WriteIntentState;
  outcome_message: string | null;
};

export type ApplicationSnapshot = {
  schema_version: string;
  active_workspace: "job-seeking" | "recruiting";
  platform_session: PlatformSession;
  active_run: RunSummary | null;
  selected_reference: string | null;
  local_decision: string | null;
  sensitive_content_present: boolean;
  pending_write_intent: WriteIntent | null;
  last_transition: string | null;
  error: DomainError | null;
  job_seeking: JobSeekingState | null;
  recruiting: RecruitingState | null;
};

export type SnapshotView = "ready" | "empty" | "error" | "recovery";

export function deriveView(snapshot: ApplicationSnapshot): SnapshotView {
  if (snapshot.pending_write_intent !== null) {
    return "ready";
  }
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
    snapshot.pending_write_intent === null &&
    (snapshot.recruiting === null || (
      snapshot.recruiting.openings.length === 0 &&
      snapshot.recruiting.selected_opening === null &&
      snapshot.recruiting.applicants.length === 0
    ))
  ) {
    return "empty";
  }
  return "ready";
}
