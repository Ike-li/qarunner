# qarunner Comprehensive E2E Test Plan

## Application Overview

qarunner is a single-page application (React 18 + TypeScript + Vite 5 + Semi UI 2) for automated test execution, scheduling, and regression comparison. It features a full-screen login overlay, dashboard with stats cards, suite pass-rate trend sparkline, project sidebar, execution records table with filtering, run details drawer (logs/report/diff tabs), trigger-run modal with profile management, add-suite modal (local path or Git clone), schedule management, user management (admin only), and fullscreen report/terminal overlays. This plan covers all functional areas using data-testid selectors (kebab-case), covering happy paths, edge cases, error handling, and empty states across the complete application surface.

## Test Scenarios

### 1. Login and Authentication

**Seed:** `frontend/tests-e2e/seed.authed-admin.spec.ts`

#### 1.1. Login form renders correctly for unauthenticated users

**File:** `tests-e2e/login-auth.spec.ts`

**Steps:**
  1. Navigate to the application root URL
    - expect: Page should load at the app root URL
  2. Observe the login overlay elements
    - expect: login-title should display the platform name 'qarunner'
    - expect: login-subtitle should be visible
    - expect: login-username input field should be visible and empty
    - expect: login-password input field should be visible and empty
    - expect: login-submit button should be visible
    - expect: Theme toggle should be visible in top-right corner
    - expect: Language toggle should be visible in top-right corner

#### 1.2. Failed login with wrong credentials shows error banner

**File:** `tests-e2e/login-auth.spec.ts`

**Steps:**
  1. Navigate to the app
    - expect: Login form is displayed
  2. Enter 'wrong_user' as username
    - expect: login-username field contains 'wrong_user'
  3. Enter 'wrong_password' as password
    - expect: login-password field contains 'wrong_password'
  4. Click the login-submit button
    - expect: login-submit button should show loading state briefly
  5. Wait for the login request to complete
    - expect: login-error alert should become visible
    - expect: login-error should contain 'Incorrect username or password'
    - expect: Login form should remain visible

#### 1.3. Successful login as admin redirects to dashboard

**File:** `tests-e2e/login-auth.spec.ts`

**Steps:**
  1. Navigate to the app
    - expect: Login form is displayed
  2. Enter 'admin' as username and the configured admin password
  3. Click login-submit button
    - expect: login-submit shows loading state briefly
  4. Wait for dashboard to load
    - expect: profile-username should display 'admin'
    - expect: profile-role should display 'admin'
    - expect: stat-total should be visible
    - expect: stat-success-rate should be visible
    - expect: stat-failed should be visible
    - expect: stat-active should be visible
    - expect: execution-records-title should be visible
    - expect: Sidebar and header should be visible

#### 1.4. Logout from the header returns to login screen

**File:** `tests-e2e/login-auth.spec.ts`

**Steps:**
  1. Login as admin first
    - expect: Dashboard is displayed
  2. Click the logout button in the header
    - expect: Login overlay should be displayed again
    - expect: login-title should be visible
    - expect: login-submit should be visible

#### 1.5. Login form field validation - empty username

**File:** `tests-e2e/login-auth.spec.ts`

**Steps:**
  1. Navigate to the app
    - expect: Login form is displayed
  2. Leave username empty, enter any password
  3. Click login-submit
    - expect: Native HTML5 form validation should block submission

#### 1.6. Login form field validation - empty password

**File:** `tests-e2e/login-auth.spec.ts`

**Steps:**
  1. Navigate to the app
    - expect: Login form is displayed
  2. Enter any username, leave password empty
  3. Click login-submit
    - expect: Native HTML5 form validation should block submission

#### 1.7. Theme toggle on login screen switches between dark and light

**File:** `tests-e2e/login-auth.spec.ts`

**Steps:**
  1. Navigate to the app
    - expect: Login form is displayed
  2. Click the theme toggle button
    - expect: The theme should switch
    - expect: The icon should change accordingly
  3. Click the theme toggle button again
    - expect: The theme should switch back

