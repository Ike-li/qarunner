// Shared domain types for the qarunner frontend. Mirrors the API response
// shapes (see src/qarunner/api/schemas.py) plus the scanned test tree.

export interface TestSummary {
  total: number
  passed: number
  failed: number
  skipped: number
  error: number
  duration_ms: number
  pass_rate: number
}

export interface ReportRef {
  allure_results_dir: string
  allure_report_file: string | null
  html_generated: boolean
}

export interface Run {
  id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'timeout' | 'cancelled'
  runner: string
  created_by: string
  tests_path: string
  args: string[]
  executor_mode: 'subprocess' | 'docker'
  summary: TestSummary | null
  report: ReportRef | null
  exit_code: number | null
  error: string | null
  passed: boolean | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  stdout?: string | null
  stderr?: string | null
  locked?: boolean
}

export interface TestCaseResult {
  suite: string
  name: string
  status: 'passed' | 'failed' | 'skipped' | 'error'
  duration_ms: number
  message: string | null
}

export interface RunDiffBaselineInfo {
  id: string
  created_at: string
  status: Run['status']
}

export interface RunDiff {
  baseline: RunDiffBaselineInfo | null
  diff: {
    new_failures: TestCaseResult[]
    fixed: TestCaseResult[]
    still_failing: TestCaseResult[]
    new_cases: TestCaseResult[]
    removed_cases: TestCaseResult[]
  }
}

export interface UserProfile {
  username: string
  role: 'admin' | 'user'
  created_at: string
}

export interface Credential {
  id: string
  name: string
  type: string
  created_by: string
  created_at: string
}

export interface Profile {
  id: string
  name: string
  description: string | null
  tests_path: string
  runner: string
  selected_files: string[]
  selected_markers: string[]
  extra_args: string
  executor_mode: 'subprocess' | 'docker'
  timeout: number | null
  created_by: string
  created_at: string
  env: Record<string, string>
}

export interface Schedule {
  id: string
  name: string
  profile_id: string
  cron_expression: string
  enabled: boolean
  timezone: string
  last_run_at: string | null
  next_run_at: string | null
  created_by: string
  created_at: string
}

export interface TreeNode {
  name: string
  path: string
  is_dir: boolean
  children?: TreeNode[]
}

export interface SuiteInfo {
  name: string
  source: string
  repo_url: string | null
  ref: string | null
}
