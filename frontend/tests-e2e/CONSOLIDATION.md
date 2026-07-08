# E2E Layers And Consolidation

This directory keeps the existing Playwright project boundaries:

- `chromium` runs anonymous and login-flow specs only.
- `chromium-authed-admin` runs `*.authed-admin.spec.ts`.
- `chromium-authed-user` runs `*.authed-user.spec.ts`.
- `workers: 1` stays in `playwright.config.ts` to avoid dev DB races.

## Runnable Layers

- `npm run test:ui:smoke`: smallest auth/dashboard infrastructure check.
- `npm run test:ui:a11y`: accessibility-only specs.
- `npm run test:ui:live-api`: browser plus real backend/API flows.
- `npm run test:ui:mocked-ui`: deterministic mocked UI contract specs.
- `npm run test:ui:playwright-runner`: real Playwright runner to dashboard drawer artifact acceptance.
- `npm run test:ui:legacy-duplicates`: old overlap retained until deletion is approved.
- `npm run test:ui:regression`: full suite.

## Current Authorities

- Drawer contract: `run-details.authed-admin.spec.ts`.
  `report-viewer.authed-admin.spec.ts` and `run-diff.authed-admin.spec.ts` remain runnable under
  `test:ui:legacy-duplicates`, but the normal mocked UI layer uses `run-details` as the authority.

- AI Analysis tab contract: `ai-insights.authed-admin.spec.ts`.
  Owns the fourth drawer tab (`drawer-tab-ai`): disabled/degraded state (no API key), empty +
  on-demand Generate, the structured diagnosis card (category/confidence/evidence/next-steps/
  regression badge), Regenerate, and the transport-error state. Fully mocks `/runs/{id}/ai-analysis`
  so it is deterministic without a real LLM key.

- Trigger modal contract: `trigger-modal.authed-admin.spec.ts`.
  `trigger-run.authed-admin.spec.ts` is retained as a legacy duplicate until file deletion is
  approved.

- Schedule modal contract: `schedule-mgmt.authed-admin.spec.ts`.
  Real schedule execution belongs in `journey-schedule-heartbeat.authed-admin.spec.ts`.
  `schedule.authed-admin.spec.ts` and `schedule-trigger.authed-admin.spec.ts` are retained as
  legacy duplicates until deletion is approved.

- User management contract: `user-management.authed-admin.spec.ts`.
  It preserves the live API/admin CRUD flow and now also owns the validation, backend-error,
  role-selector, non-admin visibility, row-action, and storage-cleanup contract cases migrated
  from `user-mgmt.authed-admin.spec.ts`. The old file is retained only under
  `test:ui:legacy-duplicates` until deletion is approved.

## Duplicate Migration Map

These files stay runnable only through `npm run test:ui:legacy-duplicates` until deletion is
approved. The normal `mocked-ui` and `live-api` layers already route to the authority files below.

| Legacy file | Authority | Covered authority scenarios | Remaining deletion condition |
| --- | --- | --- | --- |
| `trigger-run.authed-admin.spec.ts` | `trigger-modal.authed-admin.spec.ts` | open/close, args/timeout persistence, valid submit, backend error banner, runner options, Playwright args copy, saved profile load/update, selected files and markers | Delete after approval; no extra scenario needs migration. |
| `schedule.authed-admin.spec.ts` | `schedule-mgmt.authed-admin.spec.ts` | open modal, cron preview, invalid cron, save, delete, immediate trigger, timezone, close paths, empty cron/name, enabled toggle | Delete after approval; live schedule heartbeat stays in `journey-schedule-heartbeat.authed-admin.spec.ts`. |
| `schedule-trigger.authed-admin.spec.ts` | `schedule-mgmt.authed-admin.spec.ts` plus `journey-schedule-heartbeat.authed-admin.spec.ts` | pre-populated edit modal and create-from-scratch are modal contract; trigger-to-dashboard behavior is journey/live flow | Delete after approval once live heartbeat remains green. |
| `report-viewer.authed-admin.spec.ts` | `run-details.authed-admin.spec.ts` | report summary/buttons, fullscreen report open/close, Escape close, report missing/generating states through drawer contract | Delete after approval; keep one final `run-details` pass before deletion. |
| `run-diff.authed-admin.spec.ts` | `run-details.authed-admin.spec.ts` | five diff buckets, empty baseline, case expansion, history, flaky badge, loading/error states | Delete after approval; no extra scenario needs migration. |
| `user-mgmt.authed-admin.spec.ts` | `user-management.authed-admin.spec.ts` | modal fields, non-admin hidden users button, create user, close modal, row actions, required-field validation, backend creation error, role selector, storage cleanup API call | Delete after approval; normal layers now use `user-management` as the single authority. |

## Deletion Rule

No old spec file is deleted in this phase. Deleting or skipping legacy specs needs explicit user
approval because it changes the regression surface.