#### 1.8. Language toggle on login screen switches between EN and ZH

**File:** `tests-e2e/login-auth.spec.ts`

**Steps:**
  1. Navigate to the app
    - expect: Login form is displayed with default language
  2. Click the language toggle button
    - expect: All UI labels and placeholders should switch to the other language
  3. Click the language toggle button again
    - expect: UI should switch back to the original language

### 2. Dashboard Layout and Header

**Seed:** `frontend/tests-e2e/seed.authed-admin.spec.ts`

#### 2.1. Dashboard renders all four stat cards with correct labels

**File:** `tests-e2e/dashboard.authed-admin.spec.ts`

**Steps:**
  1. Login as admin
    - expect: Dashboard is displayed
  2. Check the stats cards row
    - expect: stat-total should be visible with a count
    - expect: stat-success-rate should be visible with a percentage
    - expect: stat-failed should be visible with a count
    - expect: stat-active should be visible with a count

#### 2.2. Header displays user info and control buttons for admin

**File:** `tests-e2e/dashboard.authed-admin.spec.ts`

**Steps:**
  1. Login as admin
    - expect: Dashboard is displayed
  2. Check header elements
    - expect: profile-username shows 'admin'
    - expect: profile-role shows 'admin'
    - expect: open-users-button should be visible (admin only)
    - expect: open-trigger-button should be visible
    - expect: Theme toggle button should be present
    - expect: Language toggle button should be present
    - expect: Logout button should be present

#### 2.3. Sidebar shows 'All Suites' with add-suite button

**File:** `tests-e2e/dashboard.authed-admin.spec.ts`

**Steps:**
  1. Login as admin
    - expect: Dashboard is displayed
  2. Check the sidebar
    - expect: 'All Suites' heading should be visible
    - expect: open-add-suite-button should be visible in the sidebar
    - expect: If no suites: 'No suites scanned' message should appear

#### 2.4. Language toggle in header switches UI language

**File:** `tests-e2e/dashboard.authed-admin.spec.ts`

**Steps:**
  1. Login as admin
    - expect: Dashboard is displayed
  2. Click the language toggle in the header
    - expect: Header text, stats card titles, and table title should switch language
  3. Toggle back
    - expect: UI should revert to original language

#### 2.5. Theme toggle in header switches between dark and light

**File:** `tests-e2e/dashboard.authed-admin.spec.ts`

**Steps:**
  1. Login as admin
    - expect: Dashboard is displayed
  2. Click the theme toggle in the header
    - expect: The visual theme should switch
    - expect: The icon should change accordingly
  3. Toggle back
    - expect: The theme should revert

#### 2.6. Suite trend sparkline appears when a suite is selected from sidebar

**File:** `tests-e2e/dashboard.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with mocked /tests returning a suite name
    - expect: Dashboard is displayed
  2. Click on a suite name in the sidebar
    - expect: The suite item should become visually active
    - expect: suite-trend section should become visible
    - expect: suite-trend-svg should render the trend sparkline
    - expect: suite-trend-latest should show the latest pass rate percentage
  3. Click 'All Suites' to deselect
    - expect: suite-trend should no longer be visible

#### 2.7. Suite trend shows insufficient-data hint with fewer than 2 runs

**File:** `tests-e2e/dashboard.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and select a suite with only 1 data point
    - expect: suite-trend should be visible
  2. Check the trend section
    - expect: suite-trend-insufficient should be visible
    - expect: suite-trend-svg should have count 0 or not be rendered

### 3. Runs Table and Filtering

**Seed:** `frontend/tests-e2e/seed.authed-admin.spec.ts`

#### 3.1. Execution records table displays with correct columns

