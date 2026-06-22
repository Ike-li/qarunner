import { useEffect, useState, useCallback, useRef } from 'react'
import { 
  Play, 
  RotateCw, 
  CheckCircle2, 
  XCircle, 
  Clock, 
  Calendar,
  AlertTriangle, 
  ExternalLink, 
  ChevronRight, 
  FolderGit2, 
  SlidersHorizontal, 
  Activity, 
  X, 
  Copy, 
  Check,
  Download,
  BarChart3,
  Sparkles,
  Users,
  LogOut,
  Lock,
  Unlock,
  Plus,
  Cpu,
  Box,
  Terminal,
  Sun,
  Moon,
  Pencil,
  Trash2,
  Maximize2,
  ChevronsLeft,
  ChevronsRight,
  Search,
  ZoomIn,
  ZoomOut,
  ChevronUp,
  ChevronDown
} from 'lucide-react'
import styles from './App.module.css'

interface TestSummary {
  total: number
  passed: number
  failed: number
  skipped: number
  error: number
  duration_ms: number
  pass_rate: number
}

interface ReportRef {
  allure_results_dir: string
  allure_report_file: string | null
  html_generated: boolean
}

interface Run {
  id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'timeout'
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


interface UserProfile {
  username: string
  role: 'admin' | 'user'
  created_at: string
}

const translations = {
  en: {
    platformTitle: "qarunner",
    platformSubtitle: "QA TEST ORCHESTRATION",
    platformSubtitleFull: "QA TEST ORCHESTRATION PLATFORM",
    
    // Auth & Login
    username: "Username",
    usernamePlaceholder: "Enter username",
    password: "Password",
    passwordPlaceholder: "Enter password",
    authenticating: "Authenticating...",
    signIn: "Sign In",
    loginErrorPlaceholder: "Please enter both username and password.",
    
    // Header Actions
    users: "Users",
    triggerRun: "Trigger Run",
    signOut: "Sign Out",
    managePlatformUsers: "Manage Platform Users",
    
    // Stats Summary Cards
    totalExecutions: "Total Executions",
    successRate: "Success Rate",
    failedRuns: "Failed Runs",
    activeQueue: "Active Queue",
    
    // Main Table Section
    executionRecords: "Execution Records",
    refreshLogs: "Refresh Execution Logs",
    loadingHistory: "Loading execution history...",
    noRunsTitle: "No runs recorded yet",
    noRunsDesc: "Trigger your first automated pytest run to see full statistics and Allure HTML report analysis.",
    launchFirstRun: "Launch First Run",
    
    // Table Columns
    runId: "RUN ID",
    targetSuite: "TARGET SUITE",
    status: "STATUS",
    engine: "ENGINE",
    owner: "OWNER",
    results: "RESULTS",
    passRate: "PASS RATE",
    duration: "DURATION",
    createdAt: "CREATED AT",
    
    // Status badges
    status_queued: "queued",
    status_running: "running",
    status_completed: "completed",
    status_failed: "failed",
    status_timeout: "timeout",
    
    // Engine badges
    engine_docker: "Docker",
    engine_subprocess: "Local",
    
    // Run Details Drawer
    executionDetails: "Execution Details",
    id: "ID",
    allTestsPassed: "ALL TESTS PASSED",
    suiteFailed: "SUITE FAILED",
    runner: "Runner",
    exitCode: "Exit Code",
    triggeredBy: "Triggered By",
    environment: "Environment",
    created: "Created",
    finished: "Finished",
    consoleLogs: "Console Logs",
    testReport: "Test Report",
    streaming: "Streaming",
    copied: "Copied",
    copy: "Copy",
    download: "Download",
    waitingLogs: "Waiting for test execution logs...",
    noLogsAvailable: "No console logs available for this execution.",
    pytestArguments: "Pytest Arguments",
    executionStacktrace: "Execution Stacktrace",
    testOutcomes: "Test Outcomes",
    passed: "Passed",
    failed: "Failed",
    error: "Error",
    skipped: "Skipped",
    durationLabel: "Duration",
    execInProgress: "Execution is in progress...",
    execInProgressDesc: "The outcomes summary will populate once the test suite finishes running.",
    openAllureReport: "Open Allure HTML Report",
    allureReportDesc: "Allure compiled report served as a single-page self-contained interactive web page.",
    generatingAllureReport: "Generating Allure Report...",
    generatingAllureReportDesc: "Once execution completes, the interactive report will be built.",
    noReportGenerated: "No report generated",
    noReportGeneratedDesc: "This execution did not produce Allure metrics or encountered an execution failure.",
    
    // Trigger Modal
    triggerTitle: "Trigger Automated Pytest Run",
    targetDirectory: "Target Test Directory",
    scanningDirectory: "Scanning tests_root directory subfolders...",
    directoryHelp: "Relative subfolder scanning of the configured QARUNNER_TESTS_ROOT.",
    executionEnvironment: "Execution Environment",
    localSubprocessTitle: "Local Subprocess",
    localSubprocessDesc: "Host speed runner",
    dockerContainerTitle: "Docker Container",
    dockerContainerDesc: "Isolated environmental sandbox",
    envHelp: "Choose whether to execute tests directly on the host or inside an isolated container.",
    pytestArgsLabel: "Pytest Arguments",
    pytestArgsPlaceholder: "e.g. -v -k test_api --tb=short",
    pytestArgsHelp: "Whitespace separated flags to append to the pytest command execution.",
    timeoutLabel: "Execution Timeout (seconds)",
    timeoutPlaceholder: "default: 1800",
    timeoutHelp: "Task will abort with a timeout status if exceeded.",
    allureReportsLabel: "Allure Reports",
    cancel: "Cancel",
    schedulingTask: "Scheduling Task...",
    launchRun: "Launch Run",
    
    // User Management Modal
    userManagementTitle: "User Management Panel",
    platformDirectory: "Platform Directory",
    registerNewUser: "Register New User",
    systemAccessRole: "System Access Role",
    userStandardAccess: "User (Standard Access)",
    administratorFullControls: "Administrator (Full Controls)",
    registering: "Registering...",
    addUserAccount: "Add User Account",
    noUsersRegistered: "No users registered.",
    registeredAt: "Registered At",
    role: "Role",
    
    // Forms validation / errors
    enterBothFields: "Please enter both username and password.",
    enterBothFieldsNewUser: "Please fill in both username and password.",
    selectTestDir: "Please select a test directory.",
    failedCreateUser: "Failed to create user.",
    failedTriggerRun: "Failed to trigger run.",
    workspaceSuites: "Workspace Suites",
    quickTrigger: "Quick Trigger",
    clearFilter: "Clear Filter",
    noSuitesScanned: "No suites found",
    allSuites: "All Suites",
    testSuiteSelection: "Test Suite File Discovery",
    scannedMarkersTitle: "Pytest Marker Filter",
    saveAsProfile: "Save as Execution Profile",
    saveProfileDesc: "Save this exact configuration for single-click execution later in the launch modal or project sidebar.",
    profileNamePlaceholder: "Profile name (e.g. Daily API Regression)",
    profileDescPlaceholder: "Profile description (optional)",
    selectProfile: "Load Named Profile Template",
    customArgsTitle: "Extra Pytest Command Parameters",
    noProfilesConfigured: "No saved profiles for this suite"
  },
  zh: {
    platformTitle: "qarunner",
    platformSubtitle: "QA 测试编排系统",
    platformSubtitleFull: "QA 自动化测试编排平台",
    
    // Auth & Login
    username: "用户名",
    usernamePlaceholder: "请输入用户名",
    password: "密码",
    passwordPlaceholder: "请输入密码",
    authenticating: "身份验证中...",
    signIn: "登 录",
    loginErrorPlaceholder: "请输入用户名和密码。",
    
    // Header Actions
    users: "用户管理",
    triggerRun: "启动测试",
    signOut: "退出登录",
    managePlatformUsers: "平台用户管理",
    
    // Stats Summary Cards
    totalExecutions: "总执行次数",
    successRate: "整体成功率",
    failedRuns: "失败运行数",
    activeQueue: "活跃队列",
    
    // Main Table Section
    executionRecords: "测试执行记录",
    refreshLogs: "刷新执行日志",
    loadingHistory: "正在加载执行历史...",
    noRunsTitle: "暂无运行记录",
    noRunsDesc: "触发您的首次自动化 pytest 运行，以查看完整统计信息和 Allure HTML 报告分析。",
    launchFirstRun: "启动首次运行",
    
    // Table Columns
    runId: "运行 ID",
    targetSuite: "目标测试套件",
    status: "状态",
    engine: "执行引擎",
    owner: "执行人",
    results: "测试结果",
    passRate: "通过率",
    duration: "耗时",
    createdAt: "创建时间",
    
    // Status badges
    status_queued: "排队中",
    status_running: "运行中",
    status_completed: "已完成",
    status_failed: "执行失败",
    status_timeout: "执行超时",
    
    // Engine badges
    engine_docker: "Docker 隔离",
    engine_subprocess: "本地进程",
    
    // Run Details Drawer
    executionDetails: "执行详细信息",
    id: "ID",
    allTestsPassed: "全部测试通过",
    suiteFailed: "套件执行失败",
    runner: "运行器",
    exitCode: "退出码",
    triggeredBy: "触发用户",
    environment: "执行环境",
    created: "创建时间",
    finished: "结束时间",
    consoleLogs: "控制台日志",
    testReport: "测试报告",
    streaming: "实时传输中",
    copied: "已复制",
    copy: "复制",
    download: "下载",
    waitingLogs: "等待测试执行日志...",
    noLogsAvailable: "此运行没有可用的控制台日志。",
    pytestArguments: "Pytest 执行参数",
    executionStacktrace: "执行堆栈轨迹",
    testOutcomes: "测试结果指标",
    passed: "已通过",
    failed: "已失败",
    error: "异常",
    skipped: "已跳过",
    durationLabel: "总耗时",
    execInProgress: "测试执行中...",
    execInProgressDesc: "测试套件执行完成后，将自动加载指标结果。",
    openAllureReport: "打开 Allure 报告",
    allureReportDesc: "Allure 编译后的交互式测试报告，以独立单网页形式提供。",
    generatingAllureReport: "正在生成 Allure 报告...",
    generatingAllureReportDesc: "测试执行完成后，将自动编译交互式报告。",
    noReportGenerated: "未生成测试报告",
    noReportGeneratedDesc: "此运行未产生 Allure 指标，或遇到了执行中断/异常。",
    
    // Trigger Modal
    triggerTitle: "启动自动化 Pytest 运行",
    targetDirectory: "目标测试目录",
    scanningDirectory: "正在扫描 tests_root 目录的子文件夹...",
    directoryHelp: "自动扫描已配置的 QARUNNER_TESTS_ROOT 中的相对子目录。",
    executionEnvironment: "执行环境配置",
    localSubprocessTitle: "本地子进程",
    localSubprocessDesc: "宿主机高速运行模式",
    dockerContainerTitle: "Docker 容器",
    dockerContainerDesc: "安全隔离的环境沙箱",
    envHelp: "选择是直接在宿主机上执行测试，还是在隔离的容器沙箱中执行。",
    pytestArgsLabel: "Pytest 执行参数",
    pytestArgsPlaceholder: "例如 -v -k test_api --tb=short",
    pytestArgsHelp: "传递给 pytest 命令执行的空格分隔的附加参数和标记。",
    timeoutLabel: "运行超时限制 (秒)",
    timeoutPlaceholder: "默认：1800 秒",
    timeoutHelp: "如果执行超出此限制，任务将被强制中止并标记为超时。",
    allureReportsLabel: "Allure 报告生成",
    cancel: "取 消",
    schedulingTask: "正在调度任务...",
    launchRun: "启动运行",
    
    // User Management Modal
    userManagementTitle: "用户管理面板",
    platformDirectory: "平台用户名录",
    registerNewUser: "注册新用户",
    systemAccessRole: "系统访问权限",
    userStandardAccess: "标准用户 (只读/触发权限)",
    administratorFullControls: "系统管理员 (完整管理权限)",
    registering: "正在注册中...",
    addUserAccount: "添加用户账户",
    noUsersRegistered: "暂无注册用户。",
    registeredAt: "注册时间",
    role: "系统角色",
    
    // Forms validation / errors
    enterBothFields: "请输入用户名和密码。",
    enterBothFieldsNewUser: "请填写用户名和密码。",
    selectTestDir: "请选择一个测试目录。",
    failedCreateUser: "创建用户失败。",
    failedTriggerRun: "启动运行失败。",
    workspaceSuites: "项目测试套件",
    quickTrigger: "快速运行",
    clearFilter: "清除筛选",
    noSuitesScanned: "未扫描到套件",
    allSuites: "全部套件",
    testSuiteSelection: "测试用例文件选择",
    scannedMarkersTitle: "Pytest 标签(Marker)筛选",
    saveAsProfile: "保存为执行方案模板",
    saveProfileDesc: "保存当前的选择与参数，后续可在启动框或首页左侧列表下一键直接触发运行。",
    profileNamePlaceholder: "方案名称，例如：每日接口回归",
    profileDescPlaceholder: "方案描述 (可选)",
    selectProfile: "加载预设执行方案",
    customArgsTitle: "附加 Pytest 命令行参数",
    noProfilesConfigured: "该套件暂无配置方案"
  }
} as const;


export default function App() {
  const [runs, setRuns] = useState<Run[]>([])
  const [tests, setTests] = useState<string[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedRunDetails, setSelectedRunDetails] = useState<Run | null>(null)
  const [detailsLoading, setDetailsLoading] = useState<boolean>(false)
  const [drawerTab, setDrawerTab] = useState<'logs' | 'report'>('logs')
  const terminalRef = useRef<HTMLDivElement>(null)
  const [isTriggerModalOpen, setIsTriggerModalOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [copySuccess, setCopySuccess] = useState(false)
  const [logLevelFilter, setLogLevelFilter] = useState<'ALL' | 'ERROR' | 'WARNING' | 'SUCCESS'>('ALL')

  // Localization & Theme states
  const [lang, setLang] = useState<'en' | 'zh'>(() => (localStorage.getItem('qarunner_lang') as 'en' | 'zh') || 'en')
  const [theme, setTheme] = useState<'dark' | 'light'>(() => (localStorage.getItem('qarunner_theme') as 'dark' | 'light') || 'dark')

  const t = useCallback((key: keyof typeof translations['en']) => {
    return translations[lang][key] || key
  }, [lang])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('qarunner_theme', theme)
  }, [theme])

  useEffect(() => {
    localStorage.setItem('qarunner_lang', lang)
  }, [lang])


