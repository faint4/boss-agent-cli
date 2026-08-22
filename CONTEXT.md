# Local Recruitment Workspace

A local, single-operator product that provides separate job-seeking and recruiting workspaces while reusing the project's recruitment-platform capabilities.

## Language

**Local Operator**:
The person using the product on the current computer. One Local Operator may switch between the Job-Seeking Workspace and Recruiting Workspace.
_Avoid_: User, account

**Job Seeker**:
The person using the Job-Seeking Workspace to discover, evaluate, and act on job opportunities.
_Avoid_: Candidate

**Recruiting Prospect**:
A person evaluated or contacted from the Recruiting Workspace for a job opening.
_Avoid_: Candidate, user

**Job-Seeking Workspace**:
The role-isolated area where a Job Seeker sets goals, searches and filters jobs, reviews details, and manages a job shortlist.
_Avoid_: Candidate mode, candidate side

**Recruiting Workspace**:
The role-isolated area where the Local Operator selects openings, discovers or reviews Recruiting Prospects, examines resumes and conversations, and decides on follow-up actions.
_Avoid_: Recruiter mode, HR side

**Platform Write**:
An action that changes remote recruitment-platform state or communicates with another person, including applying, greeting, replying, requesting a resume, and changing a job's publication state.
_Avoid_: Action, automation

**Confirmation Gate**:
The mandatory Local Operator approval immediately before each Platform Write. The first release does not perform unattended or batch Platform Writes.
_Avoid_: Review queue, optional confirmation

**Core Journey**:
The single end-to-end workflow delivered for each workspace in the first release. A Core Journey is narrower than exposing every existing CLI or MCP capability.
_Avoid_: Full feature parity, command coverage

**Platform Session**:
The authenticated relationship between exactly one Workspace and BOSS. A Platform Session is never shared across Workspaces.
_Avoid_: Global login, shared cookie

**Job Shortlist**:
The Job Seeker's local collection of job opportunities selected for later comparison or action.
_Avoid_: Favorites, application queue

**Inbound Applicant**:
A Recruiting Prospect who has initiated or entered a conversation for the currently selected job opening.
_Avoid_: Lead, candidate record

**Sensitive Recruiting Content**:
Personal information about a Recruiting Prospect, including resumes, contact details, and conversation content, that is retrieved only when needed and is not retained by default.
_Avoid_: Candidate data, profile cache

**Write Intent**:
An immutable, short-lived proposal for one Platform Write to one target. It records exactly what the Confirmation Gate asks the Local Operator to approve.
_Avoid_: Pending action, batch approval

**Recoverable Run**:
A visible execution of a Core Journey that can stop without performing an unconfirmed Platform Write and can later resume from saved non-sensitive state.
_Avoid_: Background job, automation task