**File:** `tests-e2e/runs-table.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with mocked run data
    - expect: Dashboard is displayed
  2. Check the runs table
    - expect: execution-records-title should be visible
    - expect: Table columns should include: Run ID, Lock status, Target Suite, Status, Owner, Results, Pass Rate, Duration, Created At
    - expect: Each run row should show the run ID truncated to 8 chars
    - expect: Status tags should use appropriate colors per status
    - expect: Progress bars should show pass rate

#### 3.2. Filter by status shows only matching runs

**File:** `tests-e2e/runs-table.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with mocked runs of mixed statuses
    - expect: Dashboard is displayed
  2. Click filter-status-select to open the dropdown
    - expect: Dropdown options appear
  3. Select 'failed' from the status filter dropdown
    - expect: Table should only show rows with 'failed' status
  4. Select 'completed' from the status filter dropdown
    - expect: Table should only show rows with 'completed' status

#### 3.3. Filter by owner shows only matching runs

**File:** `tests-e2e/runs-table.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with mocked runs from multiple owners
    - expect: Dashboard is displayed
  2. Click filter-owner-select to open the dropdown
    - expect: Dropdown shows available owner names
  3. Select a specific owner
    - expect: Table should only show runs created by that owner

#### 3.4. Search by Run ID filters correctly

**File:** `tests-e2e/runs-table.authed-admin.spec.ts`

**Steps:**
  1. Login as admin
    - expect: Dashboard is displayed
  2. Enter a partial run ID in search-run-input
    - expect: Table should only display runs whose ID contains the search text

#### 3.5. Reset filters restores all runs

**File:** `tests-e2e/runs-table.authed-admin.spec.ts`

**Steps:**
  1. Login as admin
    - expect: Dashboard is displayed
  2. Apply a status or owner filter so the table shows fewer rows
    - expect: Table shows filtered subset
  3. Click reset-filters-button
    - expect: All runs should be displayed again
    - expect: The filter controls should be reset to 'ALL'

#### 3.6. Combined filters narrow results correctly

**File:** `tests-e2e/runs-table.authed-admin.spec.ts`

**Steps:**
  1. Login as admin
    - expect: Dashboard is displayed
  2. Set status filter to 'completed'
    - expect: Table shows completed runs only
  3. Additionally search by a specific run ID fragment
    - expect: Table should only show completed runs matching the ID

#### 3.7. Log filter tab buttons filter by All / Manual / Scheduled

**File:** `tests-e2e/runs-table.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with mocked runs of different trigger types
    - expect: Dashboard is displayed
  2. Click the 'Manually Triggered' radio button
    - expect: Table should show only manually-triggered runs
  3. Click the 'Scheduled Runs' radio button
    - expect: Table should show only scheduled runs
  4. Click 'All Runs' radio button
    - expect: Table should show all runs again

#### 3.8. Empty table state shows placeholder with 'Launch First Run' button

**File:** `tests-e2e/runs-table.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with empty /runs response
    - expect: Dashboard is displayed
  2. Check the runs table area
    - expect: 'No runs yet' placeholder message should be visible
    - expect: 'Launch your first run' button should be visible
    - expect: Clicking the launch button should open the trigger modal

### 4. Trigger Run Modal

**Seed:** `frontend/tests-e2e/seed.authed-admin.spec.ts`

#### 4.1. Trigger run modal opens and closes via header button

**File:** `tests-e2e/trigger-modal.authed-admin.spec.ts`

**Steps:**
  1. Login as admin
    - expect: Dashboard is displayed
  2. Click open-trigger-button in the header
    - expect: trigger-modal should become visible
    - expect: trigger-runner-select should be visible
    - expect: trigger-args-input should be visible
    - expect: trigger-timeout-input should be visible
    - expect: trigger-cancel-button should be visible
    - expect: trigger-submit-button should be visible
  3. Click trigger-cancel-button
    - expect: trigger-modal should not be visible

#### 4.2. Runner select has pytest and playwright options

**File:** `tests-e2e/trigger-modal.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open the trigger modal
    - expect: trigger-modal is visible
  2. Click trigger-runner-select
    - expect: Dropdown includes 'pytest' option
    - expect: Dropdown includes 'playwright' option