  // Auth State
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('qarunner_token'))
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null)

  // Login Form State
  const [loginUsername, setLoginUsername] = useState('')
  const [loginPassword, setLoginPassword] = useState('')
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loginLoading, setLoginLoading] = useState(false)

  // Admin User Manager Modal State
  const [isUserModalOpen, setIsUserModalOpen] = useState(false)
  const [usersList, setUsersList] = useState<UserProfile[]>([])
  const [usersLoading, setUsersLoading] = useState(false)
  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newUserRole, setNewUserRole] = useState<'admin' | 'user'>('user')
  const [newUserError, setNewUserError] = useState<string | null>(null)
  const [newUserLoading, setNewUserLoading] = useState(false)

  // Trigger form state
  const [testsPath, setTestsPath] = useState('')
  const [customArgs, setCustomArgs] = useState('')
  const [allureEnabled, setAllureEnabled] = useState(true)
  const [timeoutSeconds, setTimeoutSeconds] = useState<number | ''>('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [executorMode, setExecutorMode] = useState<'subprocess' | 'docker'>('subprocess')
  const [selectedSuiteFilter, setSelectedSuiteFilter] = useState<string | null>(null)

  // Visual test suite & profile states
  const [profiles, setProfiles] = useState<any[]>([])
  const [scannedFilesTree, setScannedFilesTree] = useState<any[]>([])
  const [scannedMarkers, setScannedMarkers] = useState<string[]>([])
  const [selectedFiles, setSelectedFiles] = useState<string[]>([])
  const [selectedMarkers, setSelectedMarkers] = useState<string[]>([])
  const [profileName, setProfileName] = useState('')
  const [profileDesc, setProfileDesc] = useState('')
  const [isSavingProfile, setIsSavingProfile] = useState(false)
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null)
  const [selectedProfileId, setSelectedProfileId] = useState<string>('')
  const [expandedFolders, setExpandedFolders] = useState<string[]>([])
  
  // Custom Environment Variables & Retention States
  const [envVars, setEnvVars] = useState<{ key: string; value: string }[]>([])
  const [retentionDays, setRetentionDays] = useState<number>(30)
  const [isCleaningStorage, setIsCleaningStorage] = useState<boolean>(false)
  const [streamedStdout, setStreamedStdout] = useState<string>('')
  const [isStreaming, setIsStreaming] = useState<boolean>(false)

  // New Log & Drawer controls to resolve "logs are too small" issue
  const [isDrawerExpanded, setIsDrawerExpanded] = useState<boolean>(false)
  const [isTerminalHeightExpanded, setIsTerminalHeightExpanded] = useState<boolean>(false)
  const [terminalFontSize, setTerminalFontSize] = useState<number>(13)
  const [logSearchQuery, setLogSearchQuery] = useState<string>('')

  // Fullscreen terminal features (Word Wrap, Auto Scroll lock, and element Ref)
  const [isTerminalFullscreen, setIsTerminalFullscreen] = useState<boolean>(false)
  const [isWordWrapEnabled, setIsWordWrapEnabled] = useState<boolean>(true)
  const [isAutoScrollEnabled, setIsAutoScrollEnabled] = useState<boolean>(true)
  const fullscreenTerminalRef = useRef<HTMLDivElement>(null)
  const [isIframeLoading, setIsIframeLoading] = useState<boolean>(true)

  // Reset profile editing states when modal is closed
  useEffect(() => {
    if (!isTriggerModalOpen) {
      setEditingProfileId(null)
      setIsSavingProfile(false)
      setProfileName('')
      setProfileDesc('')
      setEnvVars([])
    }
  }, [isTriggerModalOpen])

  // Schedules Subsystem State
  const [schedules, setSchedules] = useState<any[]>([])
  const [isScheduleModalOpen, setIsScheduleModalOpen] = useState(false)
  const [scheduleProfile, setScheduleProfile] = useState<any | null>(null)
  const [schedName, setSchedName] = useState('')
  const [schedExpression, setSchedExpression] = useState('')
  const [schedTimezone, setSchedTimezone] = useState('UTC')
  const [schedEnabled, setSchedEnabled] = useState(true)
  const [previewNextRuns, setPreviewNextRuns] = useState<string[]>([])
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [logFilterTab, setLogFilterTab] = useState<'All' | 'Manual' | 'Scheduled'>('All')

  // Logout handler
  const handleLogout = useCallback(() => {
    localStorage.removeItem('qarunner_token')
    document.cookie = "token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Strict"
    setToken(null)
    setCurrentUser(null)
    setSelectedRunId(null)
    setRuns([])
    setTests([])
    setProfiles([])
    setScannedFilesTree([])
    setScannedMarkers([])
    setSelectedFiles([])
    setSelectedMarkers([])
    setProfileName('')
    setProfileDesc('')
    setIsSavingProfile(false)
    setSelectedProfileId('')
    setExpandedFolders([])
    setSchedules([])
  }, [])

  // Fetch current user profile
  const fetchProfile = useCallback(async (authToken: string) => {
    try {
      const resp = await fetch('/auth/me', {
        headers: { 'Authorization': `Bearer ${authToken}` }
      })
      if (resp.status === 401) {
        handleLogout()
        return
      }
      if (resp.ok) {
        const user = await resp.json()
        setCurrentUser(user)
      } else {
        handleLogout()
      }
    } catch (err) {
      console.error('Error fetching profile:', err)
    }
  }, [handleLogout])

  // Fetch runs list
  const fetchRuns = useCallback(async () => {
    if (!token) return
    try {
      const resp = await fetch('/runs', {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (resp.status === 401) {
        handleLogout()
        return
      }
      if (resp.ok) {
        const data = await resp.json()
        setRuns(data.runs)
      }
    } catch (err) {
      console.error('Error fetching runs:', err)
    } finally {
      setLoading(false)
    }
  }, [token, handleLogout])

  // Fetch a single run details (with stdout/stderr)
  const fetchSelectedRunDetails = useCallback(async (runId: string) => {
    if (!token) return
    setDetailsLoading(true)
    try {
      const resp = await fetch(`/runs/${runId}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (resp.status === 401) {
        handleLogout()
        return
      }
      if (resp.ok) {
        const data = await resp.json()
        setSelectedRunDetails(data)
      }
    } catch (err) {
      console.error('Error fetching run details:', err)
    } finally {
      setDetailsLoading(false)
    }
  }, [token, handleLogout])  // Fetch saved test profiles
  const fetchProfiles = useCallback(async () => {
    if (!token) return
    try {
      const resp = await fetch('/profiles', {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (resp.status === 401) {
        handleLogout()
        return
      }
      if (resp.ok) {
        const data = await resp.json()
        setProfiles(data)
      }
    } catch (err) {
      console.error('Error fetching profiles:', err)
    }
  }, [token, handleLogout])

  // Fetch saved test schedules
  const fetchSchedules = useCallback(async () => {
    if (!token) return
    try {
      const resp = await fetch('/schedules', {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (resp.status === 401) {
        handleLogout()
        return
      }
      if (resp.ok) {
        const data = await resp.json()
        setSchedules(data)
      }
    } catch (err) {
      console.error('Error fetching schedules:', err)
    }
  }, [token, handleLogout])

  // Fetch schedule preview next 5 runs
  const fetchSchedulePreview = useCallback(async (expression: string, timezone: string) => {
    if (!token || !expression) {
      setPreviewNextRuns([])
      setPreviewError(null)
      return
    }
    try {
      const resp = await fetch(`/schedules/preview?expression=${encodeURIComponent(expression)}&timezone=${encodeURIComponent(timezone)}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (resp.ok) {
        const data = await resp.json()
        setPreviewNextRuns(data.next_runs)
        setPreviewError(null)
      } else {
        const errData = await resp.json()
        setPreviewError(errData.detail || 'Invalid cron expression')
        setPreviewNextRuns([])
      }
    } catch (err) {
      setPreviewError('Failed to fetch schedule preview')
      setPreviewNextRuns([])
    }
  }, [token])

  // Open schedule manager modal and populate fields
  const handleOpenScheduleModal = useCallback((profile: any) => {
    setScheduleProfile(profile)
    // Find if there is an existing schedule for this profile
    const existing = schedules.find(s => s.profile_id === profile.id)
    if (existing) {
      // Editing existing schedule
      setSchedName(existing.name)
      setSchedExpression(existing.cron_expression)
      setSchedTimezone(existing.timezone || 'UTC')
      setSchedEnabled(existing.enabled)
      fetchSchedulePreview(existing.cron_expression, existing.timezone || 'UTC')
    } else {
      // Creating new schedule
      setSchedName(`${profile.name} Schedule`)
      setSchedExpression('0 2 * * *') // Default daily at 2am
      setSchedTimezone('UTC')
      setSchedEnabled(true)
      fetchSchedulePreview('0 2 * * *', 'UTC')
    }
    setPreviewError(null)
    setIsScheduleModalOpen(true)
  }, [schedules, fetchSchedulePreview])

  // Save schedule configuration
  const handleSaveSchedule = useCallback(async () => {
    if (!token || !scheduleProfile) return
    const existing = schedules.find(s => s.profile_id === scheduleProfile.id)
    const payload = {
      name: schedName,
      profile_id: scheduleProfile.id,
      cron_expression: schedExpression,
      enabled: schedEnabled,
      timezone: schedTimezone
    }

    try {
      const url = existing ? `/schedules/${existing.id}` : '/schedules'
      const method = existing ? 'PUT' : 'POST'
      const resp = await fetch(url, {
        method,
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(payload)
      })

      if (resp.status === 401) {
        handleLogout()
        return
      }

      if (resp.ok) {
        await fetchSchedules()
        setIsScheduleModalOpen(false)
      } else {
        const errData = await resp.json()
        setPreviewError(errData.detail || 'Failed to save schedule')
      }
    } catch (err) {
      setPreviewError('Network error. Failed to save schedule.')
    }
  }, [token, scheduleProfile, schedules, schedName, schedExpression, schedEnabled, schedTimezone, handleLogout, fetchSchedules])

  // Delete schedule configuration
  const handleDeleteSchedule = useCallback(async (scheduleId: string) => {
    if (!token) return
    if (!window.confirm(lang === 'zh' ? '确定要删除此定时调度吗？' : 'Are you sure you want to delete this schedule?')) {
      return
    }
    try {
      const resp = await fetch(`/schedules/${scheduleId}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (resp.status === 401) {
        handleLogout()
        return
      }
      if (resp.ok) {
        await fetchSchedules()
        // If deleting the active profile's schedule being configured, update states
        if (scheduleProfile) {
          const existing = schedules.find(s => s.profile_id === scheduleProfile.id)
          if (existing && existing.id === scheduleId) {
            setSchedName('')
            setSchedExpression('')
            setPreviewNextRuns([])
          }
        }
      }
    } catch (err) {
      console.error('Error deleting schedule:', err)
    }
  }, [token, lang, scheduleProfile, schedules, handleLogout, fetchSchedules])

  // Fetch file tree and markers for the active test suite
  const fetchSuiteMetadata = useCallback(async (suiteName: string) => {
    if (!token || !suiteName) return
    try {
      // 1. Fetch File Tree
      const treeResp = await fetch(`/tests/${encodeURIComponent(suiteName)}/tree`, {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (treeResp.status === 401) {
        handleLogout()
        return
      }
      if (treeResp.ok) {
        const treeData = await treeResp.json()
        setScannedFilesTree(treeData)
      } else {
        setScannedFilesTree([])
      }

      // 2. Fetch Markers statically
      const markersResp = await fetch(`/tests/${encodeURIComponent(suiteName)}/markers`, {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (markersResp.ok) {
        const markersData = await markersResp.json()
        setScannedMarkers(markersData)
      } else {
        setScannedMarkers([])
      }
    } catch (err) {
      console.error('Error fetching suite metadata:', err)
      setScannedFilesTree([])
      setScannedMarkers([])
    }
  }, [token, handleLogout])

  // Handle direct single-click trigger of a profile from the sidebar
  const handleTriggerProfile = useCallback(async (profile: any) => {
    if (!token) return
    const payload = {
      tests_path: profile.tests_path,
      runner: 'pytest',
      args: [],
      allure: true,
      timeout: profile.timeout,
      executor_mode: profile.executor_mode,
      selected_files: profile.selected_files,
      selected_markers: profile.selected_markers,
      extra_args: profile.extra_args,
      env: profile.env || {}
    }

    try {
      const resp = await fetch('/runs', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(payload)
      })

      if (resp.status === 401) {
        handleLogout()
        return
      }

      if (resp.ok) {
        const newRun = await resp.json()
        await fetchRuns()
        setSelectedRunId(newRun.id)
      } else {
        const errorData = await resp.json()
        alert(errorData.detail || 'Failed to trigger run for profile.')
      }
    } catch (err) {
      console.error('Network error. Failed to trigger run for profile.', err)
    }
  }, [token, handleLogout, fetchRuns])

  // Handle deleting a saved execution profile
  const handleDeleteProfile = useCallback(async (profileId: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation()
    const confirmMsg = lang === 'zh' ? '确定要删除此执行方案吗？' : 'Are you sure you want to delete this profile?'
    if (!window.confirm(confirmMsg)) return

    try {
      const resp = await fetch(`/profiles/${profileId}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${token}` }
      })

      if (resp.status === 401) {
        handleLogout()
        return
      }

      if (resp.ok) {
        await fetchProfiles()
        await fetchSchedules()
        if (selectedProfileId === profileId) {
          setSelectedProfileId('')
        }
      } else {
        const errorData = await resp.json()
        alert(errorData.detail || 'Failed to delete profile.')
      }
    } catch (err) {
      console.error('Error deleting profile:', err)
    }
  }, [token, handleLogout, lang, fetchProfiles, fetchSchedules, selectedProfileId])


  // Fetch tests directories
  const fetchTests = useCallback(async () => {
    if (!token) return
    try {
      const resp = await fetch('/tests', {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (resp.status === 401) {
        handleLogout()
        return
      }
      if (resp.ok) {
        const data = await resp.json()
        setTests(data)
        if (data.length > 0) {
          setTestsPath(data[0]) // default to first subdirectory
        }
      }
    } catch (err) {
      console.error('Error fetching test directories:', err)
    }
  }, [token, handleLogout])

  // Admin: Fetch all registered users
  const fetchUsers = useCallback(async () => {
    if (!token || !currentUser || currentUser.role !== 'admin') return
    setUsersLoading(true)
    try {
      const resp = await fetch('/users', {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (resp.status === 401) {
        handleLogout()
        return
      }
      if (resp.ok) {
        const data = await resp.json()
        setUsersList(data.users)
      }
    } catch (err) {
      console.error('Error fetching users:', err)
    } finally {
      setUsersLoading(false)
    }
  }, [token, currentUser, handleLogout])

  // Handle Login submission
  const handleLoginSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!loginUsername.trim() || !loginPassword.trim()) {
      setLoginError('Please enter both username and password.')
      return
    }

    setLoginLoading(true)
    setLoginError(null)

    try {
      const resp = await fetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: loginUsername.trim(),
          password: loginPassword
        })
      })

      if (resp.ok) {
        const data = await resp.json()
        localStorage.setItem('qarunner_token', data.access_token)
        document.cookie = `token=${data.access_token}; path=/; max-age=86400; SameSite=Strict`
        setToken(data.access_token)
        setLoginUsername('')
        setLoginPassword('')
      } else {
        const errorData = await resp.json()
        setLoginError(errorData.detail || 'Invalid username or password.')
      }
    } catch (err) {
      setLoginError('Network error. Failed to connect to authentication server.')
    } finally {
      setLoginLoading(false)
    }
  }

  // Handle Admin creating a new user
  const handleCreateUserSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newUsername.trim() || !newPassword.trim()) {
      setNewUserError('Please fill in both username and password.')
      return
    }

    setNewUserLoading(true)
    setNewUserError(null)

    try {
      const resp = await fetch('/users', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          username: newUsername.trim(),
          password: newPassword,
          role: newUserRole
        })
      })

      if (resp.status === 401) {
        handleLogout()
        return
      }

      if (resp.ok) {
        // Reset registration form & reload list
        setNewUsername('')
        setNewPassword('')
        setNewUserRole('user')
        await fetchUsers()
      } else {
        const errorData = await resp.json()
        setNewUserError(errorData.detail || 'Failed to create user.')
      }
    } catch (err) {
      setNewUserError('Network error. Failed to connect to server.')
    } finally {
      setNewUserLoading(false)
    }
  }

  // Handle Trigger new test run
  const handleTriggerRun = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!testsPath) {
      setFormError('Please select a test directory.')
      return
    }

    setIsSubmitting(true)
    setFormError(null)

    const envPayload = envVars.reduce((acc, curr) => {
      const k = curr.key.trim()
      if (k) acc[k] = curr.value
      return acc
    }, {} as Record<string, string>)

    // Clear args array to rely entirely on selective compilation via backend orchestrator
    const payload = {
      tests_path: testsPath,
      runner: 'pytest',
      args: [],
      allure: allureEnabled,
      timeout: timeoutSeconds === '' ? null : Number(timeoutSeconds),
      executor_mode: executorMode,
      selected_files: selectedFiles,
      selected_markers: selectedMarkers,
      extra_args: customArgs,
      env: envPayload
    }

    try {
      const resp = await fetch('/runs', {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(payload)
      })

      if (resp.status === 401) {
        handleLogout()
        return
      }

      if (resp.ok) {
        const newRun = await resp.json()
        setIsTriggerModalOpen(false)
        // Reset form
        setCustomArgs('')
        setAllureEnabled(true)
        setTimeoutSeconds('')
        setExecutorMode('subprocess')
        setSelectedFiles([])
        setSelectedMarkers([])
        setSelectedProfileId('')
        setProfileName('')
        setProfileDesc('')
        setIsSavingProfile(false)
        // Refresh & auto-select newly created run
        await fetchRuns()
        setSelectedRunId(newRun.id)
      } else {
        const errorData = await resp.json()
        setFormError(errorData.detail || 'Failed to trigger run.')
      }
    } catch (err) {
      setFormError('Network error. Failed to connect to server.')
    } finally {
      setIsSubmitting(false)
    }
  }

  // Handle saving current execution settings as a named profile
  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!profileName.trim()) {
      setFormError('Please enter a profile name.')
      return
    }

    const envPayload = envVars.reduce((acc, curr) => {
      const k = curr.key.trim()
      if (k) acc[k] = curr.value
      return acc
    }, {} as Record<string, string>)

    const payload = {
      name: profileName.trim(),
      description: profileDesc.trim() || null,
      tests_path: testsPath,
      selected_files: selectedFiles,
      selected_markers: selectedMarkers,
      extra_args: customArgs,
      executor_mode: executorMode,
      timeout: timeoutSeconds === '' ? null : Number(timeoutSeconds),
      env: envPayload
    }

    try {
      const resp = await fetch('/profiles', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(payload)
      })

      if (resp.status === 401) {
        handleLogout()
        return
      }

      if (resp.ok) {
        // Refresh profiles list and close the save panel
        await fetchProfiles()
        setProfileName('')
        setProfileDesc('')
        setIsSavingProfile(false)
      } else {
        const errorData = await resp.json()
        setFormError(errorData.detail || 'Failed to save execution profile.')
      }
    } catch (err) {
      setFormError('Network error. Failed to save execution profile.')
    }
  }

  // Handle locking and unlocking a run record
  const handleToggleLock = useCallback(async (runId: string, e: React.MouseEvent) => {
    e.stopPropagation() // Prevent selecting row when clicking lock
    if (!token) return
    const run = runs.find(r => r.id === runId)
    if (!run) return
    const newLocked = !run.locked

    try {
      const resp = await fetch(`/runs/${runId}/lock`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ locked: newLocked })
      })

      if (resp.status === 401) {
        handleLogout()
        return
      }

      if (resp.ok) {
        // Update local runs state
        setRuns(prev => prev.map(r => r.id === runId ? { ...r, locked: newLocked } : r))
        // If the selected run details are open, update them too
        if (selectedRunDetails && selectedRunDetails.id === runId) {
          setSelectedRunDetails(prev => prev ? { ...prev, locked: newLocked } : null)
        }
      } else {
        const errorData = await resp.json()
        alert(errorData.detail || 'Failed to toggle lock status.')
      }
    } catch (err) {
      console.error('Error toggling lock:', err)
    }
  }, [token, runs, selectedRunDetails, handleLogout])

  // Handle opening the launch modal in Edit Profile mode
  const handleOpenEditProfile = useCallback((profile: any) => {
    setEditingProfileId(profile.id)
    setTestsPath(profile.tests_path)
    setCustomArgs(profile.extra_args || '')
    setAllureEnabled(true)
    setTimeoutSeconds(profile.timeout === null ? '' : profile.timeout)
    setExecutorMode(profile.executor_mode || 'subprocess')
    setSelectedFiles(profile.selected_files || [])
    setSelectedMarkers(profile.selected_markers || [])
    setProfileName(profile.name || '')
    setProfileDesc(profile.description || '')
    setEnvVars(
      profile.env
        ? Object.entries(profile.env).map(([key, value]) => ({ key, value: String(value) }))
        : []
    )
    setIsSavingProfile(true)
    setFormError(null)
    setIsTriggerModalOpen(true)
  }, [])

  // Handle updating an existing profile via PUT
  const handleUpdateProfile = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!editingProfileId) return
    if (!profileName.trim()) {
      setFormError('Please enter a profile name.')
      return
    }

    const envPayload = envVars.reduce((acc, curr) => {
      const k = curr.key.trim()
      if (k) acc[k] = curr.value
      return acc
    }, {} as Record<string, string>)

    const payload = {
      name: profileName.trim(),
      description: profileDesc.trim() || null,
      tests_path: testsPath,
      selected_files: selectedFiles,
      selected_markers: selectedMarkers,
      extra_args: customArgs,
      executor_mode: executorMode,
      timeout: timeoutSeconds === '' ? null : Number(timeoutSeconds),
      env: envPayload
    }

    setIsSubmitting(true)
    try {
      const resp = await fetch(`/profiles/${editingProfileId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(payload)
      })

      if (resp.status === 401) {
        handleLogout()
        return
      }

      if (resp.ok) {
        await fetchProfiles()
        await fetchSchedules()
        setIsTriggerModalOpen(false)
        setEditingProfileId(null)
        setIsSavingProfile(false)
        setProfileName('')
        setProfileDesc('')
        setCustomArgs('')
        setTimeoutSeconds('')
        setExecutorMode('subprocess')
        setSelectedFiles([])
        setSelectedMarkers([])
      } else {
        const errorData = await resp.json()
        setFormError(errorData.detail || 'Failed to update profile.')
      }
    } catch (err) {
      setFormError('Network error. Failed to update profile.')
    } finally {
      setIsSubmitting(false)
    }
  }

  // Initial loads on auth state changes
  useEffect(() => {
    if (token) {
      document.cookie = `token=${token}; path=/; max-age=86400; SameSite=Strict`
      fetchProfile(token)
      fetchRuns()
      fetchTests()
      fetchProfiles()
      fetchSchedules()
    }
  }, [token, fetchProfile, fetchRuns, fetchTests, fetchProfiles, fetchSchedules])

  // Trigger test-suite file-tree and markers discovery when active directory changes
  useEffect(() => {
    if (testsPath) {
      fetchSuiteMetadata(testsPath)
      // Reset form selections & profile states on project directory swap
      setSelectedFiles([])
      setSelectedMarkers([])
      setSelectedProfileId('')
      setProfileName('')
      setProfileDesc('')
      setIsSavingProfile(false)
    }
  }, [testsPath, fetchSuiteMetadata])

  // Debounced effect to fetch real-time future projected runtimes
  useEffect(() => {
    if (schedExpression) {
      const delayDebounce = setTimeout(() => {
        fetchSchedulePreview(schedExpression, schedTimezone)
      }, 500)
      return () => clearTimeout(delayDebounce)
    }
  }, [schedExpression, schedTimezone, fetchSchedulePreview])

  // Get currently selected run details (prefer detailed view with stdout/stderr)
  const selectedRun = selectedRunDetails || runs.find(r => r.id === selectedRunId) || null

  // Fetch full details whenever a run is selected
  useEffect(() => {
    setLogLevelFilter('ALL')
    setStreamedStdout('') // Clear streamed logs when selecting a different run
    if (selectedRunId) {
      fetchSelectedRunDetails(selectedRunId)
    } else {
      setSelectedRunDetails(null)
    }
  }, [selectedRunId, fetchSelectedRunDetails])

  // When selected run transitions from active to inactive, fetch details once to get the completed logs
  const lastStatusRef = useRef<string | null>(null)
  useEffect(() => {
    if (!selectedRunId) {
      lastStatusRef.current = null
      return
    }
    // Listen to shallow list status first to break circular deadlock in case of interval starvation
    const shallowRun = runs.find(r => r.id === selectedRunId)
    const currentStatus = shallowRun?.status || selectedRunDetails?.status || null
    const wasActive = lastStatusRef.current === 'running' || lastStatusRef.current === 'queued'
    const isInactive = currentStatus && currentStatus !== 'running' && currentStatus !== 'queued'

    if (wasActive && isInactive) {
      fetchSelectedRunDetails(selectedRunId)
      fetchRuns()
    }
    lastStatusRef.current = currentStatus
  }, [selectedRunId, runs, selectedRunDetails?.status, fetchSelectedRunDetails, fetchRuns])

  // Connect to EventSource for SSE live log streaming when a run is active
  useEffect(() => {
    if (!token || !selectedRunId) {
      setStreamedStdout('')
      setIsStreaming(false)
      return
    }

    const run = runs.find(r => r.id === selectedRunId) || selectedRunDetails
    if (!run) return

    const isActive = run.status === 'running' || run.status === 'queued'
    if (!isActive) {
      // Don't clear streamedStdout immediately so we don't flash a blank screen
      // while we fetch the completed logs from the backend.
      setIsStreaming(false)
      return
    }

    setStreamedStdout('')
    setIsStreaming(true)

    const url = `/runs/${selectedRunId}/stream?token=${encodeURIComponent(token)}`
    const eventSource = new EventSource(url)

    eventSource.onmessage = (event) => {
      setStreamedStdout(prev => prev + event.data + '\n')
    }

    eventSource.onerror = (err) => {
      console.error('SSE connection error, closing stream:', err)
      fetchSelectedRunDetails(selectedRunId)
      eventSource.close()
      setIsStreaming(false)
    }

    return () => {
      eventSource.close()
      setIsStreaming(false)
    }
  }, [token, selectedRunId, runs, selectedRunDetails, fetchSelectedRunDetails])

  // Scroll terminal logs to bottom on changes
  useEffect(() => {
    if (isAutoScrollEnabled) {
      if (terminalRef.current) {
        terminalRef.current.scrollTop = terminalRef.current.scrollHeight
      }
      if (fullscreenTerminalRef.current) {
        fullscreenTerminalRef.current.scrollTop = fullscreenTerminalRef.current.scrollHeight
      }
    }
  }, [selectedRun?.stdout, selectedRun?.stderr, streamedStdout, isStreaming, drawerTab, logSearchQuery, isAutoScrollEnabled, isTerminalFullscreen])

  // Reset drawer tab, logs search query, and fullscreen status when selected run changes
  useEffect(() => {
    if (selectedRunId) {
      setDrawerTab('logs')
      setLogSearchQuery('')
      setIsTerminalFullscreen(false)
      setIsIframeLoading(true)
    }
  }, [selectedRunId])

  // Reset iframe loading state when drawer tab changes to ensure smooth loading transitions
  useEffect(() => {
    setIsIframeLoading(true)
  }, [drawerTab])

  // Create refs to capture latest state values for starvation-free polling
  const runsRef = useRef(runs)
  const selectedRunIdRef = useRef(selectedRunId)
  const selectedRunDetailsRef = useRef(selectedRunDetails)

  // Keep refs synchronized with the latest state
  useEffect(() => {
    runsRef.current = runs
  }, [runs])

  useEffect(() => {
    selectedRunIdRef.current = selectedRunId
  }, [selectedRunId])

  useEffect(() => {
    selectedRunDetailsRef.current = selectedRunDetails
  }, [selectedRunDetails])

  // Automated real-time polling
  useEffect(() => {
    if (!token) return

    const interval = setInterval(() => {
      const currentRuns = runsRef.current
      const currentSelectedId = selectedRunIdRef.current
      const currentDetails = selectedRunDetailsRef.current

      const hasActiveRuns = currentRuns.some(r => r.status === 'queued' || r.status === 'running')
      const activeSelectedRun = currentDetails || currentRuns.find(r => r.id === currentSelectedId) || null
      const isSelectedActive = activeSelectedRun && (activeSelectedRun.status === 'queued' || activeSelectedRun.status === 'running')

      if (hasActiveRuns || isSelectedActive) {
        fetchRuns()
        if (currentSelectedId && isSelectedActive) {
          fetchSelectedRunDetails(currentSelectedId)
        }
      }
    }, 1500)

    return () => clearInterval(interval)
  }, [token, fetchRuns, fetchSelectedRunDetails])


  // Utility formatting helpers
  const formatDuration = (ms: number | undefined | null) => {
    if (ms === undefined || ms === null) return '-'
    if (ms < 1000) return `${ms}ms`
    const sec = (ms / 1000).toFixed(1)
    return `${sec}s`
  }

  const formatDate = (isoStr: string | null) => {
    if (!isoStr) return '-'
    const d = new Date(isoStr)
    return d.toLocaleString()
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    setCopySuccess(true)
    setTimeout(() => setCopySuccess(false), 2000)
  }

  // Derived aggregates for Dashboard Header
  const totalRuns = runs.length
  const completedRuns = runs.filter(r => r.status === 'completed')
  const passedRunsCount = completedRuns.filter(r => r.passed).length
  const overallSuccessRate = completedRuns.length > 0 
    ? ((passedRunsCount / completedRuns.length) * 100).toFixed(0) 
    : '0'
  const activeRunsCount = runs.filter(r => r.status === 'queued' || r.status === 'running').length

  // Rendering 1: Login full-screen Glassmorphism if no token
  if (!token) {
    return (
      <div className={styles.loginOverlay}>
        <div className={styles.ambientGlow1}></div>
        <div className={styles.ambientGlow2}></div>

        {/* Floating Switcher Controls inside Login Screen */}
        <div style={{ position: 'absolute', top: '1.5rem', right: '1.5rem', display: 'flex', gap: '0.75rem', zIndex: 1000 }}>
          <button 
            type="button"
            className={styles.actionIconButton} 
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title={theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
          >
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          <button 
            type="button"
            className={styles.actionIconButton} 
            onClick={() => setLang(lang === 'en' ? 'zh' : 'en')}
            title={lang === 'en' ? '切换为中文' : 'Switch to English'}
          >
            <span className={styles.langText}>{lang === 'en' ? 'ZH' : 'EN'}</span>
          </button>
        </div>

        <div className={styles.loginCard}>
          <div className={styles.loginLogoGroup}>
            <div className={styles.loginLogoIcon}>
              <Activity className={styles.pulseIcon} />
            </div>
            <div className={styles.loginLogoText}>
              <h1>{t('platformTitle')}</h1>
              <span>{t('platformSubtitle')}</span>
            </div>
          </div>

          <form onSubmit={handleLoginSubmit} className={styles.form} style={{ padding: 0 }}>
            {loginError && (
              <div className={styles.formErrorAlert} style={{ marginBottom: '1rem' }}>
                <AlertTriangle size={16} />
                <span>{loginError}</span>
              </div>
            )}

            <div className={styles.formField}>
              <label className={styles.label}>
                <span>{t('username')}</span>
              </label>
              <input 
                type="text"
                className={styles.input}
                placeholder={t('usernamePlaceholder')}
                value={loginUsername}
                onChange={(e) => setLoginUsername(e.target.value)}
                disabled={loginLoading}
                required
                autoFocus
              />
            </div>

            <div className={styles.formField} style={{ marginTop: '0.75rem' }}>
              <label className={styles.label}>
                <span>{t('password')}</span>
              </label>
              <input 
                type="password"
                className={styles.input}
                placeholder={t('passwordPlaceholder')}
                value={loginPassword}
                onChange={(e) => setLoginPassword(e.target.value)}
                disabled={loginLoading}
                required
              />
            </div>

            <button 
              type="submit" 
              className={styles.submitButton}
              style={{ marginTop: '1.75rem', justifyContent: 'center', width: '100%' }}
              disabled={loginLoading}
            >
              {loginLoading ? (
                <>
                  <RotateCw size={16} className={styles.spinIcon} />
                  <span>{t('authenticating')}</span>
                </>
              ) : (
                <>
                  <Lock size={16} />
                  <span>{t('signIn')}</span>
                </>
              )}
            </button>
          </form>
        </div>
      </div>
    )
  }

  // Helper to collect all file paths nested recursively under a node folder
  const getAllFilesUnderNode = (node: any): string[] => {
    if (!node.is_dir) {
      return [node.path]
    }
    let paths: string[] = []
    if (node.children) {
      for (const child of node.children) {
        paths = paths.concat(getAllFilesUnderNode(child))
      }
    }
    return paths
  }

  // Get checkbox selection state of a tree node
  const getNodeCheckState = (node: any): 'checked' | 'partial' | 'unchecked' => {
    if (!node.is_dir) {
      return selectedFiles.includes(node.path) ? 'checked' : 'unchecked'
    }
    const descendantFiles = getAllFilesUnderNode(node)
    if (descendantFiles.length === 0) return 'unchecked'
    const checkedCount = descendantFiles.filter(p => selectedFiles.includes(p)).length
    if (checkedCount === descendantFiles.length) {
      return 'checked'
    } else if (checkedCount > 0) {
      return 'partial'
    }
    return 'unchecked'
  }

  // Toggle selection of a folder node or individual test file
  const handleToggleNode = (node: any) => {
    const descendantFiles = getAllFilesUnderNode(node)
    const currentState = getNodeCheckState(node)

    if (currentState === 'checked') {
      setSelectedFiles(prev => prev.filter(p => !descendantFiles.includes(p)))
    } else {
      setSelectedFiles(prev => {
        const filtered = prev.filter(p => !descendantFiles.includes(p))
        return [...filtered, ...descendantFiles]
      })
    }
  }

  // Recursive renderer for the folder/file test suite tree checkbox component
  const renderTreeNode = (node: any, depth = 0) => {
    const isFolder = node.is_dir
    const isExpanded = expandedFolders.includes(node.path)
    const checkState = getNodeCheckState(node)

    return (
      <div key={node.path} className={styles.treeNode} style={{ marginLeft: `${depth * 0.75}rem` }}>
        <div className={styles.treeRow}>
          {isFolder ? (
            <button
              type="button"
              className={`${styles.treeExpandButton} ${isExpanded ? styles.treeExpandButtonExpanded : ''}`}
              onClick={() => {
                setExpandedFolders(prev =>
                  prev.includes(node.path) ? prev.filter(p => p !== node.path) : [...prev, node.path]
                )
              }}
            >
              <ChevronRight size={14} />
            </button>
          ) : (
            <div style={{ width: '16px' }} />
          )}

          <div className={styles.treeCheckboxWrapper} onClick={() => handleToggleNode(node)}>
            <div className={`${styles.treeCheckbox} ${checkState === 'checked' ? styles.treeCheckboxChecked : checkState === 'partial' ? styles.treeCheckboxPartial : ''}`} />
          </div>

          <div className={`${styles.treeLabel} ${isFolder ? styles.treeNodeFolder : styles.treeNodeFile}`} onClick={() => handleToggleNode(node)}>
            {isFolder ? <FolderGit2 size={14} className={styles.treeIcon} /> : <SlidersHorizontal size={12} className={styles.treeIcon} />}
            <span>{node.name}</span>
          </div>
        </div>

        {isFolder && isExpanded && node.children && (
          <div className={styles.treeChildren}>
            {node.children.map((child: any) => renderTreeNode(child, depth + 1))}
          </div>
        )}
      </div>
    )
  }

  // Filter runs by selected suite in left sidebar if active, and by creator type for scheduling segmentation
  const filteredRuns = runs.filter(r => {
    // 1. Suite filter
    if (selectedSuiteFilter && r.tests_path !== selectedSuiteFilter) {
      return false
    }
    // 2. Tab filter
    if (logFilterTab === 'Manual') {
      return r.created_by !== 'system:schedule'
    } else if (logFilterTab === 'Scheduled') {
      return r.created_by === 'system:schedule'
    }
    return true
  })

  // Log downloading helper
  const downloadLogs = (runId: string) => {
    const logText = isStreaming 
      ? streamedStdout 
      : (selectedRun?.stdout || '') + '\n' + (selectedRun?.stderr || '');
    const blob = new Blob([logText], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `run_${runId}_execution.log`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  // Log level filtering helper
  const matchesLogLevel = (line: string, filter: 'ALL' | 'ERROR' | 'WARNING' | 'SUCCESS') => {
    if (filter === 'ALL') return true;
    const lowerLine = line.toLowerCase();
    if (filter === 'ERROR') {
      return (
        lowerLine.includes('failed') ||
        lowerLine.includes('error') ||
        lowerLine.includes('exception') ||
        lowerLine.includes('traceback') ||
        line.startsWith('E   ') ||
        line.startsWith('>   ')
      );
    }
    if (filter === 'WARNING') {
      return lowerLine.includes('warning') || lowerLine.includes('userwarning') || lowerLine.includes('deprecationwarning');
    }
    if (filter === 'SUCCESS') {
      return lowerLine.includes('passed');
    }
    return true;
  };

  // Log filtering helper
  const getFilteredLogs = (text: string) => {
    if (!text) return '';
    let lines = text.split('\n');

    // First stage: Filter by log level
    if (logLevelFilter !== 'ALL') {
      lines = lines.filter(line => matchesLogLevel(line, logLevelFilter));
    }

    // Second stage: Filter by search query
    if (logSearchQuery) {
      const query = logSearchQuery.toLowerCase();
      lines = lines.filter(line => line.toLowerCase().includes(query));
    }

    return lines.join('\n');
  };

  const filteredStdout = selectedRun?.stdout ? getFilteredLogs(selectedRun.stdout) : ''
  const filteredStderr = selectedRun?.stderr ? getFilteredLogs(selectedRun.stderr) : ''
  const filteredStreamed = streamedStdout ? getFilteredLogs(streamedStdout) : ''

  // Aggregation helper for saved profiles statistics in the sidebar
  const getProfileRunStats = (profile: any) => {
    // Find all completed/failed/timeout/running runs matching this profile's tests_path
    const profileRuns = runs.filter(r => r.tests_path === profile.tests_path)

    // Finished runs for pass rate calculation (completed, failed, timeout)
    const finishedRuns = profileRuns.filter(r => r.status === 'completed' || r.status === 'failed' || r.status === 'timeout')

    let passRate = 0
    if (finishedRuns.length > 0) {
      const passedCount = finishedRuns.filter(r => r.status === 'completed' && r.passed === true).length
      passRate = Math.round((passedCount / finishedRuns.length) * 100)
    }

    // Last 5 runs chronologically from left to right (oldest to newest)
    const last5 = profileRuns.slice(0, 5).reverse()

    return {
      passRate,
      hasRuns: finishedRuns.length > 0,
      last5
    };
  };

  // Regex syntax highlighter for comfortable reading
  const formatLogLine = (line: string) => {
    if (line.startsWith('====') || line.startsWith('----') || line.includes('test session starts')) {
      return <span className={styles.logHeaderLine}>{line}</span>
    }
    if (line.includes('PASSED') || line.includes('passed') && (line.includes('in ') || line.includes('==='))) {
      return <span className={styles.logSuccessLine}>{line}</span>
    }
    if (line.includes('FAILED') || line.includes('failed') || line.includes('AssertionError') || line.includes('ValueError') || line.startsWith('E   ') || line.startsWith('>   ')) {
      return <span className={styles.logErrorLine}>{line}</span>
    }
    if (line.includes('WARNING') || line.includes('warning') || line.includes('UserWarning')) {
      return <span className={styles.logWarningLine}>{line}</span>
    }
    return <span>{line}</span>
  }

  const renderFormattedLogs = (text: string) => {
    if (!text) return null
    const lines = text.split('\n')
    return lines.map((line, idx) => (
      <div key={idx} className={styles.terminalLineRow}>
        <span className={styles.terminalLineNumber}>{idx + 1}</span>
        <span className={styles.terminalLineContent}>{formatLogLine(line)}</span>
      </div>
    ))
  }

  // Rendering 2: Full Dashboard View for Authenticated Users
  return (
    <div className={styles.appContainer}>
      {/* Background aesthetics */}
      <div className={styles.ambientGlow1}></div>
      <div className={styles.ambientGlow2}></div>

      {/* Main navigation / header */}
      <header className={styles.header}>
        <div className={styles.logoGroup}>
          <div className={styles.logoIcon}>
            <Activity className={styles.pulseIcon} />
          </div>
          <div className={styles.logoText}>
            <h1>{t('platformTitle')}</h1>
            <span>{t('platformSubtitleFull')}</span>
          </div>
        </div>

        <div className={styles.headerActions}>
          {/* User profile capsule */}
          {currentUser && (
            <div className={styles.userProfileCapsule}>
              <div className={styles.userAvatar}>
                {currentUser.username.substring(0, 2).toUpperCase()}
              </div>
              <div className={styles.userInfo}>
                <span className={styles.profileUsername}>{currentUser.username}</span>
                <span className={`${styles.profileRoleTag} ${styles[`profileRole_${currentUser.role}`]}`}>
                  {currentUser.role}
                </span>
              </div>
            </div>
          )}

          {/* Admin User Management Button */}
          {currentUser?.role === 'admin' && (
            <button 
              className={styles.manageUsersButton}
              onClick={() => {
                fetchUsers()
                setIsUserModalOpen(true)
              }}
              title={t('managePlatformUsers')}
            >
              <Users size={16} />
              <span>{t('users')}</span>
            </button>
          )}

          {/* Theme Toggle */}
          <button 
            className={styles.actionIconButton} 
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title={theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
          >
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          </button>

          {/* Language Toggle */}
          <button 
            className={styles.actionIconButton} 
            onClick={() => setLang(lang === 'en' ? 'zh' : 'en')}
            title={lang === 'en' ? '切换为中文' : 'Switch to English'}
          >
            <span className={styles.langText}>{lang === 'en' ? 'ZH' : 'EN'}</span>
          </button>

          <button 
            className={styles.triggerButton}
            onClick={() => {
              fetchTests()
              setIsTriggerModalOpen(true)
            }}
          >
            <Play size={16} fill="currentColor" />
            <span>{t('triggerRun')}</span>
          </button>

          {/* Logout Trigger */}
          <button 
            className={styles.logoutButton}
            onClick={handleLogout}
            title={t('signOut')}
          >
            <LogOut size={18} />
          </button>
        </div>
      </header>

      {/* Stats Summary Cards */}
      <section className={styles.statsContainer}>
        <div className={styles.statCard}>
          <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(99, 102, 241, 0.1)', color: '#818cf8' }}>
            <Activity size={20} />
          </div>
          <div className={styles.statDetails}>
            <span className={styles.statLabel}>{t('totalExecutions')}</span>
            <h2 className={styles.statValue}>{totalRuns}</h2>
          </div>
        </div>

        <div className={styles.statCard}>
          <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(16, 185, 129, 0.1)', color: '#10b981' }}>
            <CheckCircle2 size={20} />
          </div>
          <div className={styles.statDetails}>
            <span className={styles.statLabel}>{t('successRate')}</span>
            <h2 className={styles.statValue}>{overallSuccessRate}%</h2>
          </div>
        </div>

        <div className={styles.statCard}>
          <div className={styles.statIconWrapper} style={{ backgroundColor: 'rgba(239, 68, 68, 0.1)', color: '#ef4444' }}>
            <XCircle size={20} />
          </div>
          <div className={styles.statDetails}>
            <span className={styles.statLabel}>{t('failedRuns')}</span>
            <h2 className={styles.statValue}>
              {runs.filter(r => r.status === 'failed' || (r.status === 'completed' && !r.passed)).length}
            </h2>
          </div>
        </div>

        <div className={styles.statCard}>
          <div className={`${styles.statIconWrapper} ${activeRunsCount > 0 ? styles.pulseGlow : ''}`} style={{ backgroundColor: activeRunsCount > 0 ? 'rgba(245, 158, 11, 0.15)' : 'rgba(245, 158, 11, 0.05)', color: '#f59e0b' }}>
            <RotateCw size={20} className={activeRunsCount > 0 ? styles.spinIcon : ''} />
          </div>
          <div className={styles.statDetails}>
            <span className={styles.statLabel}>{t('activeQueue')}</span>
            <h2 className={styles.statValue}>{activeRunsCount}</h2>
          </div>
        </div>
      </section>

      {/* Main Table section */}
      <main className={styles.mainContent}>
        {/* Left Column: Project Sidebar */}
        <div className={styles.sidebarCard}>
          <div className={styles.sidebarHeader}>
            <FolderGit2 size={16} className={styles.iconAccent} />
            <h2>{t('workspaceSuites')}</h2>
          </div>
          <div className={styles.sidebarContent}>
            {/* "All Suites" selector */}
            <div 
              className={`${styles.sidebarItem} ${selectedSuiteFilter === null ? styles.sidebarItemActive : ''}`}
              onClick={() => setSelectedSuiteFilter(null)}
            >
              <div className={styles.sidebarItemMain}>
                <Activity size={14} className={styles.sidebarIcon} />
                <span>{t('allSuites')}</span>
              </div>
              <span className={styles.suiteCountBadge}>
                {runs.length}
              </span>
            </div>

            {/* Scanned test directories */}
            {tests.length === 0 ? (
              <div className={styles.sidebarEmpty}>
                <p>{t('noSuitesScanned')}</p>
              </div>
            ) : (
              tests.map((suite) => {
                const isFiltered = selectedSuiteFilter === suite
                const suiteRunsCount = runs.filter(r => r.tests_path === suite).length
                const suiteProfiles = profiles.filter(p => p.tests_path === suite)
                return (
                  <div 
                    key={suite}
                    className={styles.sidebarItemContainer}
                  >
                    <div 
                      className={`${styles.sidebarItemRow} ${isFiltered ? styles.sidebarItemRowActive : ''}`}
                      onClick={() => setSelectedSuiteFilter(suite)}
                    >
                      <div className={styles.sidebarItemMain}>
                        <FolderGit2 size={14} className={styles.sidebarIcon} />
                        <span className={styles.suiteNameText} title={suite}>{suite}</span>
                      </div>
                      <div className={styles.sidebarItemActions} onClick={(e) => e.stopPropagation()}>
                        <span className={styles.suiteCountBadge}>
                          {suiteRunsCount}
                        </span>
                        <button 
                          className={styles.quickPlayButton}
                          title={t('quickTrigger')}
                          onClick={() => {
                            setTestsPath(suite)
                            setIsTriggerModalOpen(true)
                          }}
                        >
                          <Play size={10} fill="currentColor" />
                        </button>
                      </div>
                    </div>

                    {suiteProfiles.length > 0 && (
                      <div className={styles.nestedProfileList} onClick={(e) => e.stopPropagation()}>
                        {suiteProfiles.map(profile => {
                          const existingSched = schedules.find(s => s.profile_id === profile.id);
                          const isSchedActive = existingSched?.enabled;

                          // Compute dynamic run statistics
                          const stats = getProfileRunStats(profile);
                          const dots: React.ReactNode[] = [];

                          // Pad with empty dots to keep layout consistent at 5 dots
                          for (let i = 0; i < 5 - stats.last5.length; i++) {
                            dots.push(
                              <span 
                                key={`empty-${i}`} 
                                className={`${styles.historyDot} ${styles.dotEmpty}`} 
                                title={lang === 'zh' ? '无执行记录' : 'No execution'} 
                              />
                            );
                          }

                          // Fill with recent execution colored dots
                          stats.last5.forEach(run => {
                            let dotClass = styles.dotEmpty;
                            let tooltip = '';
                            if (run.status === 'running' || run.status === 'queued') {
                              dotClass = styles.dotRunning;
                              tooltip = lang === 'zh' ? '运行中...' : 'Running...';
                            } else if (run.status === 'completed' && run.passed) {
                              dotClass = styles.dotPass;
                              tooltip = lang === 'zh' 
                                ? `已通过 (耗时: ${run.summary?.duration_ms ? Math.round(run.summary.duration_ms / 1000) : 0}秒)\n${new Date(run.created_at).toLocaleString()}` 
                                : `Passed (${run.summary?.duration_ms ? Math.round(run.summary.duration_ms / 1000) : 0}s)\n${new Date(run.created_at).toLocaleString()}`;
                            } else {
                              dotClass = styles.dotFail;
                              tooltip = lang === 'zh' 
                                ? `未通过\n${new Date(run.created_at).toLocaleString()}` 
                                : `Failed\n${new Date(run.created_at).toLocaleString()}`;
                            }

                            dots.push(
                              <span 
                                key={run.id} 
                                className={`${styles.historyDot} ${dotClass}`} 
                                title={tooltip}
                                onClick={() => setSelectedRunId(run.id)}
                              />
                            );
                          });

                          return (
                            <div key={profile.id} className={styles.nestedProfileItem} title={profile.description || ''}>
                              {/* Row 1: Profile Main Info and Actions */}
                              <div className={styles.nestedProfileMainRow}>
                                <div className={styles.nestedProfileInfo}>
                                  <SlidersHorizontal size={11} className={styles.nestedProfileIcon} />
                                  <span className={styles.nestedProfileName}>{profile.name}</span>
                                  {isSchedActive && (
                                    <span className={styles.activeScheduleIndicator} title={lang === 'zh' ? `定时已启用: ${existingSched.cron_expression}` : `Schedule active: ${existingSched.cron_expression}`} />
                                  )}
                                </div>
                                <div className={styles.nestedProfileActions}>
                                  <button 
                                    className={styles.nestedProfilePlayButton}
                                    title={lang === 'zh' ? '立即执行' : 'Instant Run'}
                                    onClick={() => handleTriggerProfile(profile)}
                                  >
                                    <Play size={8} fill="currentColor" />
                                  </button>
                                  <button 
                                    className={styles.nestedProfileEditButton}
                                    title={lang === 'zh' ? '编辑方案内容' : 'Edit Profile'}
                                    onClick={() => handleOpenEditProfile(profile)}
                                  >
                                    <Pencil size={8} />
                                  </button>
                                  <button 
                                    className={`${styles.nestedProfileClockButton} ${isSchedActive ? styles.nestedProfileClockButtonActive : ''}`}
                                    title={lang === 'zh' ? '配置定时调度' : 'Configure Schedule'}
                                    onClick={() => handleOpenScheduleModal(profile)}
                                  >
                                    <Clock size={8} />
                                  </button>
                                  <button 
                                    className={styles.nestedProfileDeleteButton}
                                    title={lang === 'zh' ? '删除方案' : 'Delete Profile'}
                                    onClick={(e) => handleDeleteProfile(profile.id, e)}
                                  >
                                    <X size={8} />
                                  </button>
                                </div>
                              </div>

                              {/* Row 2: Performance metrics and historical circles */}
                              <div className={styles.nestedProfileStatsRow}>
                                {stats.hasRuns ? (
                                  <span className={`${styles.profilePassRateBadge} ${stats.passRate >= 80 ? styles.badgeHighPass : stats.passRate >= 50 ? styles.badgeMediumPass : styles.badgeLowPass}`}>
                                    {stats.passRate}% {lang === 'zh' ? '通过率' : 'Pass'}
                                  </span>
                                ) : (
                                  <span className={styles.profileNoRunsBadge}>
                                    {lang === 'zh' ? '暂无记录' : 'No runs'}
                                  </span>
                                )}
                                <div className={styles.profileHistoryDots} title={lang === 'zh' ? '最近 5 次执行历史 (从左至右: 较早 -> 最新，点击圆点可载入日志)' : 'Last 5 runs (left to right: older -> newest, click to load logs)'}>
                                  {dots}
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </div>

        <div className={styles.tableCard}>
          <div className={styles.tableHeader}>
            <div className={styles.tableTitleGroup}>
              <BarChart3 size={18} className={styles.iconMuted} />
              <h2>{t('executionRecords')}</h2>
            </div>

            {/* Segmented Filter Tab */}
            <div className={styles.logFilters}>
              <button 
                className={`${styles.logFilterButton} ${logFilterTab === 'All' ? styles.logFilterButtonActive : ''}`}
                onClick={() => setLogFilterTab('All')}
              >
                <Activity size={12} />
                <span>{lang === 'zh' ? '全部记录' : 'All Runs'}</span>
              </button>
              <button 
                className={`${styles.logFilterButton} ${logFilterTab === 'Manual' ? styles.logFilterButtonActive : ''}`}
                onClick={() => setLogFilterTab('Manual')}
              >
                <Users size={12} />
                <span>{lang === 'zh' ? '手动触发' : 'Manually Triggered'}</span>
              </button>
              <button 
                className={`${styles.logFilterButton} ${logFilterTab === 'Scheduled' ? styles.logFilterButtonActive : ''}`}
                onClick={() => setLogFilterTab('Scheduled')}
              >
                <Clock size={12} />
                <span>{lang === 'zh' ? '定时触发' : 'Scheduled Runs'}</span>
              </button>
            </div>

            <button 
              className={styles.refreshIconButton} 
              onClick={fetchRuns}
              title={t('refreshLogs')}
            >
              <RotateCw size={16} />
            </button>
          </div>

          {loading && runs.length === 0 ? (
            <div className={styles.loadingState}>
              <RotateCw size={36} className={styles.spinIcon} />
              <p>{t('loadingHistory')}</p>
            </div>
          ) : runs.length === 0 ? (
            <div className={styles.emptyState}>
              <Sparkles size={48} className={styles.iconSparkle} />
              <h3>{t('noRunsTitle')}</h3>
              <p>{t('noRunsDesc')}</p>
              <button 
                className={styles.triggerButton}
                onClick={() => setIsTriggerModalOpen(true)}
                style={{ marginTop: '1.5rem' }}
              >
                <Play size={16} fill="currentColor" />
                <span>{t('launchFirstRun')}</span>
              </button>
            </div>
          ) : (
            <div className={styles.tableWrapper}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>{t('runId')}</th>
                    <th style={{ width: '50px', textAlign: 'center' }}><Lock size={12} /></th>
                    <th>{t('targetSuite')}</th>
                    <th>{t('status')}</th>
                    <th>{t('engine')}</th>
                    <th>{t('owner')}</th>
                    <th>{t('results')}</th>
                    <th>{t('passRate')}</th>
                    <th>{t('duration')}</th>
                    <th>{t('createdAt')}</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRuns.map((run) => {
                    const isSelected = run.id === selectedRunId
                    return (
                      <tr 
                        key={run.id}
                        className={`${styles.tableRow} ${isSelected ? styles.rowSelected : ''}`}
                        onClick={() => setSelectedRunId(run.id)}
                      >
                        <td className={styles.cellId}>
                          <code>{run.id.slice(0, 8)}</code>
                        </td>
                        <td style={{ textAlign: 'center' }} onClick={(e) => e.stopPropagation()}>
                          <button
                            type="button"
                            className={`${styles.lockButton} ${run.locked ? styles.lockButtonActive : ''}`}
                            onClick={(e) => handleToggleLock(run.id, e)}
                            title={run.locked 
                              ? (lang === 'zh' ? '已锁定 (保护文件不被清理)' : 'Locked (Protected from physical cleanup)') 
                              : (lang === 'zh' ? '未锁定 (可进行物理清理)' : 'Unlocked (Eligible for physical cleanup)')
                            }
                          >
                            {run.locked ? (
                              <Lock size={12} className={styles.lockIconActive} />
                            ) : (
                              <Unlock size={12} className={styles.lockIconInactive} />
                            )}
                          </button>
                        </td>
                        <td className={styles.cellPath}>
                          <FolderGit2 size={15} className={styles.inlineIcon} />
                          <span>{run.tests_path}</span>
                        </td>
                        <td>
                          <span className={`${styles.badge} ${styles[`badge_${run.status}`]}`}>
                            {run.status === 'queued' && <RotateCw size={12} className={styles.spinIcon} />}
                            {run.status === 'running' && <Activity size={12} className={styles.pulseIcon} />}
                            {run.status === 'completed' && run.passed && <CheckCircle2 size={12} />}
                            {run.status === 'completed' && !run.passed && <XCircle size={12} />}
                            {run.status === 'failed' && <XCircle size={12} />}
                            {run.status === 'timeout' && <Clock size={12} />}
                            <span className={styles.badgeText}>{t(`status_${run.status}`)}</span>
                          </span>
                        </td>
                        <td>
                          <span className={`${styles.engineBadge} ${
                            run.executor_mode === 'docker' ? styles.engineBadge_docker : styles.engineBadge_subprocess
                          }`}>
                            {run.executor_mode === 'docker' ? <Box size={12} className={styles.inlineIcon} /> : <Cpu size={12} className={styles.inlineIcon} />}
                            <span>{t(`engine_${run.executor_mode}`)}</span>
                          </span>
                        </td>
                        <td>
                          <span className={`${styles.ownerBadge} ${
                            run.created_by === 'system' ? styles.ownerBadge_system :
                            run.created_by === 'admin' ? styles.ownerBadge_admin : styles.ownerBadge_user
                          }`}>
                            {run.created_by}
                          </span>
                        </td>
                        <td>
                          {run.summary ? (
                            <span className={styles.summaryStats}>
                              <span className={styles.textPassed}>{run.summary.passed}</span>
                              <span className={styles.statDivider}>/</span>
                              <span className={styles.textFailed}>{run.summary.failed + run.summary.error}</span>
                              <span className={styles.statDivider}>/</span>
                              <span>{run.summary.total}</span>
                            </span>
                          ) : (
                            <span className={styles.textMuted}>-</span>
                          )}
                        </td>
                        <td>
                          {run.summary ? (
                            <div className={styles.progressContainer}>
                              <div className={styles.progressBarWrapper}>
                                <div 
                                  className={`${styles.progressBar} ${run.passed ? styles.bgPassed : styles.bgFailed}`}
                                  style={{ width: `${run.summary.pass_rate * 100}%` }}
                                ></div>
                              </div>
                              <span className={styles.progressText}>
                                {(run.summary.pass_rate * 100).toFixed(0)}%
                              </span>
                            </div>
                          ) : (
                            <span className={styles.textMuted}>-</span>
                          )}
                        </td>
                        <td className={styles.textMono}>
                          {formatDuration(run.summary?.duration_ms)}
                        </td>
                        <td className={styles.textMuted}>
                          {formatDate(run.created_at)}
                        </td>
                        <td className={styles.cellArrow}>
                          <ChevronRight size={16} />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>

      {/* Drawer: Detailed Run Information */}
      <div className={`${styles.drawerOverlay} ${selectedRun ? styles.drawerOpen : ''}`} onClick={() => {
        setSelectedRunId(null)
        setIsDrawerExpanded(false)
      }}>
        <div className={`${styles.drawer} ${isDrawerExpanded ? styles.drawerExpanded : ''}`} onClick={(e) => e.stopPropagation()}>
          <div className={styles.drawerHeader}>
            <div className={styles.drawerTitleGroup}>
              <h3>{t('executionDetails')}</h3>
              <code>{t('id')}: {selectedRun?.id}</code>
            </div>
            <div className={styles.drawerHeaderActions}>
              <button 
                className={styles.drawerExpandButton} 
                onClick={() => setIsDrawerExpanded(!isDrawerExpanded)}
                title={isDrawerExpanded ? (lang === 'zh' ? "收起面板" : "Collapse Panel Width") : (lang === 'zh' ? "宽屏模式" : "Expand Panel Width")}
              >
                {isDrawerExpanded ? <ChevronsRight size={18} /> : <ChevronsLeft size={18} />}
              </button>
              <button className={styles.drawerCloseButton} onClick={() => {
                setSelectedRunId(null)
                setIsDrawerExpanded(false)
              }}>
                <X size={20} />
              </button>
            </div>
          </div>

          {selectedRun && (
            <div className={styles.drawerContent}>
              {/* Top Big Status Badge */}
              <div className={styles.drawerStatusSection}>
                <span className={`${styles.drawerBadge} ${styles[`badge_${selectedRun.status}`]}`}>
                  {selectedRun.status === 'queued' && <RotateCw size={16} className={styles.spinIcon} />}
                  {selectedRun.status === 'running' && <Activity size={16} className={styles.pulseIcon} />}
                  {selectedRun.status === 'completed' && selectedRun.passed && <CheckCircle2 size={16} />}
                  {selectedRun.status === 'completed' && !selectedRun.passed && <XCircle size={16} />}
                  {selectedRun.status === 'failed' && <XCircle size={16} />}
                  {selectedRun.status === 'timeout' && <Clock size={16} />}
                  <span>{t(`status_${selectedRun.status}`).toUpperCase()}</span>
                </span>
                {selectedRun.status === 'completed' && (
                  <span className={selectedRun.passed ? styles.textSuccessGlow : styles.textDangerGlow}>
                    {selectedRun.passed ? t('allTestsPassed') : t('suiteFailed')}
                  </span>
                )}
              </div>

              {/* Info Matrix grid */}
              <div className={styles.infoGrid}>
                <div className={styles.infoItem}>
                  <span className={styles.infoLabel}>{t('runner')}</span>
                  <span className={styles.infoValue}>{selectedRun.runner}</span>
                </div>
                <div className={styles.infoItem}>
                  <span className={styles.infoLabel}>{t('exitCode')}</span>
                  <span className={styles.infoValue}>
                    {selectedRun.exit_code !== null ? selectedRun.exit_code : '-'}
                  </span>
                </div>
                <div className={styles.infoItem}>
                  <span className={styles.infoLabel}>{t('triggeredBy')}</span>
                  <span className={`${styles.ownerBadge} ${
                    selectedRun.created_by === 'system' ? styles.ownerBadge_system :
                    selectedRun.created_by === 'admin' ? styles.ownerBadge_admin : styles.ownerBadge_user
                  }`} style={{ marginTop: '0.15rem', alignSelf: 'flex-start' }}>
                    {selectedRun.created_by}
                  </span>
                </div>
                <div className={styles.infoItem}>
                  <span className={styles.infoLabel}>{t('environment')}</span>
                  <span className={`${styles.engineBadge} ${
                    selectedRun.executor_mode === 'docker' ? styles.engineBadge_docker : styles.engineBadge_subprocess
                  }`} style={{ marginTop: '0.15rem', alignSelf: 'flex-start' }}>
                    {selectedRun.executor_mode === 'docker' ? <Box size={12} className={styles.inlineIcon} /> : <Cpu size={12} className={styles.inlineIcon} />}
                    <span>{t(`engine_${selectedRun.executor_mode}`)}</span>
                  </span>
                </div>
                <div className={styles.infoItem}>
                  <span className={styles.infoLabel}>{t('created')}</span>
                  <span className={styles.infoValue}>{formatDate(selectedRun.created_at)}</span>
                </div>
                <div className={styles.infoItem}>
                  <span className={styles.infoLabel}>{t('finished')}</span>
                  <span className={styles.infoValue}>{formatDate(selectedRun.finished_at)}</span>
                </div>
              </div>

              {/* Tab Selector Segmented Control */}
              <div className={styles.drawerTabs}>
                <button 
                  className={`${styles.drawerTabButton} ${drawerTab === 'logs' ? styles.drawerTabButtonActive : ''}`}
                  onClick={() => setDrawerTab('logs')}
                >
                  <Terminal size={14} />
                  <span>{t('consoleLogs')}</span>
                </button>
                <button 
                  className={`${styles.drawerTabButton} ${drawerTab === 'report' ? styles.drawerTabButtonActive : ''}`}
                  onClick={() => setDrawerTab('report')}
                >
                  <BarChart3 size={14} />
                  <span>{t('testReport')}</span>
                </button>
              </div>

              {drawerTab === 'logs' && (
                <>
                  {/* Terminal Console Logs */}
                  <div className={styles.terminalSection}>
                    <div className={styles.terminalHeader}>
                      <div className={styles.terminalTitleGroup}>
                        <Terminal size={14} className={styles.terminalHeaderIcon} />
                        <h4>{t('consoleLogs')}</h4>
                      </div>
                      
                      <div className={styles.terminalHeaderControls}>
                        {/* Search Input Box */}
                        <div className={styles.terminalSearchWrapper}>
                          <Search size={12} className={styles.terminalSearchIcon} />
                          <input 
                            type="text" 
                            className={styles.terminalSearchInput}
                            placeholder={lang === 'zh' ? "搜索日志..." : "Search logs..."}
                            value={logSearchQuery}
                            onChange={(e) => setLogSearchQuery(e.target.value)}
                          />
                          {logSearchQuery && (
                            <button 
                              className={styles.terminalSearchClear} 
                              onClick={() => setLogSearchQuery('')}
                              title={lang === 'zh' ? "清除搜索" : "Clear search"}
                            >
                              <X size={10} />
                            </button>
                          )}
                        </div>

                        {/* Log Level Capsule Filters */}
                        <div className={styles.logLevelCapsules}>
                          {(['ALL', 'ERROR', 'WARNING', 'SUCCESS'] as const).map(level => {
                            let levelLabel: string = level;
                            if (lang === 'zh') {
                              levelLabel = level === 'ALL' ? '全部' : level === 'ERROR' ? '异常' : level === 'WARNING' ? '警告' : '成功';
                            } else {
                              levelLabel = level === 'ALL' ? 'ALL' : level === 'ERROR' ? 'ERR' : level === 'WARNING' ? 'WARN' : 'OK';
                            }
                            return (
                              <button
                                key={level}
                                className={`${styles.capsuleBtn} ${styles[`capsule_${level}`]} ${logLevelFilter === level ? styles.capsuleActive : ''}`}
                                onClick={() => setLogLevelFilter(level)}
                              >
                                {levelLabel}
                              </button>
                            );
                          })}
                        </div>

                        {/* Font Sizer Controls */}
                        <div className={styles.fontSizeControls}>
                          <button 
                            className={styles.fontSizeBtn}
                            onClick={() => setTerminalFontSize(prev => Math.max(10, prev - 1))}
                            title={lang === 'zh' ? "减小字号" : "Decrease Font Size"}
                          >
                            <ZoomOut size={12} />
                          </button>
                          <span className={styles.fontSizeValue}>{terminalFontSize}px</span>
                          <button 
                            className={styles.fontSizeBtn}
                            onClick={() => setTerminalFontSize(prev => Math.min(20, prev + 1))}
                            title={lang === 'zh' ? "增大字号" : "Increase Font Size"}
                          >
                            <ZoomIn size={12} />
                          </button>
                        </div>

                        {/* Height toggle button */}
                        <button 
                          className={styles.terminalHeightBtn}
                          onClick={() => setIsTerminalHeightExpanded(!isTerminalHeightExpanded)}
                          title={isTerminalHeightExpanded ? (lang === 'zh' ? "折叠控制台高度" : "Minimize height") : (lang === 'zh' ? "展开控制台高度" : "Maximize height")}
                        >
                          {isTerminalHeightExpanded ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
                        </button>

                        {/* Fullscreen toggle button */}
                        <button 
                          className={styles.terminalCopyButton}
                          onClick={() => setIsTerminalFullscreen(true)}
                          title={lang === 'zh' ? "全屏终端" : "Fullscreen Terminal"}
                        >
                          <Maximize2 size={12} />
                          <span>{lang === 'zh' ? "全屏终端" : "Fullscreen"}</span>
                        </button>

                        {/* Live Streaming indicator */}
                        {(selectedRun.status === 'running' || selectedRun.status === 'queued') && (
                          <span className={isStreaming ? styles.livePulse : styles.streamingIndicator}>
                            {!isStreaming && <span className={styles.streamingDot}></span>}
                            {isStreaming ? (lang === 'zh' ? '实时' : 'LIVE') : t('streaming')}
                          </span>
                        )}

                        {/* Copy Button */}
                        <button 
                          className={styles.terminalCopyButton}
                          onClick={() => {
                            const logsText = isStreaming 
                              ? streamedStdout 
                              : (selectedRun.stdout || '') + '\n' + (selectedRun.stderr || '');
                            copyToClipboard(logsText);
                          }}
                        >
                          {copySuccess ? <Check size={12} style={{ color: '#10b981' }} /> : <Copy size={12} />}
                          <span>{copySuccess ? t('copied') : t('copy')}</span>
                        </button>

                        {/* Download Button */}
                        <button 
                          className={styles.terminalCopyButton}
                          onClick={() => downloadLogs(selectedRun.id)}
                          title={lang === 'zh' ? '下载完整日志' : 'Download raw log file'}
                        >
                          <Download size={12} />
                          <span>{t('download')}</span>
                        </button>
                      </div>
                    </div>
                    <div 
                      className={`${styles.terminalBlock} ${isTerminalHeightExpanded ? styles.terminalBlockExpanded : ''}`} 
                      ref={terminalRef}
                      style={{ fontSize: `${terminalFontSize}px` }}
                    >
                      {isStreaming ? (
                        filteredStreamed ? (
                          <pre className={styles.stdoutPre}>{renderFormattedLogs(filteredStreamed)}</pre>
                        ) : streamedStdout ? (
                          <span className={styles.terminalPlaceholder}>
                            {lang === 'zh' ? '无匹配搜索结果' : 'No matching logs found'}
                          </span>
                        ) : (
                          <span className={styles.terminalPlaceholder}>
                            <span className={styles.waitingLogs}>
                              <span className={styles.pulsingText}>{t('waitingLogs')}</span>
                            </span>
                          </span>
                        )
                      ) : (selectedRun.stdout || selectedRun.stderr || streamedStdout) ? (
                        (filteredStdout || filteredStderr || filteredStreamed) ? (
                          <>
                            {(filteredStdout || (selectedRunDetails ? null : filteredStreamed)) && (
                              <pre className={styles.stdoutPre}>
                                {renderFormattedLogs(filteredStdout || filteredStreamed)}
                              </pre>
                            )}
                            {filteredStderr && <pre className={styles.stderrPre}>{renderFormattedLogs(filteredStderr)}</pre>}
                          </>
                        ) : (
                          <span className={styles.terminalPlaceholder}>
                            {lang === 'zh' ? '无匹配搜索结果' : 'No matching logs found'}
                          </span>
                        )
                      ) : detailsLoading ? (
                        <span className={styles.terminalPlaceholder}>
                          <span className={styles.waitingLogs}>
                            <span className={styles.pulsingText}>{lang === 'zh' ? '正在加载控制台日志...' : 'Loading console logs...'}</span>
                          </span>
                        </span>
                      ) : (
                        <span className={styles.terminalPlaceholder}>
                          {t('noLogsAvailable')}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Arguments box */}
                  {selectedRun.args && selectedRun.args.length > 0 && (
                    <div className={styles.argsBlock}>
                      <h4>{t('pytestArguments')}</h4>
                      <div className={styles.argsWrapper}>
                        {selectedRun.args.map((arg: string, idx: number) => (
                          <code key={idx} className={styles.argBadge}>{arg}</code>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Error logs container */}
                  {selectedRun.error && (
                    <div className={styles.errorLogSection}>
                      <div className={styles.errorLogHeader}>
                        <h4>{t('executionStacktrace')}</h4>
                        <button 
                          className={styles.copyButton}
                          onClick={() => copyToClipboard(selectedRun.error || '')}
                        >
                          {copySuccess ? <Check size={14} style={{ color: '#10b981' }} /> : <Copy size={14} />}
                          <span>{copySuccess ? t('copied') : t('copy')}</span>
                        </button>
                      </div>
                      <pre className={styles.errorText}>
                        <code>{selectedRun.error}</code>
                      </pre>
                    </div>
                  )}
                </>
              )}

              {drawerTab === 'report' && (
                <>
                  {/* Dynamic summary chart block */}
                  {selectedRun.summary ? (
                    <div className={styles.summaryBreakdown}>
                      <h4>{t('testOutcomes')}</h4>
                      <div className={styles.summaryGrid}>
                        <div className={styles.summaryBox} style={{ borderLeftColor: '#10b981' }}>
                          <span className={styles.summaryBoxLabel}>{t('passed')}</span>
                          <span className={`${styles.summaryBoxValue} ${styles.textPassed}`}>
                            {selectedRun.summary.passed}
                          </span>
                        </div>
                        <div className={styles.summaryBox} style={{ borderLeftColor: '#ef4444' }}>
                          <span className={styles.summaryBoxLabel}>{t('failed')}</span>
                          <span className={`${styles.summaryBoxValue} ${styles.textFailed}`}>
                            {selectedRun.summary.failed}
                          </span>
                        </div>
                        <div className={styles.summaryBox} style={{ borderLeftColor: '#f59e0b' }}>
                          <span className={styles.summaryBoxLabel}>{t('error')}</span>
                          <span className={`${styles.summaryBoxValue} ${styles.textError}`}>
                            {selectedRun.summary.error}
                          </span>
                        </div>
                        <div className={styles.summaryBox} style={{ borderLeftColor: '#6b7280' }}>
                          <span className={styles.summaryBoxLabel}>{t('skipped')}</span>
                          <span className={`${styles.summaryBoxValue} ${styles.textSkipped}`}>
                            {selectedRun.summary.skipped}
                          </span>
                        </div>
                      </div>

                      {/* Multi-segmented single bar chart */}
                      <div className={styles.segmentBar}>
                        {selectedRun.summary.passed > 0 && (
                          <div 
                            className={styles.segmentPassed} 
                            style={{ width: `${(selectedRun.summary.passed / selectedRun.summary.total) * 100}%` }}
                            title={`${t('passed')}: ${selectedRun.summary.passed}`}
                          ></div>
                        )}
                        {selectedRun.summary.failed > 0 && (
                          <div 
                            className={styles.segmentFailed} 
                            style={{ width: `${(selectedRun.summary.failed / selectedRun.summary.total) * 100}%` }}
                            title={`${t('failed')}: ${selectedRun.summary.failed}`}
                          ></div>
                        )}
                        {selectedRun.summary.error > 0 && (
                          <div 
                            className={styles.segmentError} 
                            style={{ width: `${(selectedRun.summary.error / selectedRun.summary.total) * 100}%` }}
                            title={`${t('error')}: ${selectedRun.summary.error}`}
                          ></div>
                        )}
                        {selectedRun.summary.skipped > 0 && (
                          <div 
                            className={styles.segmentSkipped} 
                            style={{ width: `${(selectedRun.summary.skipped / selectedRun.summary.total) * 100}%` }}
                            title={`${t('skipped')}: ${selectedRun.summary.skipped}`}
                          ></div>
                        )}
                      </div>
                      <div className={styles.durationBreakdown}>
                        <Clock size={14} />
                        <span>{t('durationLabel')}: {formatDuration(selectedRun.summary.duration_ms)}</span>
                      </div>
                    </div>
                  ) : (
                    <div className={styles.summaryPlaceholder}>
                      <Activity size={24} className={styles.pulseIcon} style={{ color: '#06b6d4', marginBottom: '0.75rem' }} />
                      <p>{t('execInProgress')}</p>
                      <span>{t('execInProgressDesc')}</span>
                    </div>
                  )}

                  {/* Action: Open Allure Report (Inline Iframe Integration) */}
                  {selectedRun.report?.html_generated && selectedRun.report?.allure_report_file ? (
                    <div className={styles.reportIframeContainer}>
                      <div className={styles.reportIframeHeader}>
                        <div className={styles.reportIframeTitle}>
                          <BarChart3 size={14} style={{ color: '#a855f7' }} />
                          <span>{lang === 'zh' ? 'Allure 交互式测试报告' : 'Allure Interactive Test Report'}</span>
                        </div>
                        <div className={styles.reportIframeHeaderActions}>
                          <a 
                            href={`/runs/${selectedRun.id}/report?token=${token}`}
                            target="_blank"
                            rel="noreferrer"
                            className={styles.terminalCopyButton}
                            style={{ padding: '0.2rem 0.5rem', borderRadius: '4px', fontSize: '0.7rem' }}
                            title={lang === 'zh' ? '在新窗口中打开' : 'Open in New Window'}
                          >
                            <ExternalLink size={10} />
                            <span>{lang === 'zh' ? '新窗口打开' : 'New Window'}</span>
                          </a>
                        </div>
                      </div>

                      {isIframeLoading && (
                        <div className={styles.reportIframeLoading}>
                          <RotateCw size={24} className={styles.spinIcon} style={{ color: '#06b6d4' }} />
                          <span>{lang === 'zh' ? '正在载入测试报告资源...' : 'Loading Allure report resources...'}</span>
                        </div>
                      )}

                      <iframe
                        src={`/runs/${selectedRun.id}/report?token=${token}`}
                        className={styles.reportIframe}
                        onLoad={() => setIsIframeLoading(false)}
                        title="Allure Report"
                      />
                    </div>
                  ) : (
                    <div className={styles.reportPlaceholder}>
                      {selectedRun.status === 'running' || selectedRun.status === 'queued' ? (
                        <>
                          <RotateCw size={24} className={styles.spinIcon} style={{ color: '#a855f7', marginBottom: '0.75rem' }} />
                          <p>{t('generatingAllureReport')}</p>
                          <span>{t('generatingAllureReportDesc')}</span>
                        </>
                      ) : (
                        <>
                          <AlertTriangle size={24} style={{ color: '#f59e0b', marginBottom: '0.75rem' }} />
                          <p>{t('noReportGenerated')}</p>
                          <span>{t('noReportGeneratedDesc')}</span>
                        </>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Fullscreen Terminal Overlay */}
      {isTerminalFullscreen && selectedRun && (
        <div 
          className={styles.fullscreenTerminalOverlay} 
          onClick={() => setIsTerminalFullscreen(false)}
        >
          <div 
            className={styles.fullscreenTerminal} 
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header Controls */}
            <div className={styles.fullscreenTerminalHeader}>
              <div className={styles.terminalTitleGroup}>
                <Terminal size={16} className={styles.terminalHeaderIcon} />
                <h3 className={styles.fullscreenTerminalTitle}>
                  {lang === 'zh' ? '只读控制台终端' : 'Read-only Console Terminal'}
                  <span className={styles.fullscreenTerminalSub}>
                    #{selectedRun.id}
                  </span>
                </h3>
              </div>
              
              <div className={styles.fullscreenTerminalControls}>
                {/* Search Box */}
                <div className={styles.terminalSearchWrapper}>
                  <Search size={12} className={styles.terminalSearchIcon} />
                  <input 
                    type="text" 
                    className={styles.terminalSearchInput}
                    placeholder={lang === 'zh' ? "搜索日志..." : "Search logs..."}
                    value={logSearchQuery}
                    onChange={(e) => setLogSearchQuery(e.target.value)}
                  />
                  {logSearchQuery && (
                    <button 
                      className={styles.terminalSearchClear} 
                      onClick={() => setLogSearchQuery('')}
                      title={lang === 'zh' ? "清除搜索" : "Clear search"}
                    >
                      <X size={10} />
                    </button>
                  )}
                </div>

                {/* Log Level Capsule Filters */}
                <div className={styles.logLevelCapsules}>
                  {(['ALL', 'ERROR', 'WARNING', 'SUCCESS'] as const).map(level => {
                    let levelLabel: string = level;
                    if (lang === 'zh') {
                      levelLabel = level === 'ALL' ? '全部' : level === 'ERROR' ? '异常' : level === 'WARNING' ? '警告' : '成功';
                    } else {
                      levelLabel = level === 'ALL' ? 'ALL' : level === 'ERROR' ? 'ERR' : level === 'WARNING' ? 'WARN' : 'OK';
                    }
                    return (
                      <button
                        key={level}
                        className={`${styles.capsuleBtn} ${styles[`capsule_${level}`]} ${logLevelFilter === level ? styles.capsuleActive : ''}`}
                        onClick={() => setLogLevelFilter(level)}
                      >
                        {levelLabel}
                      </button>
                    );
                  })}
                </div>

                {/* Font Sizer Controls */}
                <div className={styles.fontSizeControls}>
                  <button 
                    className={styles.fontSizeBtn}
                    onClick={() => setTerminalFontSize(prev => Math.max(10, prev - 1))}
                    title={lang === 'zh' ? "减小字号" : "Decrease Font Size"}
                  >
                    <ZoomOut size={12} />
                  </button>
                  <span className={styles.fontSizeValue}>{terminalFontSize}px</span>
                  <button 
                    className={styles.fontSizeBtn}
                    onClick={() => setTerminalFontSize(prev => Math.min(24, prev + 1))}
                    title={lang === 'zh' ? "增大字号" : "Increase Font Size"}
                  >
                    <ZoomIn size={12} />
                  </button>
                </div>

                {/* Word Wrap Toggle */}
                <button 
                  className={`${styles.terminalToolbarBtn} ${isWordWrapEnabled ? styles.terminalToolbarBtnActive : ''}`}
                  onClick={() => setIsWordWrapEnabled(!isWordWrapEnabled)}
                  title={isWordWrapEnabled ? (lang === 'zh' ? "禁用自动换行" : "Disable word wrap") : (lang === 'zh' ? "启用自动换行" : "Enable word wrap")}
                >
                  {lang === 'zh' ? '自动换行' : 'Word Wrap'}
                </button>

                {/* Auto Scroll Toggle */}
                <button 
                  className={`${styles.terminalToolbarBtn} ${isAutoScrollEnabled ? styles.terminalToolbarBtnActive : ''}`}
                  onClick={() => setIsAutoScrollEnabled(!isAutoScrollEnabled)}
                  title={isAutoScrollEnabled ? (lang === 'zh' ? "锁定滚动" : "Freeze scrolling") : (lang === 'zh' ? "自动滚动" : "Auto scroll")}
                >
                  {lang === 'zh' ? '滚动锁定' : 'Scroll Lock'}
                </button>

                {/* Copy Button */}
                <button 
                  className={styles.terminalCopyButton}
                  onClick={() => {
                    const logsText = isStreaming 
                      ? streamedStdout 
                      : (selectedRun.stdout || '') + '\n' + (selectedRun.stderr || '');
                    copyToClipboard(logsText);
                  }}
                >
                  {copySuccess ? <Check size={12} style={{ color: '#10b981' }} /> : <Copy size={12} />}
                  <span>{copySuccess ? t('copied') : t('copy')}</span>
                </button>

                {/* Download Button */}
                <button 
                  className={styles.terminalCopyButton}
                  onClick={() => downloadLogs(selectedRun.id)}
                  title={lang === 'zh' ? '下载完整日志' : 'Download raw log file'}
                >
                  <Download size={12} />
                  <span>{t('download')}</span>
                </button>

                {/* Close Button */}
                <button 
                  className={styles.fullscreenTerminalCloseBtn}
                  onClick={() => setIsTerminalFullscreen(false)}
                  title={lang === 'zh' ? "关闭全屏" : "Close fullscreen"}
                >
                  <X size={16} />
                </button>
              </div>
            </div>

            {/* Terminal Content Body */}
            <div 
              className={`${styles.fullscreenTerminalBody} ${!isWordWrapEnabled ? styles.noWrapPre : ''}`}
              ref={fullscreenTerminalRef}
              style={{ fontSize: `${terminalFontSize}px` }}
            >
              {isStreaming ? (
                filteredStreamed ? (
                  <pre className={styles.stdoutPre}>{renderFormattedLogs(filteredStreamed)}</pre>
                ) : streamedStdout ? (
                  <span className={styles.terminalPlaceholder}>
                    {lang === 'zh' ? '无匹配搜索结果' : 'No matching logs found'}
                  </span>
                ) : (
                  <span className={styles.terminalPlaceholder}>
                    <span className={styles.waitingLogs}>
                      <span className={styles.pulsingText}>{t('waitingLogs')}</span>
                    </span>
                  </span>
                )
              ) : (selectedRun.stdout || selectedRun.stderr || streamedStdout) ? (
                (filteredStdout || filteredStderr || filteredStreamed) ? (
                  <>
                    {(filteredStdout || (selectedRunDetails ? null : filteredStreamed)) && (
                      <pre className={styles.stdoutPre}>
                        {renderFormattedLogs(filteredStdout || filteredStreamed)}
                      </pre>
                    )}
                    {filteredStderr && <pre className={styles.stderrPre}>{renderFormattedLogs(filteredStderr)}</pre>}
                  </>
                ) : (
                  <span className={styles.terminalPlaceholder}>
                    {lang === 'zh' ? '无匹配搜索结果' : 'No matching logs found'}
                  </span>
                )
              ) : detailsLoading ? (
                <span className={styles.terminalPlaceholder}>
                  <span className={styles.waitingLogs}>
                    <span className={styles.pulsingText}>{lang === 'zh' ? '正在加载控制台日志...' : 'Loading console logs...'}</span>
                  </span>
                </span>
              ) : (
                <span className={styles.terminalPlaceholder}>
                  {t('noLogsAvailable')}
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Modal: Trigger Run */}
      {isTriggerModalOpen && (
        <div className={styles.modalOverlay} onClick={() => setIsTriggerModalOpen(false)}>
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <div className={styles.modalHeader}>
              <div className={styles.modalTitleGroup}>
                <SlidersHorizontal size={20} className={styles.iconAccent} />
                <h2>{editingProfileId ? (lang === 'zh' ? '修改预设执行方案' : 'Modify Saved Execution Profile') : t('triggerTitle')}</h2>
              </div>
              <button className={styles.modalCloseButton} onClick={() => setIsTriggerModalOpen(false)}>
                <X size={20} />
              </button>
            </div>

            <form onSubmit={editingProfileId ? handleUpdateProfile : handleTriggerRun} className={styles.form}>
              {formError && (
                <div className={styles.formErrorAlert}>
                  <AlertTriangle size={16} />
                  <span>{t(formError as any) || formError}</span>
                </div>
              )}

              <div className={styles.formField}>
                <label className={styles.label}>
                  <span>{t('targetDirectory')}</span>
                  <span className={styles.requiredIndicator}>*</span>
                </label>
                {tests.length === 0 ? (
                  <div className={styles.directoryFallbackText}>
                    {t('scanningDirectory')}
                  </div>
                ) : (
                  <div className={styles.selectWrapper}>
                    <select 
                      className={styles.select}
                      value={testsPath}
                      onChange={(e) => setTestsPath(e.target.value)}
                      required
                      disabled={!!editingProfileId}
                    >
                      {tests.map(dir => (
                        <option key={dir} value={dir}>{dir}</option>
                      ))}
                    </select>
                  </div>
                )}
                <span className={styles.fieldHelp}>
                  {t('directoryHelp')}
                </span>
              </div>

              {/* 1. Saved Execution Profiles Selection Template */}
              {!editingProfileId && (
                <div className={styles.formField}>
                  <label className={styles.label}>{t('selectProfile')}</label>
                  <div className={styles.profileSelectorContainer}>
                    <select
                      className={styles.select}
                      value={selectedProfileId}
                      onChange={(e) => {
                        const profileId = e.target.value
                        setSelectedProfileId(profileId)
                        if (profileId) {
                          const prof = profiles.find(p => p.id === profileId)
                          if (prof) {
                            setSelectedFiles(prof.selected_files || [])
                            setSelectedMarkers(prof.selected_markers || [])
                            setCustomArgs(prof.extra_args || '')
                            setExecutorMode(prof.executor_mode || 'subprocess')
                            setTimeoutSeconds(prof.timeout || '')
                            const env_data = prof.env || {}
                            const mappedVars = Object.entries(env_data).map(([key, value]) => ({ key, value: String(value) }))
                            setEnvVars(mappedVars)
                          }
                        } else {
                          // Reset to default / manual
                          setSelectedFiles([])
                          setSelectedMarkers([])
                          setCustomArgs('')
                          setExecutorMode('subprocess')
                          setTimeoutSeconds('')
                          setEnvVars([])
                        }
                      }}
                    >
                      <option value="">-- {lang === 'zh' ? '手动配置 (自定义)' : 'Manual Configuration (Custom)'} --</option>
                      {profiles
                        .filter(p => p.tests_path === testsPath)
                        .map(p => (
                          <option key={p.id} value={p.id}>{p.name} {p.description ? `(${p.description})` : ''}</option>
                        ))
                      }
                    </select>
                    {selectedProfileId && (
                      <button
                      type="button"
                      className={styles.nestedProfileDeleteButton}
                      title={lang === 'zh' ? '删除方案' : 'Delete Profile'}
                      onClick={(e) => handleDeleteProfile(selectedProfileId, e)}
                      style={{ flexShrink: 0, width: '32px', height: '32px', borderRadius: '8px' }}
                    >
                      <X size={16} />
                    </button>
                  )}
                </div>
              </div>
            )}

              {/* 2. Visual File Selection Tree Checklist */}
              <div className={styles.formField}>
                <label className={styles.label}>{t('testSuiteSelection')}</label>
                <div className={styles.treeContainer}>
                  {scannedFilesTree.length === 0 ? (
                    <div className={styles.directoryFallbackText} style={{ padding: '0.5rem' }}>
                      {lang === 'zh' ? '无可用测试文件。' : 'No pytest files discovered.'}
                    </div>
                  ) : (
                    scannedFilesTree.map(node => renderTreeNode(node))
                  )}
                </div>
              </div>

              {/* 3. Visual Tag/Marker Picker */}
              {scannedMarkers.length > 0 && (
                <div className={styles.formField}>
                  <label className={styles.label}>{t('scannedMarkersTitle')}</label>
                  <div className={styles.tagContainer}>
                    {scannedMarkers.map(tag => {
                      const isActive = selectedMarkers.includes(tag)
                      return (
                        <span
                          key={tag}
                          className={`${styles.tagPill} ${isActive ? styles.tagPillActive : ''}`}
                          onClick={() => {
                            setSelectedMarkers(prev =>
                              prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]
                            )
                          }}
                        >
                          @{tag}
                        </span>
                      )
                    })}
                  </div>
                </div>
              )}

              <div className={styles.formField}>
                <label className={styles.label}>
                  <span>{t('executionEnvironment')}</span>
                  <span className={styles.requiredIndicator}>*</span>
                </label>
                <div className={styles.segmentedControl}>
                  <button
                    type="button"
                    className={`${styles.segmentButton} ${executorMode === 'subprocess' ? styles.segmentButtonActive : ''}`}
                    onClick={() => setExecutorMode('subprocess')}
                  >
                    <Cpu size={16} />
                    <div className={styles.segmentTextGroup}>
                      <span className={styles.segmentTitle}>{t('localSubprocessTitle')}</span>
                      <span className={styles.segmentDesc}>{t('localSubprocessDesc')}</span>
                    </div>
                  </button>
                  <button
                    type="button"
                    className={`${styles.segmentButton} ${executorMode === 'docker' ? styles.segmentButtonActive : ''}`}
                    onClick={() => setExecutorMode('docker')}
                  >
                    <Box size={16} />
                    <div className={styles.segmentTextGroup}>
                      <span className={styles.segmentTitle}>{t('dockerContainerTitle')}</span>
                      <span className={styles.segmentDesc}>{t('dockerContainerDesc')}</span>
                    </div>
                  </button>
                </div>
                <span className={styles.fieldHelp}>
                  {t('envHelp')}
                </span>
              </div>

              <div className={styles.formField}>
                <label className={styles.label}>{t('pytestArgsLabel')}</label>
                <input 
                  type="text"
                  className={styles.input}
                  placeholder={t('pytestArgsPlaceholder')}
                  value={customArgs}
                  onChange={(e) => setCustomArgs(e.target.value)}
                />
                <span className={styles.fieldHelp}>
                  {t('pytestArgsHelp')}
                </span>
              </div>

              {/* Custom Environment Variables Grid Editor */}
              <div className={styles.formField}>
                <label className={styles.label}>
                  <span>{lang === 'zh' ? '自定义环境变量' : 'Custom Environment Variables'}</span>
                </label>
                <div className={styles.envGridContainer}>
                  {envVars.map((env, idx) => (
                    <div key={idx} className={styles.envRow}>
                      <input
                        type="text"
                        className={styles.envInput}
                        placeholder={lang === 'zh' ? '变量名 e.g. BASE_URL' : 'Name e.g. BASE_URL'}
                        value={env.key}
                        onChange={(e) => {
                          const updated = [...envVars]
                          updated[idx].key = e.target.value
                          setEnvVars(updated)
                        }}
                      />
                      <input
                        type="text"
                        className={styles.envInput}
                        placeholder={lang === 'zh' ? '变量值' : 'Value'}
                        value={env.value}
                        onChange={(e) => {
                          const updated = [...envVars]
                          updated[idx].value = e.target.value
                          setEnvVars(updated)
                        }}
                      />
                      <button
                        type="button"
                        className={styles.envDeleteBtn}
                        title={lang === 'zh' ? '删除' : 'Delete'}
                        onClick={() => {
                          setEnvVars(envVars.filter((_, i) => i !== idx))
                        }}
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    className={styles.envAddBtn}
                    onClick={() => {
                      setEnvVars([...envVars, { key: '', value: '' }])
                    }}
                  >
                    <Plus size={14} />
                    <span>{lang === 'zh' ? '添加环境变量' : 'Add Variable'}</span>
                  </button>
                </div>
              </div>

              <div className={styles.formFieldRow}>
                <div className={styles.formField} style={{ flex: 1 }}>
                  <label className={styles.label}>{t('timeoutLabel')}</label>
                  <input 
                    type="number"
                    className={styles.input}
                    placeholder={t('timeoutPlaceholder')}
                    min="1"
                    value={timeoutSeconds}
                    onChange={(e) => setTimeoutSeconds(e.target.value === '' ? '' : Number(e.target.value))}
                  />
                  <span className={styles.fieldHelp}>
                    {t('timeoutHelp')}
                  </span>
                </div>

                <div className={styles.formField} style={{ flex: '0 0 auto', alignSelf: 'flex-start', paddingTop: '0.5rem' }}>
                  <label className={styles.label}>{t('allureReportsLabel')}</label>
                  <label className={styles.switchContainer}>
                    <input 
                      type="checkbox"
                      className={styles.switchInput}
                      checked={allureEnabled}
                      onChange={(e) => setAllureEnabled(e.target.checked)}
                    />
                    <span className={styles.switchSlider}></span>
                  </label>
                </div>
              </div>

              {/* 4. Save Execution Settings as Profile Toggle & Form */}
              <div className={styles.formField}>
                {editingProfileId ? (
                  <div className={styles.profileSaveForm}>
                    <div className={styles.profileSaveTitleGroup}>
                      <SlidersHorizontal size={14} className={styles.iconAccent} />
                      <h4>{lang === 'zh' ? '修改预设方案信息' : 'Modify Profile Information'}</h4>
                    </div>
                    <p className={styles.profileSaveDesc}>
                      {lang === 'zh' ? '在此处更新当前执行方案的名称和描述信息。' : 'Update the name and description of the current execution profile here.'}
                    </p>
                    
                    <div className={styles.profileSaveInputs}>
                      <input
                        type="text"
                        className={styles.input}
                        placeholder={t('profileNamePlaceholder')}
                        value={profileName}
                        onChange={(e) => setProfileName(e.target.value)}
                        required
                      />
                      <input
                        type="text"
                        className={styles.input}
                        placeholder={t('profileDescPlaceholder')}
                        value={profileDesc}
                        onChange={(e) => setProfileDesc(e.target.value)}
                      />
                    </div>
                  </div>
                ) : !isSavingProfile ? (
                  <button
                    type="button"
                    className={styles.profileSaveToggleBtn}
                    onClick={() => setIsSavingProfile(true)}
                  >
                    <Plus size={14} />
                    <span>{t('saveAsProfile')}</span>
                  </button>
                ) : (
                  <div className={styles.profileSaveForm}>
                    <div className={styles.profileSaveTitleGroup}>
                      <SlidersHorizontal size={14} className={styles.iconAccent} />
                      <h4>{t('saveAsProfile')}</h4>
                    </div>
                    <p className={styles.profileSaveDesc}>{t('saveProfileDesc')}</p>
                    
                    <div className={styles.profileSaveInputs}>
                      <input
                        type="text"
                        className={styles.input}
                        placeholder={t('profileNamePlaceholder')}
                        value={profileName}
                        onChange={(e) => setProfileName(e.target.value)}
                        required={isSavingProfile}
                      />
                      <input
                        type="text"
                        className={styles.input}
                        placeholder={t('profileDescPlaceholder')}
                        value={profileDesc}
                        onChange={(e) => setProfileDesc(e.target.value)}
                      />
                    </div>
                    
                    <div className={styles.profileSaveActions}>
                      <button
                        type="button"
                        className={styles.cancelButton}
                        onClick={() => {
                          setIsSavingProfile(false)
                          setProfileName('')
                          setProfileDesc('')
                        }}
                        style={{ padding: '0.25rem 0.75rem', fontSize: '0.8rem' }}
                      >
                        {t('cancel')}
                      </button>
                      <button
                        type="button"
                        className={styles.profileSaveSubmitBtn}
                        onClick={handleSaveProfile}
                      >
                        {lang === 'zh' ? '确认保存' : 'Save'}
                      </button>
                    </div>
                  </div>
                )}
              </div>

              <div className={styles.formActions}>
                <button 
                  type="button" 
                  className={styles.cancelButton}
                  onClick={() => setIsTriggerModalOpen(false)}
                >
                  {t('cancel')}
                </button>
                <button 
                  type="submit" 
                  className={styles.submitButton}
                  disabled={isSubmitting || tests.length === 0}
                >
                  {isSubmitting ? (
                    <>
                      <RotateCw size={16} className={styles.spinIcon} />
                      <span>{editingProfileId ? (lang === 'zh' ? '正在保存...' : 'Saving Changes...') : t('schedulingTask')}</span>
                    </>
                  ) : editingProfileId ? (
                    <>
                      <Check size={16} />
                      <span>{lang === 'zh' ? '保存方案修改' : 'Save Profile Changes'}</span>
                    </>
                  ) : (
                    <>
                      <Play size={16} fill="currentColor" />
                      <span>{t('launchRun')}</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal: Schedule Manager */}
      {isScheduleModalOpen && scheduleProfile && (
        <div className={styles.modalOverlay} onClick={() => setIsScheduleModalOpen(false)}>
          <div className={`${styles.modal} ${styles.scheduleModal}`} onClick={(e) => e.stopPropagation()}>
            <div className={styles.modalHeader}>
              <div className={styles.modalTitleGroup}>
                <Clock size={20} className={styles.iconAccent} />
                <h2>{lang === 'zh' ? '配置定时运行计划' : 'Configure Scheduled Execution'}</h2>
              </div>
              <button className={styles.modalCloseButton} onClick={() => setIsScheduleModalOpen(false)}>
                <X size={20} />
              </button>
            </div>

            <div className={styles.form}>
              <div className={styles.scheduleProfileBanner}>
                <span className={styles.bannerLabel}>{lang === 'zh' ? '执行方案: ' : 'Profile: '}</span>
                <span className={styles.bannerValue}>{scheduleProfile.name}</span>
                <span className={styles.bannerSuite}>({scheduleProfile.tests_path})</span>
              </div>

              {previewError && (
                <div className={styles.formErrorAlert}>
                  <AlertTriangle size={16} />
                  <span>{previewError}</span>
                </div>
              )}

              <div className={styles.formField}>
                <label className={styles.label}>
                  <span>{lang === 'zh' ? '计划名称' : 'Schedule Name'}</span>
                  <span className={styles.requiredIndicator}>*</span>
                </label>
                <input 
                  type="text"
                  className={styles.input}
                  value={schedName}
                  onChange={(e) => setSchedName(e.target.value)}
                  placeholder={lang === 'zh' ? '输入定时计划名称' : 'e.g. Daily Regression'}
                  required
                />
              </div>

              <div className={styles.formRow}>
                <div className={styles.formField} style={{ flex: 1 }}>
                  <label className={styles.label}>
                    <span>{lang === 'zh' ? 'Cron 表达式' : 'Cron Expression'}</span>
                    <span className={styles.requiredIndicator}>*</span>
                  </label>
                  <input 
                    type="text"
                    className={styles.input}
                    value={schedExpression}
                    onChange={(e) => setSchedExpression(e.target.value)}
                    placeholder="e.g. 0 2 * * *"
                    required
                  />
                  <span className={styles.fieldHelp}>
                    {lang === 'zh' ? '标准 5 位 Cron 语法 (分 时 日 月 周)' : 'Standard 5-field cron syntax (min hour day month day-of-week).'}
                  </span>
                </div>

                <div className={styles.formField} style={{ width: '150px' }}>
                  <label className={styles.label}>
                    <span>{lang === 'zh' ? '时区' : 'Timezone'}</span>
                  </label>
                  <div className={styles.selectWrapper}>
                    <select 
                      className={styles.select}
                      value={schedTimezone}
                      onChange={(e) => setSchedTimezone(e.target.value)}
                    >
                      <option value="UTC">UTC</option>
                      <option value="Asia/Shanghai">Asia/Shanghai</option>
                      <option value="America/New_York">America/New_York</option>
                      <option value="Europe/London">Europe/London</option>
                    </select>
                  </div>
                </div>
              </div>

              <div className={styles.formField}>
                <label className={styles.checkboxLabel} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
                  <input 
                    type="checkbox"
                    className={styles.checkbox}
                    checked={schedEnabled}
                    onChange={(e) => setSchedEnabled(e.target.checked)}
                  />
                  <span>{lang === 'zh' ? '启用此定时调度' : 'Enable this schedule'}</span>
                </label>
              </div>

              {/* Real-time future runs preview widget */}
              <div className={styles.previewWidget}>
                <div className={styles.previewWidgetHeader}>
                  <Calendar size={14} className={styles.previewIcon} />
                  <span>{lang === 'zh' ? '未来 5 次运行时间预测:' : 'Next 5 Projected Runs Preview (Static Check):'}</span>
                </div>
                {previewNextRuns.length > 0 ? (
                  <div className={styles.previewList}>
                    {previewNextRuns.map((runTime, idx) => (
                      <div key={runTime} className={styles.previewItem}>
                        <span className={styles.previewIdx}>#{idx + 1}</span>
                        <span className={styles.previewTime}>{new Date(runTime).toLocaleString(lang === 'zh' ? 'zh-CN' : 'en-US', { timeZone: schedTimezone })}</span>
                        <span className={styles.previewTz}>({schedTimezone})</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className={styles.previewEmpty}>
                    {previewError ? (
                      <span className={styles.previewErrorText}>{previewError}</span>
                    ) : (
                      <span>{lang === 'zh' ? '请输入有效的 Cron 表达式' : 'Please enter a valid Cron expression'}</span>
                    )}
                  </div>
                )}
              </div>

              <div className={styles.modalActions} style={{ display: 'flex', justifyContent: 'space-between', marginTop: '2rem' }}>
                <div>
                  {schedules.some(s => s.profile_id === scheduleProfile.id) && (
                    <button 
                      type="button"
                      className={`${styles.button} ${styles.buttonDanger}`}
                      onClick={() => handleDeleteSchedule(schedules.find(s => s.profile_id === scheduleProfile.id).id)}
                    >
                      {lang === 'zh' ? '注销调度' : 'Delete Schedule'}
                    </button>
                  )}
                </div>
                <div style={{ display: 'flex', gap: '0.75rem' }}>
                  <button 
                    type="button"
                    className={`${styles.button} ${styles.buttonSecondary}`}
                    onClick={() => setIsScheduleModalOpen(false)}
                  >
                    {t('cancel')}
                  </button>
                  <button 
                    type="button"
                    className={`${styles.button} ${styles.buttonPrimary}`}
                    onClick={handleSaveSchedule}
                  >
                    {lang === 'zh' ? '保存配置' : 'Save Config'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Modal: Admin User Management */}
      {isUserModalOpen && currentUser?.role === 'admin' && (
        <div className={styles.modalOverlay} onClick={() => setIsUserModalOpen(false)}>
          <div className={styles.modal} style={{ width: '640px' }} onClick={(e) => e.stopPropagation()}>
            <div className={styles.modalHeader}>
              <div className={styles.modalTitleGroup}>
                <Users size={20} className={styles.iconAccent} />
                <h2>{t('userManagementTitle')}</h2>
              </div>
              <button className={styles.modalCloseButton} onClick={() => setIsUserModalOpen(false)}>
                <X size={20} />
              </button>
            </div>

            {/* Section 1: User Directory List */}
            <div className={styles.userListSection}>
              <div className={styles.sectionHeader}>
                <h3>{t('platformDirectory')}</h3>
                {usersLoading && <RotateCw size={14} className={styles.spinIcon} />}
              </div>

              <div className={styles.userTableWrapper}>
                <table className={styles.userTable}>
                  <thead>
                    <tr>
                      <th>{t('username')}</th>
                      <th>{t('role')}</th>
                      <th>{t('registeredAt')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usersList.length === 0 ? (
                      <tr>
                        <td colSpan={3} style={{ textAlign: 'center', color: 'var(--text-dim)', padding: '1.5rem' }}>
                          {t('noUsersRegistered')}
                        </td>
                      </tr>
                    ) : (
                      usersList.map((usr) => (
                        <tr key={usr.username}>
                          <td className={styles.tdUsername}>{usr.username}</td>
                          <td>
                            <span className={`${styles.roleBadge} ${styles[`roleBadge_${usr.role}`]}`}>
                              {usr.role}
                            </span>
                          </td>
                          <td className={styles.tdDate}>{formatDate(usr.created_at)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Section 2: Register New User Form */}
            <form onSubmit={handleCreateUserSubmit} className={styles.userCreateSection}>
              <div className={styles.sectionHeader}>
                <h3>{t('registerNewUser')}</h3>
              </div>

              {newUserError && (
                <div className={styles.formErrorAlert} style={{ margin: 0 }}>
                  <AlertTriangle size={16} />
                  <span>{t(newUserError as any) || newUserError}</span>
                </div>
              )}

              <div className={styles.userFormRow}>
                <div className={styles.formField}>
                  <label className={styles.label}>{t('username')}</label>
                  <input 
                    type="text"
                    className={styles.input}
                    placeholder="e.g. testing_lead"
                    value={newUsername}
                    onChange={(e) => setNewUsername(e.target.value)}
                    disabled={newUserLoading}
                    required
                  />
                </div>

                <div className={styles.formField}>
                  <label className={styles.label}>{t('password')}</label>
                  <input 
                    type="password"
                    className={styles.input}
                    placeholder="••••••••"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    disabled={newUserLoading}
                    required
                  />
                </div>
              </div>

              <div className={styles.userFormRow}>
                <div className={styles.formField}>
                  <label className={styles.label}>{t('systemAccessRole')}</label>
                  <div className={styles.selectWrapper}>
                    <select 
                      className={styles.select}
                      value={newUserRole}
                      onChange={(e) => setNewUserRole(e.target.value as 'admin' | 'user')}
                      disabled={newUserLoading}
                      required
                    >
                      <option value="user">{t('userStandardAccess')}</option>
                      <option value="admin">{t('administratorFullControls')}</option>
                    </select>
                  </div>
                </div>

                <div className={styles.formField} style={{ justifyContent: 'flex-end' }}>
                  <button 
                    type="submit" 
                    className={styles.submitButton}
                    style={{ width: '100%', height: '38px', justifyContent: 'center' }}
                    disabled={newUserLoading || !newUsername.trim() || !newPassword.trim()}
                  >
                    {newUserLoading ? (
                      <>
                        <RotateCw size={14} className={styles.spinIcon} />
                        <span>{t('registering')}</span>
                      </>
                    ) : (
                      <>
                        <Plus size={14} />
                        <span>{t('addUserAccount')}</span>
                      </>
                    )}
                  </button>
                </div>
              </div>
            </form>

            {/* Section 3: Storage Space & Data Retention Policy */}
            <div className={styles.retentionSection}>
              <div className={styles.sectionHeader}>
                <h3>{lang === 'zh' ? '存储空间与数据保留策略' : 'Storage Space & Data Retention Policy'}</h3>
              </div>
              <p className={styles.sectionDescription} style={{ color: 'var(--text-dim)', fontSize: '0.85rem', marginBottom: '1rem', lineHeight: '1.4' }}>
                {lang === 'zh' 
                  ? '配置平台保留测试日志及报告的策略。执行物理清理将永久删除指定天数之前的运行日志和 HTML 报告目录，但会保留 SQLite 中的分析指标和运行结果数据，且已锁定的记录将被安全保护，不予清理。' 
                  : 'Configure the storage retention policy. Running cleanup will permanently delete physical run execution directories (logs and reports) older than the specified days. SQLite metadata and run results will be preserved, and locked/pinned runs will be protected from deletion.'}
              </p>
              
              <div className={styles.retentionFormRow}>
                <div className={styles.formField} style={{ flex: '1' }}>
                  <label className={styles.label}>{lang === 'zh' ? '保留天数' : 'Retention Days'}</label>
                  <input 
                    type="number"
                    min="1"
                    className={styles.input}
                    value={retentionDays}
                    onChange={(e) => setRetentionDays(Number(e.target.value))}
                    disabled={isCleaningStorage}
                  />
                </div>
                
                <div className={styles.formField} style={{ justifyContent: 'flex-end', flex: '1' }}>
                  <button 
                    type="button"
                    className={styles.cleanupButton}
                    disabled={isCleaningStorage || retentionDays <= 0}
                    onClick={async () => {
                      if (!confirm(lang === 'zh' ? `确认要物理清理所有非锁定且早于 ${retentionDays} 天的测试记录文件吗？此操作无法撤销。` : `Are you sure you want to clean up physical files of all unlocked runs older than ${retentionDays} days? This cannot be undone.`)) {
                        return;
                      }
                      setIsCleaningStorage(true);
                      try {
                        const resp = await fetch(`/runs/cleanup?retention_days=${retentionDays}`, {
                          method: 'POST',
                          headers: {
                            'Authorization': `Bearer ${token}`
                          }
                        });
                        const data = await resp.json();
                        if (resp.ok) {
                          alert(lang === 'zh' 
                            ? `清理成功！已清理 ${data.cleaned_count || 0} 个记录目录。` 
                            : `Cleanup successful! Purged ${data.cleaned_count || 0} runs directories.`);
                          fetchRuns();
                        } else {
                          alert(data.detail || 'Cleanup failed');
                        }
                      } catch (err) {
                        console.error('Error during cleanup:', err);
                        alert('Network error. Failed to run storage cleanup.');
                      } finally {
                        setIsCleaningStorage(false);
                      }
                    }}
                  >
                    {isCleaningStorage ? (
                      <>
                        <RotateCw size={14} className={styles.spinIcon} />
                        <span>{lang === 'zh' ? '清理中...' : 'Cleaning...'}</span>
                      </>
                    ) : (
                      <>
                        <Trash2 size={14} />
                        <span>{lang === 'zh' ? '立即执行物理清理' : 'Execute Storage Cleanup'}</span>
                      </>
                    )}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
