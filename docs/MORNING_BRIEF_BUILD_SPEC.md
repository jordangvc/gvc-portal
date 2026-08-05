# Green Valley Morning Brief and Operations Huddle

## Coding-Agent Build Specification

Status: product decisions approved for implementation.

This document is the authoritative handoff for rebuilding the existing Morning Meeting prototype. When this specification conflicts with the current prototype, mock data, or older `MORNING_MEETING.md` language, this specification wins.

Implementation companion: `MORNING_BRIEF_IMPLEMENTATION_BLUEPRINT.md` translates these approved requirements into production architecture, schemas, APIs, jobs, integration flows, delivery phases, and a test scenario matrix. Use both documents for the build; this file remains authoritative for business rules.

## Product Outcome

Create a private, mobile-first daily operating system that prepares every employee for the day, helps the General Manager run a lean 6:45 AM operations huddle, updates Monday.com and Google Drive from the field, and gives Jordan an exception-only Owner Pulse without creating routine work for him.

The morning brief is not a document. It is each employee's daily control center.

## Governing Principle: Remove Work From Jordan

Every workflow must reduce work on Jordan's plate. Prefer, in order:

1. Safe automatic resolution.
2. The responsible employee or project owner.
3. The General Manager.
4. An appropriate operations or administrative owner.
5. Jordan only when owner authority, judgment, or approval is genuinely required.

Do not create cleanup, routing, follow-up, data-entry, coaching-scheduling, user-administration, or system-maintenance tasks for Jordan. Jordan receives a high-level Owner Pulse and true owner-level exceptions only.

## User and Role Model

- Existing individual portal accounts authenticate with Google.
- Access and personalization must be based on the authenticated employee, never a hard-coded list of names.
- `Operations Team` is a dynamic portal role. People can join or leave without code changes.
- The General Manager owns the morning operations huddle.
- The target operations huddle includes the General Manager and relevant operations personnel. Current known roles are:
  - Robert — Operations Manager
  - Mark — Service Manager
  - Ethan — Technician
- Donnie is the likely General Manager, but the implementation must key behavior to the General Manager role rather than the name Donnie.
- Jordan, Jake, and Andrea may receive private personal briefs, but they are not required attendees in the target operations huddle.
- Each attendee controls their own phone or tablet. Laptop support remains available, but phone is the primary layout target.

## Primary Experiences

### Portal Home

`Your Morning Brief` is the first and most prominent tile. It shows:

- Preparation readiness, such as `5 of 6 ready`.
- Last refresh time, normally `4:30 AM`.
- Number of planned stops.
- One clear action to open the brief.

### Employee Morning Brief

The private employee view is ordered as follows:

1. Weather, leave status, and preparation state.
2. Optional hard times or time windows.
3. Starting-location selector.
4. Optimized, editable stop list.
5. Incoming and outgoing Action Requests.
6. Prominent personally relevant project cards.
7. Compact company-wide `Needs attention today` list.
8. Compact `Long-term holds` list.
9. Less-prominent operations-board items.
10. Items the employee is watching.
11. Private notes and approved custom widgets.
12. Employee-relevant Fireflies decisions and notes after the meeting.

### General Manager Morning View

The General Manager sees:

- Red/yellow/green preparation status for the Operations Team.
- The meeting sequence organized by route and hard-time constraints.
- Projects that need attention today.
- Unscheduled or unplaced work to review last.
- Unacknowledged and overdue Action Requests.
- Fireflies proposals awaiting approval.
- Preparation streaks and coaching workflow.

### Owner Pulse

Jordan sees a high-level, exception-only view:

- Team preparation percentage.
- Safety stops.
- True owner decisions.
- Three-day/five-day preparation alerts as information, not routine work.
- Huddle outcome: projects covered, actions assigned, unresolved owner-level risks.
- Planning signals such as repeated route overrides.

The Owner Pulse must not become a disguised task list.

## Authoritative Data Sources

### Monday.com

Monday.com is the operational source of truth.

- Operations board: `1920364853`
- Linked Projects board: `1918846405`
- The UI must not reproduce all board columns. It should expose the useful mobile subset.
- Employees must not see financial information such as contract value, costs, margins, invoice amounts, or payment details.

Important Operations-board fields include:

- `multiple_person_mm1ht2vj` — Ops. Owner
- `status_19` — Scheduled Day
- `status` — Stage
- `color_mm1hmwdm` — Stage Detail
- `color_mm1hrm6z` — Blocked
- `color_mm1x2172` — Overdue
- `date` — Start Date
- `lookup_mknf1rdw` — mirrored Job Location
- `lookup_mkpeqd8w` — mirrored Progress
- `link_to_projects` — relation to Projects board
- `subitems` — operational subitems