#### 4.3. Fill args and timeout fields and verify values persist

**File:** `tests-e2e/trigger-modal.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open the trigger modal
    - expect: trigger-modal is visible
  2. Enter '-k smoke --verbose' in trigger-args-input
    - expect: trigger-args-input has value '-k smoke --verbose'
  3. Enter '120' in trigger-timeout-input
    - expect: trigger-timeout-input has value '120'

#### 4.4. Submit trigger run with valid data sends POST and closes modal

**File:** `tests-e2e/trigger-modal.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with mocked /tests returning a suite
    - expect: Dashboard is displayed
  2. Open trigger modal and fill timeout value
    - expect: Modal is visible with data filled
  3. Click trigger-submit-button
    - expect: A POST /runs request should be made
    - expect: On success: trigger-modal should close
    - expect: On error: trigger-form-error should be visible

#### 4.5. Trigger modal shows error banner on backend error

**File:** `tests-e2e/trigger-modal.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with mocked /runs POST returning 400
    - expect: Dashboard is displayed
  2. Open trigger modal, fill values, and click submit
    - expect: trigger-form-error banner should appear with error message
    - expect: Modal should remain open

#### 4.6. Save as profile creates a new profile

**File:** `tests-e2e/trigger-modal.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open trigger modal
    - expect: trigger-modal is visible
  2. Fill runner, args, and timeout
    - expect: Fields have values
  3. Click the 'Save as Profile' button
    - expect: Profile name and description input fields should appear
  4. Enter a profile name and click save
    - expect: The profile should be saved
    - expect: The save form should collapse

#### 4.7. Profile select loads saved profile settings

**File:** `tests-e2e/trigger-modal.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with a mocked profile available
    - expect: Dashboard is displayed
  2. Open trigger modal
    - expect: trigger-modal is visible
  3. Select a profile from trigger-profile-select
    - expect: The runner, args, and timeout fields should be populated from the profile
    - expect: The selected file tree and markers should update

### 5. Run Details Drawer

**Seed:** `frontend/tests-e2e/seed.authed-admin.spec.ts`

#### 5.1. Clicking a run row opens the details drawer

**File:** `tests-e2e/run-details.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with at least one run in the table
    - expect: Dashboard is displayed with runs
  2. Click the first row in the runs table
    - expect: A drawer (SideSheet) should open
    - expect: Drawer should show run status badge
    - expect: Drawer should show run info matrix (runner, exit code, created by, environment, timestamps)
    - expect: Three tabs should be visible: drawer-tab-logs, drawer-tab-report, drawer-tab-diff

#### 5.2. Logs tab shows console output when available

**File:** `tests-e2e/run-details.authed-admin.spec.ts`

**Steps:**
  1. Open run details drawer for a completed run with stdout
    - expect: Drawer is open
  2. Check the logs tab (default active)
    - expect: Console log content should be visible in the terminal block
    - expect: Copy button should be available
    - expect: Download button should be available
    - expect: Fullscreen toggle button should be available

#### 5.3. Report tab shows summary bar and report buttons

**File:** `tests-e2e/run-details.authed-admin.spec.ts`

**Steps:**
  1. Open run details drawer for a run with report
    - expect: Drawer is open
  2. Click drawer-tab-report
    - expect: Summary metrics should be visible (passed, failed, error, skipped, duration)
    - expect: Segment bar chart should show proportional segments
    - expect: If report exists: report-fullscreen-button and report-new-window-link should be visible

#### 5.4. Report tab shows downloadable runner artifacts

**File:** `tests-e2e/run-details.authed-admin.spec.ts`

**Steps:**
  1. Open run details drawer for a run with Playwright artifacts
    - expect: Drawer is open
  2. Click drawer-tab-report
    - expect: run-artifacts section should be visible
    - expect: trace / screenshot / video / other artifacts should be grouped by type
    - expect: trace artifacts should appear before screenshot, video, and other artifacts
    - expect: artifact links should show relative paths, MIME types, and sizes
    - expect: download-all action should download `/runs/{run_id}/artifacts.zip`
    - expect: each artifact link should download from `/runs/{run_id}/artifacts/{path}`

#### 5.5. Diff tab shows cross-run comparison buckets

**File:** `tests-e2e/run-details.authed-admin.spec.ts`

**Steps:**
  1. Open run details drawer for a completed run
    - expect: Drawer is open
  2. Click drawer-tab-diff
    - expect: If diff data available: diff buckets should render
    - expect: If no baseline: diff-empty-baseline should be visible
    - expect: Each bucket shows the case count and colored header

#### 5.6. Expanding a diff case shows cross-run history

**File:** `tests-e2e/run-details.authed-admin.spec.ts`

**Steps:**
  1. Open diff tab for a run with diff data
    - expect: Diff buckets are visible
  2. Click diff-case-toggle on a case row
    - expect: case-history section should appear showing colored history dots
    - expect: If flaky: flaky-badge should be visible

#### 5.7. Re-run button is visible for completed runs

**File:** `tests-e2e/run-details.authed-admin.spec.ts`

**Steps:**
  1. Open drawer for a completed run
    - expect: Drawer displays run details
  2. Check the drawer header action buttons
    - expect: 'Re-run' button should be visible
    - expect: 'Delete Run' button should be visible (unless run is locked)

#### 5.8. Cancel button is visible for running/queued runs

**File:** `tests-e2e/run-details.authed-admin.spec.ts`

**Steps:**
  1. Open drawer for a running or queued run
    - expect: Drawer displays run details
  2. Check the drawer header
    - expect: 'Cancel Run' button should be visible instead of Re-run/Delete buttons

#### 5.9. Fullscreen terminal overlay opens and closes

**File:** `tests-e2e/run-details.authed-admin.spec.ts`

**Steps:**
  1. Open drawer for a run with logs
    - expect: Drawer is open with logs tab active
  2. Click the fullscreen toggle button in the terminal header
    - expect: Fullscreen terminal overlay should appear
  3. Close the overlay using the close button or Escape key
    - expect: Fullscreen overlay should close
    - expect: Drawer should still be visible

#### 5.10. Fullscreen report overlay opens and closes

**File:** `tests-e2e/run-details.authed-admin.spec.ts`

**Steps:**
  1. Open drawer for a run with report and click report tab
    - expect: Report tab is visible
  2. Click report-fullscreen-button
    - expect: fullscreen-report-overlay should appear
    - expect: fullscreen-report-iframe should be present
  3. Click fullscreen-report-close or press Escape
    - expect: Fullscreen overlay should close

#### 5.11. No logs state shows placeholder message

**File:** `tests-e2e/run-details.authed-admin.spec.ts`

**Steps:**
  1. Open drawer for a run with no stdout or stderr
    - expect: Drawer is open with logs tab
  2. Check the terminal block
    - expect: 'No logs available' placeholder should be visible

### 6. Suite Management (Add Suite Modal)

**Seed:** `frontend/tests-e2e/seed.authed-admin.spec.ts`

#### 6.1. Add suite modal opens from sidebar button

**File:** `tests-e2e/suite-management.authed-admin.spec.ts`

**Steps:**
  1. Login as admin
    - expect: Dashboard is displayed
  2. Click open-add-suite-button in the sidebar header
    - expect: add-suite-modal should be visible
    - expect: Both suite-tab-local and suite-tab-git tabs should be visible

#### 6.2. Local tab shows path input and link button