Important Projects-board fields include:

- `location5` — Job Location
- `link_mkwr6ef9` — GFolder Link
- `files` — Project Photos & Files
- `link` — Take Offs Links

### Google Calendar

- Supplies personal appointments, leave context, inspections, deliveries, and genuine time constraints.
- Brief generation must not treat every project as having an arrival time.
- `Hard time / time window` is optional and displays only when a trustworthy time exists.
- When no trustworthy time exists, show `No fixed time` and do not reduce preparation readiness.

### Google Drive

- Project tiles include `Open Drive` and `Add Update` actions.
- Resolve the exact project folder through Monday's `GFolder Link`.
- Existing media convention is `GVC Job Site Media -> exact project folder -> Pictures`.
- Never create employee- or date-named media folders.
- If multiple child folders are named `Pictures`, automatically use the most recently modified one.
- If no `Pictures` folder exists, create one inside the exact linked project folder.
- Photo update flow:
  1. Employee taps `Add Update` from a project.
  2. Employee takes or selects one or more photos.
  3. Employee optionally adds a note.
  4. Employee confirms.
  5. Portal uploads to the resolved `Pictures` folder.
  6. Portal adds a Monday update containing the note and Drive references.

### Fireflies

- Process the completed daily meeting after the meeting ends.
- Each employee receives only decisions, notes, and actions relevant to them, plus a link to the complete meeting summary.
- Fireflies-derived project changes are proposals, not automatic status changes.
- Approval order is General Manager, Robert, then Mark.
- Jordan is involved only when a proposed change requires owner-level authority.
- Approved proposals write to Monday.com.

### Slack

- Use direct messages / private side chats only for Morning Brief delivery.
- **HARD RULE (Jordan 2026-08-05): never post Morning Brief, field brief, huddle
  summary, route sheet, or prep status into Slack `#operations` (or any other
  public/shared channel).** That content is always a side chat / DM — never a
  channel broadcast. Older handoff notes that floated `#operations` posting are
  obsolete; do not implement them.
- Use direct messages, never public employee-performance warnings.
- Notify the employee when the brief is ready, an Action Request is assigned, acknowledgment is due, or a request becomes overdue.
- Notify the General Manager of operational escalations.
- Jordan receives only owner-level exceptions and the approved high-level preparation alerts.
- Native phone push notifications are a future enhancement; portal and Slack are required now.

### Cloud Scheduler — Action Request daytime acks

The 30-minute acknowledgment window only works if something evaluates escalations
during the workday. Prep-cutoff (`POST /v1/tasks/morning-prep-cutoff`, ~6:50 AM)
also runs AR escalations once, but that is too early for daytime 30-min acks.

Wire a second job that POSTs `POST /v1/tasks/morning-ar-escalations` every
10–15 minutes, Monday–Friday, 7:00–17:00 America/New_York, with `X-API-Key`.
That endpoint DMs the recipient (ack-due / overdue) and DMs the owner email on
overdue — never posts to `#operations` or any channel. Idempotent: only newly
transitioned escalations notify.

## Brief Generation and Preparation

- Generate the brief at 4:30 AM America/New_York on scheduled workdays.
- Refresh when relevant Monday or Calendar data changes.
- Preparation cutoff is 6:45 AM.
- The brief should remain useful offline. Cache the morning brief and route, queue employee updates, and synchronize when connectivity returns.

An employee is prepared when the following are satisfied:

1. First stop is selected or confirmed.
2. Today's planned work is confirmed.
3. Needed materials and information have been reviewed.
4. Blockers are answered, including an explicit `None`.
5. Requests for other people have been submitted.
6. The brief was opened before 6:45 AM.

Missing preparation behavior:

- First missed scheduled workday: private warning to the employee.
- Three consecutive scheduled workdays: private employee notice plus high-level Jordan and General Manager visibility.
- Five consecutive scheduled workdays: the portal schedules a coaching call for the General Manager and employee; Jordan receives the result as information.
- Consecutive-day calculations include scheduled workdays only.

## Project Relevance and Visibility

All operations employees can see the full Operations board, but personally relevant work is more prominent.

A project is personally relevant when:

- The employee is listed in `Ops. Owner`; or
- The employee personally authored a Monday update on the project within the previous 14 scheduled workdays.

The 14-day relevance window ends sooner when the work is completed.

Project cards are collapsed by default on phones. The collapsed state shows:

- Project/job name.
- Stage and stage detail.
- Employee responsibility/relevance reason.
- Blocked/clear state.
- Stop placement and optional hard time.
- Buttons for `Open`, `Drive`, and `Add Update`.

Expanded cards may show materials, project team, progress, current actions, and other non-financial operational data.

## Blocked and Overdue Work

Blocked and overdue work is visible to the entire Operations Team because the team collectively supports execution.

Use two compact sections:

- `Needs attention today` — new blockers, overdue work, and anything affecting today's execution.
- `Long-term holds` — blocked or delayed work with no meaningful change for seven days, unless it affects today's work.

Long-term holds should not dominate the meeting or individual brief.

## Action Requests

Create a dedicated Action Requests board and retire the `Needs from Jordan` workflow immediately.

Each request includes:

- Requester.
- Needed from.
- Request category.
- Crew trade subtype when applicable.
- Plain-language need.
- Related project.
- Due date/time.
- Acknowledged timestamp.
- Completed timestamp.
- Escalation state.

Categories:

- Crew setup
  - Framing
  - Hanging
  - Scrapping
  - Finishing
  - Ceilings/ACT
  - Service
  - Other
- Customer/GC scheduling
- Materials
- Equipment
- Information
- Decision/approval
- Help needed
- Other

Rules:

- Recipient must acknowledge within 30 minutes during scheduled work hours.
- Missing acknowledgment creates a Slack DM reminder and General Manager visibility.
- Passing the due time creates the overdue escalation.
- Requests can be acknowledged from Slack or the portal.
- Migrate only active, non-clear `Needs from Jordan` values.
- Clean and deduplicate migrated requests.
- Migrated requests begin as `Needs triage`, not overdue.
- Stop using the old column after migration.

## Stop List and Route Planning

Every employee may select the daily starting point:

- Home.
- Green Valley office.
- Current location, with explicit device permission.
- Custom starting point.

Home/start locations are private and must never be written to Monday. Only the employee and narrowly authorized management can access stored home-start data.

Route behavior:

- Build stops from personally relevant scheduled work and trustworthy project locations.
- Recommend an efficient order using drive time and hard-time constraints.
- Provide one-tap Google Maps navigation.
- Allow drag-and-drop reordering.
- Accept the employee's final order without requiring a reason.
- Track route overrides as a planning signal.
- Three overrides within ten scheduled workdays creates a private General Manager planning alert, not an employee-performance warning.
- Review projects without a valid location or schedule after routed work in `Unscheduled / needs planning`.

Stop completion is intentionally lightweight:

1. Employee checks the stop complete.
2. Employee may add an optional note or photos.
3. Employee confirms the update.
4. Portal sends the update directly to Monday.com.
5. Do not require General Manager approval.
6. Do not automatically change Stage.
7. If the employee explicitly proposes a Stage change, confirm it with the employee and send it to Monday.com.

## Personal Section

- Personal content is private to the employee at all times.
- Supported initial widgets: weather, leave status, personal Calendar commitments, private notes, and reminders.
- Employees may choose approved widgets.
- Employees may submit feature requests for future widgets.
- Feature requests enter a product backlog and must not create tasks for Jordan.

## Operations Huddle

- Runs from 6:45 to 7:00 AM.
- The General Manager owns and facilitates it.
- Attendance is based on the dynamic Operations Team, not a hard-coded roster.
- Review projects project-by-project rather than person-by-person.
- Order routed work by hard-time and route/start sequence.
- Review `Unscheduled / needs planning` last.
- Each attendee controls their own screen.
- The General Manager's run sheet provides the shared sequence and preparation state.
- Every discussion must create a decision, action, risk response, or coordination change. Otherwise assign an owner and follow-up time and park it.
- Close with actions, owners, due times, and parked-topic readback.

## Technical Architecture Boundary

The client must never call Monday, Google, Fireflies, Slack, or mapping providers directly.

Recommended boundary:

`Mobile UI -> MorningBriefService -> Integration adapters`

Recommended services:

- `IdentityService` — Google identity and portal-role mapping.
- `MorningBriefService` — generation, personalization, preparation, caching.
- `OperationsBoardService` — Monday reads, normalized project model, writeback.
- `ActionRequestService` — request lifecycle, acknowledgment, escalation.
- `RouteService` — origin privacy, travel-time matrix, optimization, navigation links.
- `MediaService` — GFolder resolution, Pictures-folder selection, uploads.
- `MeetingService` — live huddle state, actions, parking lot, scorecard.
- `FirefliesProposalService` — transcript extraction, relevance, approvals, writeback.
- `NotificationService` — portal notifications and Slack DMs.
- `OwnerPulseService` — exception-only owner summaries.