**File:** `tests-e2e/suite-management.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open add-suite-modal
    - expect: Modal is visible
  2. Click suite-tab-local
    - expect: link-path-input should be visible
    - expect: link-submit button should be visible

#### 6.3. Git tab shows clone URL, ref, credential fields

**File:** `tests-e2e/suite-management.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open add-suite-modal
    - expect: Modal is visible
  2. Click suite-tab-git
    - expect: clone-url-input should be visible
    - expect: clone-ref-input should be visible
    - expect: clone-cred-select should be visible
    - expect: clone-submit button should be visible

#### 6.4. In-credential creation UI is present on Git tab

**File:** `tests-e2e/suite-management.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open add-suite-modal
    - expect: Modal is visible
  2. Click suite-tab-git
    - expect: Git tab is active
  3. Check for credential creation inputs
    - expect: clone-newcred-name input should be visible
    - expect: clone-newcred-secret input should be visible
    - expect: clone-newcred-save button should be visible

#### 6.5. Non-allowlisted URL is rejected without spawning git

**File:** `tests-e2e/suite-management.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open add-suite-modal
    - expect: Modal is visible
  2. In Git tab, enter 'http://insecure/repo.git' in clone-url-input
    - expect: URL is entered
  3. Click clone-submit
    - expect: clone-feedback banner should appear with error message

### 7. Schedule Management

**Seed:** `frontend/tests-e2e/seed.authed-admin.spec.ts`

#### 7.1. Schedule modal opens from profile row in sidebar

**File:** `tests-e2e/schedule-mgmt.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with at least one profile present
    - expect: Dashboard is displayed
  2. Click open-schedule-button on a profile row
    - expect: schedule-modal should be visible
    - expect: schedule-name-input should be visible
    - expect: schedule-cron-input should be visible
    - expect: schedule-timezone-select should be visible
    - expect: schedule-save-button should be visible

#### 7.2. Valid cron expression shows preview of next 5 fire times

**File:** `tests-e2e/schedule-mgmt.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open schedule modal
    - expect: Schedule modal is visible
  2. Enter '0 2 * * *' in schedule-cron-input
  3. Wait for debounced preview
    - expect: schedule-preview should appear
    - expect: Preview should contain 5 list items (upcoming fire times)

#### 7.3. Invalid cron expression shows error

**File:** `tests-e2e/schedule-mgmt.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open schedule modal
    - expect: Schedule modal is visible
  2. Enter 'invalid cron' in schedule-cron-input
  3. Wait for debounced validation
    - expect: schedule-preview-error should appear with error message

#### 7.4. Save schedule creates a new schedule and closes modal

**File:** `tests-e2e/schedule-mgmt.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open schedule modal
    - expect: Schedule modal is visible
  2. Fill schedule-name-input and schedule-cron-input
    - expect: Fields are filled
  3. Click schedule-save-button
    - expect: Schedule should be saved
    - expect: schedule-modal should close

#### 7.5. Delete existing schedule removes it

**File:** `tests-e2e/schedule-mgmt.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with an existing schedule
    - expect: Dashboard is displayed
  2. Open schedule modal for the scheduled profile
    - expect: Schedule modal is visible with existing schedule data
  3. Click schedule-delete-button
    - expect: Schedule should be deleted
    - expect: schedule-modal should close

#### 7.6. Trigger schedule immediately creates a run

**File:** `tests-e2e/schedule-mgmt.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with an existing schedule
    - expect: Dashboard is displayed
  2. Accept the dialog confirmation
  3. Open schedule modal and click schedule-trigger-button
    - expect: Run should be triggered
    - expect: schedule-modal should close

#### 7.7. Timezone selector changes preview timestamps

**File:** `tests-e2e/schedule-mgmt.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open schedule modal
    - expect: Schedule modal is visible
  2. Enter '0 2 * * *' and wait for preview
    - expect: schedule-preview is visible
  3. Change schedule-timezone-select to 'Asia/Shanghai'
    - expect: Preview timestamps should update to reflect the new timezone

### 8. User Management (Admin Only)

**Seed:** `frontend/tests-e2e/seed.authed-admin.spec.ts`

#### 8.1. User management modal opens from header

**File:** `tests-e2e/user-mgmt.authed-admin.spec.ts`

**Steps:**
  1. Login as admin
    - expect: Dashboard is displayed
  2. Click open-users-button in the header
    - expect: user-modal should be visible
    - expect: User list table should show existing users including 'admin'
    - expect: user-new-username and user-new-password inputs should be visible
    - expect: user-add-submit button should be visible
    - expect: user-modal-close icon should be visible

#### 8.2. Non-admin user cannot see the users button

**File:** `tests-e2e/user-mgmt.authed-admin.spec.ts`

**Steps:**
  1. Login as a non-admin user
    - expect: Dashboard is displayed
  2. Check the header
    - expect: open-users-button should NOT be visible

#### 8.3. Create a new user

**File:** `tests-e2e/user-mgmt.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open user modal
    - expect: User modal is visible
  2. Enter a unique username in user-new-username
  3. Enter a password in user-new-password
    - expect: Fields are filled
  4. Click user-add-submit
    - expect: New user should appear in the user list table
    - expect: user-row-username should contain the new username

#### 8.4. Close user management modal

**File:** `tests-e2e/user-mgmt.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open user modal
    - expect: User modal is visible
  2. Click user-modal-close icon
    - expect: user-modal should not be visible

#### 8.5. User list shows users with row actions

**File:** `tests-e2e/user-mgmt.authed-admin.spec.ts`

**Steps:**
  1. Login as admin and open user modal
    - expect: User modal is visible
  2. Check the user table rows
    - expect: Each row should show username
    - expect: Actions should include: user-change-password, user-toggle-role, user-delete buttons

### 9. Sidebar Suite Navigation

**Seed:** `frontend/tests-e2e/seed.authed-admin.spec.ts`

#### 9.1. Clicking a suite in sidebar filters the runs table

**File:** `tests-e2e/sidebar.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with mocked suites and runs
    - expect: Dashboard is displayed
  2. Click on a suite name in the sidebar
    - expect: The suite item should become visually active (highlighted)
    - expect: The runs table should filter to show only runs matching that suite
  3. Click 'All Suites' to remove the filter
    - expect: All runs should be displayed again
    - expect: 'All Suites' should become the active item

#### 9.2. Suite update and prepare buttons visible for git suites

**File:** `tests-e2e/sidebar.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with a git-sourced suite
    - expect: Dashboard is displayed
  2. Hover over a git suite in the sidebar
    - expect: suite-update-{suite} button should be visible
    - expect: suite-prepare-{suite} button should be visible
  3. Hover over a local suite
    - expect: suite-update-{suite} button should NOT be visible
    - expect: suite-prepare-{suite} button should NOT be visible

#### 9.3. Profile row shows pass rate and history dots

**File:** `tests-e2e/sidebar.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with profiles and runs
    - expect: Dashboard is displayed
  2. Expand/observe a suite that has profiles
    - expect: Each profile should show its runner tag (pytest/playwright)
    - expect: If runs exist: pass rate percentage tag should be visible
    - expect: If runs exist: history dots (5 dots) should show run outcomes
    - expect: If no runs: 'No runs' tag should be visible

#### 9.4. Profile instant run button triggers a run

**File:** `tests-e2e/sidebar.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with a profile that has runs
    - expect: Dashboard is displayed
  2. Click the instant-run (play) button on a profile row
    - expect: A run should be triggered
    - expect: After trigger: the runs table should refresh

#### 9.5. Delete profile removes it from sidebar

**File:** `tests-e2e/sidebar.authed-admin.spec.ts`

**Steps:**
  1. Login as admin with a profile present
    - expect: Dashboard is displayed
  2. Click the delete button on a profile row
    - expect: The profile should be removed from the sidebar
    - expect: The profile should no longer appear in the trigger modal