All external writes must be idempotent, audited, retryable, and safe against duplicate webhook delivery.

## Minimum Data Objects

- `UserProfile`: Google identity, portal role, operations membership, privacy preferences.
- `MorningBrief`: person, workdate, generation timestamp, preparation state, relevant projects.
- `BriefPreparationEvent`: criterion, timestamp, workday streak.
- `DailyRoute`: private origin reference, ordered stops, optimization result, override state.
- `RouteStop`: project, sequence, optional hard time, completion, note, sync state.
- `ActionRequest`: requester, recipient, category, trade, need, project, due, acknowledgment, completion, escalation.
- `MeetingRun`: date, facilitator, start/end, ordered projects, parking items, scorecard.
- `FirefliesProposal`: meeting, person/project relevance, proposed mutation, approval state, audit link.
- `OwnerException`: severity, category, reason owner authority is required, resolution.

## Security and Privacy

- Enforce all privacy rules server-side, not only through hidden UI.
- Personal notes are employee-only.
- Home/start locations are private.
- Employees do not receive financial columns.
- Preparation warnings are private.
- Slack performance warnings are DMs only.
- Every integration write records actor, source, timestamp, destination, and result.

## Mobile and Accessibility Requirements

- Design for phone widths first, then tablet and laptop.
- Minimum supported width: 320 CSS pixels.
- Large tap targets and single-column primary flow.
- Project cards collapsed by default.
- No essential horizontal scrolling.
- Native form controls and semantic labels.
- Color is never the only preparation or blocker signal.
- Core morning brief and route remain readable offline.

## Approved UI Reference

The approved interactive mockup is located at:

`C:/Users/jorda/.codex/visualizations/2026/08/03/019fc57e-65bc-72a1-b021-80d334dbe38a/morning-brief-mobile.html`

It contains four approved directional states:

- Portal home.
- Employee brief.
- General Manager morning view.
- Owner Pulse.

Treat it as the visual direction, not production code.

## Delivery Sequence

The coding agent may stage implementation internally, but the final production feature must include all required behavior.

1. Replace hard-coded people and mock projects with authenticated role-based data.
2. Implement normalized Monday/Projects-board reads and financial-field filtering.
3. Build the mobile Portal Home, Employee Brief, General Manager view, and Owner Pulse.
4. Implement preparation readiness and scheduled-workday streaks.
5. Implement the dedicated Action Requests board, migration, Slack DMs, and escalation.
6. Implement private origins, route optimization, reorder tracking, Google Maps links, and offline caching.
7. Implement Drive folder resolution, photo upload, and confirmed Monday updates.
8. Implement the General Manager huddle run sheet and live meeting capture.
9. Implement Fireflies relevance, proposal approvals, and Monday writeback.
10. Add audit logging, idempotency, integration retries, permissions tests, and responsive UI tests.

## Acceptance Criteria

The build is not complete until all of the following are demonstrated:

1. A Google-authenticated employee sees only their private Morning Brief and permitted shared operations data.
2. `Your Morning Brief` is the primary Portal Home tile.
3. Briefs generate at 4:30 AM and preparation is measured at 6:45 AM on scheduled workdays.
4. Relevant projects follow Ops Owner and 14-scheduled-workday update-author rules.
5. Financial data is absent from employee APIs and UI.
6. The employee can select a private origin, optimize/reorder a route, open Google Maps, complete stops, and work through temporary loss of connectivity.
7. The employee can create, acknowledge, complete, and receive Slack DMs for Action Requests.
8. Old active `Needs from Jordan` items are cleaned, deduplicated, and migrated; the old workflow is retired.
9. The employee can upload photos into the resolved existing GVC `Pictures` folder and post the confirmed update to Monday.
10. Duplicate `Pictures` folders resolve automatically to the most recently modified folder.
11. The General Manager can see preparation, run the huddle, review unscheduled work last, and approve Fireflies proposals.
12. Jordan receives a high-level Owner Pulse without routine operational tasks.
13. Three-day/five-day preparation rules work only across scheduled workdays.
14. The interface passes 320px phone, tablet, and laptop responsive checks.
15. Integration writes are audited, idempotent, and recover safely from retries.

## Out of Scope for the Initial Production Release

- Native mobile push notifications. Portal notifications and Slack DMs are required now.
- Financial visibility for operations employees.
- Hard-coded attendance rosters.
- Automatic project Stage changes inferred without employee confirmation or Fireflies approval.
- Cleanup tasks assigned to Jordan when the system can resolve the issue safely.
