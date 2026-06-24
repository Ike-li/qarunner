import { useEffect, useState, useCallback, useRef } from 'react'
import { RotateCw } from 'lucide-react'
import styles from './App.module.css'
import type { Run, UserProfile, Profile, Schedule, TreeNode } from './types'
import { translations, type Lang, type TranslationKey } from './i18n'
import { LoginScreen } from './components/LoginScreen'
import { StatsCards } from './components/StatsCards'
import { useFileTreeSelection } from './hooks/useFileTreeSelection'
import { useTerminalView } from './hooks/useTerminalView'
import { TriggerRunModal } from './components/TriggerRunModal'
import { Header } from './components/Header'
import { ProjectSidebar } from './components/ProjectSidebar'
import { RunsTable } from './components/RunsTable'
import { RunDetailsDrawer } from './components/RunDetailsDrawer'
import { FullscreenTerminalOverlay } from './components/FullscreenTerminalOverlay'
import { FullscreenReportOverlay } from './components/FullscreenReportOverlay'

import { ScheduleModal } from './components/ScheduleModal'
import { UserManagementModal } from './components/UserManagementModal'

export default function App() {
  const [runs, setRuns] = useState<Run[]>([])
  const [tests, setTests] = useState<string[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedRunDetails, setSelectedRunDetails] = useState<Run | null>(null)
  const [detailsLoading, setDetailsLoading] = useState<boolean>(false)
  const terminalRef = useRef<HTMLDivElement>(null)
  const [isTriggerModalOpen, setIsTriggerModalOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  // Terminal/drawer UI preferences + log helpers (see hooks/useTerminalView)
  const {
    drawerTab,
    setDrawerTab,
    copySuccess,
    logLevelFilter,
    setLogLevelFilter,
    isDrawerExpanded,
    setIsDrawerExpanded,
    isTerminalHeightExpanded,
    setIsTerminalHeightExpanded,
    terminalFontSize,
    setTerminalFontSize,
    logSearchQuery,
    setLogSearchQuery,
    isTerminalFullscreen,
    setIsTerminalFullscreen,
    isReportFullscreen,
    setIsReportFullscreen,
    isWordWrapEnabled,

    setIsWordWrapEnabled,
    isAutoScrollEnabled,
    setIsAutoScrollEnabled,
    copyToClipboard,
    getFilteredLogs,
    renderFormattedLogs,
  } = useTerminalView()

  // Localization & Theme states
  const [lang, setLang] = useState<Lang>(() => (localStorage.getItem('qarunner_lang') as Lang) || 'en')
  const [theme, setTheme] = useState<'dark' | 'light'>(() => (localStorage.getItem('qarunner_theme') as 'dark' | 'light') || 'dark')

  const t = useCallback((key: TranslationKey) => {
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
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null)  // null = initial auth-cookie probe in flight
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
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [scannedFilesTree, setScannedFilesTree] = useState<TreeNode[]>([])
  const [scannedMarkers, setScannedMarkers] = useState<string[]>([])
  const {
    selectedFiles,
    setSelectedFiles,
    expandedFolders,
    setExpandedFolders,
    getNodeCheckState,
    handleToggleNode,
    toggleFolder,
  } = useFileTreeSelection()
  const [selectedMarkers, setSelectedMarkers] = useState<string[]>([])
  const [profileName, setProfileName] = useState('')
  const [profileDesc, setProfileDesc] = useState('')
  const [isSavingProfile, setIsSavingProfile] = useState(false)
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null)
  const [selectedProfileId, setSelectedProfileId] = useState<string>('')
  
  // Custom Environment Variables & Retention States
  const [envVars, setEnvVars] = useState<{ key: string; value: string }[]>([])
  const [retentionDays, setRetentionDays] = useState<number>(30)
  const [isCleaningStorage, setIsCleaningStorage] = useState<boolean>(false)
  const [streamedStdout, setStreamedStdout] = useState<string>('')
  const [isStreaming, setIsStreaming] = useState<boolean>(false)

  // Fullscreen terminal element ref (UI prefs live in useTerminalView)
  const fullscreenTerminalRef = useRef<HTMLDivElement>(null)


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
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [isScheduleModalOpen, setIsScheduleModalOpen] = useState(false)
  const [scheduleProfile, setScheduleProfile] = useState<Profile | null>(null)
  const [schedName, setSchedName] = useState('')
  const [schedExpression, setSchedExpression] = useState('')
  const [schedTimezone, setSchedTimezone] = useState('UTC')
  const [schedEnabled, setSchedEnabled] = useState(true)
  const [previewNextRuns, setPreviewNextRuns] = useState<string[]>([])
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [logFilterTab, setLogFilterTab] = useState<'All' | 'Manual' | 'Scheduled'>('All')

  // Reset all client-side session state. Used on user logout and on any 401
  // (the auth cookie is gone/expired, so fall back to the login screen).
  const clearSession = useCallback(() => {
    setIsAuthenticated(false)
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

  // Single fetch wrapper (FE-2). Always sends the HttpOnly auth cookie and never
  // a token header or URL param (SEC-6). A 401 means the session is gone, so we
  // clear it centrally — callers no longer duplicate 401 handling.
  const apiFetch = useCallback(
    async (path: string, opts: RequestInit = {}): Promise<Response> => {
      const resp = await globalThis.fetch(path, { ...opts, credentials: "include" })
      if (resp.status === 401) {
        clearSession()
      }
      return resp
    },
    [clearSession],
  )

  // User-initiated logout: the cookie is HttpOnly so JS can't clear it — ask the
  // server to expire it, then reset client state.
  const handleLogout = useCallback(async () => {
    try {
      await apiFetch("/auth/logout", { method: "POST" })
    } catch {
      // best-effort; clear client state regardless
    }
    clearSession()
  }, [apiFetch, clearSession])

  // Probe the session via the auth cookie and populate the current user.
  // Replaces the old token-based profile fetch; drives the isAuthenticated gate.
  const checkAuth = useCallback(async () => {
    try {
      const resp = await apiFetch('/auth/me')
      if (resp.ok) {
        setCurrentUser(await resp.json())
        setIsAuthenticated(true)
      } else {
        setIsAuthenticated(false)
      }
    } catch (err) {
      console.error('Error checking auth:', err)
      setIsAuthenticated(false)
    }
  }, [apiFetch])

  // Fetch runs list
  const fetchRuns = useCallback(async () => {
    try {
      const resp = await apiFetch('/runs')
      if (resp.ok) {
        const data = await resp.json()
        setRuns(data.runs)
      }
    } catch (err) {
      console.error('Error fetching runs:', err)
    } finally {
      setLoading(false)
    }
  }, [apiFetch])

  // Fetch a single run details (with stdout/stderr)
  const fetchSelectedRunDetails = useCallback(async (runId: string) => {
    setDetailsLoading(true)
    try {
      const resp = await apiFetch(`/runs/${runId}`)
      if (resp.ok) {
        const data = await resp.json()
        setSelectedRunDetails(data)
      }
    } catch (err) {
      console.error('Error fetching run details:', err)
    } finally {
      setDetailsLoading(false)
    }
  }, [apiFetch])  // Fetch saved test profiles
  const fetchProfiles = useCallback(async () => {
    try {
      const resp = await apiFetch('/profiles')
      if (resp.ok) {
        const data = await resp.json()
        setProfiles(data)
      }
    } catch (err) {
      console.error('Error fetching profiles:', err)
    }
  }, [apiFetch])

  // Fetch saved test schedules
  const fetchSchedules = useCallback(async () => {
    try {
      const resp = await apiFetch('/schedules')
      if (resp.ok) {
        const data = await resp.json()
        setSchedules(data)
      }
    } catch (err) {
      console.error('Error fetching schedules:', err)
    }
  }, [apiFetch])

  // Fetch schedule preview next 5 runs
  const fetchSchedulePreview = useCallback(async (expression: string, timezone: string) => {
    if (!expression) {
      setPreviewNextRuns([])
      setPreviewError(null)
      return
    }
    try {
      const resp = await apiFetch(`/schedules/preview?expression=${encodeURIComponent(expression)}&timezone=${encodeURIComponent(timezone)}`)
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
  }, [apiFetch])

  // Open schedule manager modal and populate fields
  const handleOpenScheduleModal = useCallback((profile: Profile) => {
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
    if (!scheduleProfile) return
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
      const resp = await apiFetch(url, {
        method,
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
      })


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
  }, [scheduleProfile, schedules, schedName, schedExpression, schedEnabled, schedTimezone, apiFetch, fetchSchedules])

  // Delete schedule configuration
  const handleDeleteSchedule = useCallback(async (scheduleId: string) => {
    if (!window.confirm(lang === 'zh' ? '确定要删除此定时调度吗？' : 'Are you sure you want to delete this schedule?')) {
      return
    }
    try {
      const resp = await apiFetch(`/schedules/${scheduleId}`, {
        method: 'DELETE'
      })
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
  }, [lang, scheduleProfile, schedules, apiFetch, fetchSchedules])

  // Fetch file tree and markers for the active test suite
  const fetchSuiteMetadata = useCallback(async (suiteName: string) => {
    if (!suiteName) return
    try {
      // 1. Fetch File Tree
      const treeResp = await apiFetch(`/tests/${encodeURIComponent(suiteName)}/tree`)
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
      const markersResp = await apiFetch(`/tests/${encodeURIComponent(suiteName)}/markers`)
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
  }, [apiFetch])

  // Handle direct single-click trigger of a profile from the sidebar
  const handleTriggerProfile = useCallback(async (profile: Profile) => {
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
      const resp = await apiFetch('/runs', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
      })


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
  }, [apiFetch, fetchRuns])

  // Handle deleting a saved execution profile
  const handleDeleteProfile = useCallback(async (profileId: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation()
    const confirmMsg = lang === 'zh' ? '确定要删除此执行方案吗？' : 'Are you sure you want to delete this profile?'
    if (!window.confirm(confirmMsg)) return

    try {
      const resp = await apiFetch(`/profiles/${profileId}`, {
        method: 'DELETE'
      })


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
  }, [apiFetch, lang, fetchProfiles, fetchSchedules, selectedProfileId])


  // Fetch tests directories
  const fetchTests = useCallback(async () => {
    try {
      const resp = await apiFetch('/tests')
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
  }, [apiFetch])

  // Admin: Fetch all registered users
  const fetchUsers = useCallback(async () => {
    if (!currentUser || currentUser.role !== 'admin') return
    setUsersLoading(true)
    try {
      const resp = await apiFetch('/users')
      if (resp.ok) {
        const data = await resp.json()
        setUsersList(data.users)
      }
    } catch (err) {
      console.error('Error fetching users:', err)
    } finally {
      setUsersLoading(false)
    }
  }, [currentUser, apiFetch])

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
      const resp = await apiFetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: loginUsername.trim(),
          password: loginPassword
        })
      })

      if (resp.ok) {
        // The server planted the HttpOnly auth cookie on this response; no token
        // is read or stored in JS (SEC-6). Populate auth state from the cookie.
        setLoginUsername('')
        setLoginPassword('')
        await checkAuth()
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
      const resp = await apiFetch('/users', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          username: newUsername.trim(),
          password: newPassword,
          role: newUserRole
        })
      })


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
      const resp = await apiFetch('/runs', {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
      })


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
      const resp = await apiFetch('/profiles', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
      })


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
    const run = runs.find(r => r.id === runId)
    if (!run) return
    const newLocked = !run.locked

    try {
      const resp = await apiFetch(`/runs/${runId}/lock`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ locked: newLocked })
      })


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
  }, [runs, selectedRunDetails, apiFetch])

  // Handle opening the launch modal in Edit Profile mode
  const handleOpenEditProfile = useCallback((profile: Profile) => {
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
      const resp = await apiFetch(`/profiles/${editingProfileId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
      })


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

  // Probe the auth cookie once on mount to learn whether we're already logged in.
  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  // Initial data loads once authenticated.
  useEffect(() => {
    if (isAuthenticated) {
      fetchRuns()
      fetchTests()
      fetchProfiles()
      fetchSchedules()
    }
  }, [isAuthenticated, fetchRuns, fetchTests, fetchProfiles, fetchSchedules])

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

  // Connect to EventSource for SSE live log streaming when a run is active.
  //
  // Deps are deliberately limited to [token, selectedRunId]. The previous
  // version also depended on `runs` and `selectedRunDetails`, which the polling
  // loop replaces every ~1.5s — so the effect tore down and rebuilt the
  // EventSource on every poll (constant reconnects + log flicker). Worse,
  // `onerror` called `fetchSelectedRunDetails`, mutating a dep and feeding the
  // teardown loop. We now read the freshest run state from refs to decide
  // whether to stream, and reconnect with a capped backoff so a flapping
  // connection can't spin. `fetchSelectedRunDetails` is a stable useCallback
  // keyed on [token, handleLogout], so it never churns mid-stream.
  useEffect(() => {
    if (!selectedRunId) {
      setStreamedStdout('')
      setIsStreaming(false)
      return
    }

    const findRun = (): Run | null | undefined =>
      runsRef.current.find(r => r.id === selectedRunId) || selectedRunDetailsRef.current
    const isActive = (r: Run | null | undefined): boolean =>
      r?.status === 'running' || r?.status === 'queued'

    if (!isActive(findRun())) {
      // Not active: nothing to stream. Keep streamedStdout so we don't flash a
      // blank screen while the completed logs are fetched.
      setIsStreaming(false)
      return
    }

    setStreamedStdout('')
    setIsStreaming(true)

    let eventSource: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let reconnectAttempts = 0
    let disposed = false

    const connect = () => {
      if (disposed) return
      eventSource = new EventSource(
        `/runs/${selectedRunId}/stream`
      )

      eventSource.onopen = () => {
        reconnectAttempts = 0
      }

      eventSource.onmessage = (event) => {
        setStreamedStdout(prev => prev + event.data + '\n')
      }

      eventSource.onerror = (err) => {
        console.error('SSE connection error:', err)
        eventSource?.close()
        eventSource = null
        if (disposed) return

        // The backend closes the stream when the run finishes, which surfaces
        // here as an error. If the run is no longer active, stop and fetch the
        // completed logs once — do NOT reconnect (that would spin forever).
        if (!isActive(findRun())) {
          setIsStreaming(false)
          fetchSelectedRunDetails(selectedRunId)
          return
        }

        // Still active: a transient drop. Reconnect with capped exponential
        // backoff (1s, 2s, 4s … max 15s) instead of hammering the endpoint.
        const delay = Math.min(1000 * 2 ** reconnectAttempts, 15000)
        reconnectAttempts += 1
        reconnectTimer = setTimeout(connect, delay)
      }
    }

    connect()

    return () => {
      disposed = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      eventSource?.close()
      setIsStreaming(false)
    }
  }, [selectedRunId, fetchSelectedRunDetails])

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
    }
  }, [selectedRunId])



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
    if (!isAuthenticated) return

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
  }, [isAuthenticated, fetchRuns, fetchSelectedRunDetails])


  // Utility formatting helpers (formatDuration lives in ./logUtils, unit-tested)
  const formatDate = (isoStr: string | null) => {
    if (!isoStr) return '-'
    const d = new Date(isoStr)
    return d.toLocaleString()
  }

  // Derived aggregates for Dashboard Header
  const totalRuns = runs.length
  const completedRuns = runs.filter(r => r.status === 'completed')
  const passedRunsCount = completedRuns.filter(r => r.passed).length
  const overallSuccessRate = completedRuns.length > 0 
    ? ((passedRunsCount / completedRuns.length) * 100).toFixed(0) 
    : '0'
  const activeRunsCount = runs.filter(r => r.status === 'queued' || r.status === 'running').length
  const failedRunsCount = runs.filter(r => r.status === 'failed' || (r.status === 'completed' && !r.passed)).length

  // Rendering 0: brief loader while the initial auth-cookie probe is in flight,
  // so an already-logged-in user doesn't flash the login screen on reload.
  if (isAuthenticated === null) {
    return (
      <div className={styles.loginOverlay}>
        <div className={styles.ambientGlow1}></div>
        <div className={styles.ambientGlow2}></div>
        <RotateCw size={32} className={styles.spinIcon} />
      </div>
    )
  }

  // Rendering 1: Login full-screen Glassmorphism if not authenticated
  if (!isAuthenticated) {
    return (
      <LoginScreen
        t={t}
        theme={theme}
        setTheme={setTheme}
        lang={lang}
        setLang={setLang}
        loginError={loginError}
        loginUsername={loginUsername}
        setLoginUsername={setLoginUsername}
        loginPassword={loginPassword}
        setLoginPassword={setLoginPassword}
        loginLoading={loginLoading}
        onSubmit={handleLoginSubmit}
      />
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

  const filteredStdout = selectedRun?.stdout ? getFilteredLogs(selectedRun.stdout) : ''
  const filteredStderr = selectedRun?.stderr ? getFilteredLogs(selectedRun.stderr) : ''
  const filteredStreamed = streamedStdout ? getFilteredLogs(streamedStdout) : ''

  // Aggregation helper for saved profiles statistics in the sidebar
  const getProfileRunStats = (profile: Profile) => {
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

  // Rendering 2: Full Dashboard View for Authenticated Users
  return (
    <div className={styles.appContainer}>
      {/* Background aesthetics */}
      <div className={styles.ambientGlow1}></div>
      <div className={styles.ambientGlow2}></div>

      {/* Main navigation / header */}
      <Header
        t={t}
        currentUser={currentUser}
        theme={theme}
        setTheme={setTheme}
        lang={lang}
        setLang={setLang}
        fetchUsers={fetchUsers}
        setIsUserModalOpen={setIsUserModalOpen}
        fetchTests={fetchTests}
        setIsTriggerModalOpen={setIsTriggerModalOpen}
        handleLogout={handleLogout}
      />

      {/* Stats Summary Cards */}
      <StatsCards
        t={t}
        totalRuns={totalRuns}
        overallSuccessRate={overallSuccessRate}
        failedRunsCount={failedRunsCount}
        activeRunsCount={activeRunsCount}
      />

      {/* Main Table section */}
      <main className={styles.mainContent}>
        {/* Left Column: Project Sidebar */}
        <ProjectSidebar
          t={t}
          lang={lang}
          runs={runs}
          tests={tests}
          profiles={profiles}
          schedules={schedules}
          selectedSuiteFilter={selectedSuiteFilter}
          setSelectedSuiteFilter={setSelectedSuiteFilter}
          setTestsPath={setTestsPath}
          setIsTriggerModalOpen={setIsTriggerModalOpen}
          setSelectedRunId={setSelectedRunId}
          getProfileRunStats={getProfileRunStats}
          handleTriggerProfile={handleTriggerProfile}
          handleOpenEditProfile={handleOpenEditProfile}
          handleOpenScheduleModal={handleOpenScheduleModal}
          handleDeleteProfile={handleDeleteProfile}
        />

        <RunsTable
          t={t}
          lang={lang}
          runs={runs}
          filteredRuns={filteredRuns}
          loading={loading}
          logFilterTab={logFilterTab}
          setLogFilterTab={setLogFilterTab}
          selectedRunId={selectedRunId}
          setSelectedRunId={setSelectedRunId}
          setIsTriggerModalOpen={setIsTriggerModalOpen}
          fetchRuns={fetchRuns}
          handleToggleLock={handleToggleLock}
          formatDate={formatDate}
        />
      </main>

      {/* Drawer: Detailed Run Information */}
      <RunDetailsDrawer
        t={t}
        lang={lang}
        selectedRun={selectedRun}
        selectedRunDetails={selectedRunDetails}
        streamedStdout={streamedStdout}
        isStreaming={isStreaming}
        filteredStdout={filteredStdout}
        filteredStderr={filteredStderr}
        filteredStreamed={filteredStreamed}
        detailsLoading={detailsLoading}
        drawerTab={drawerTab}
        setDrawerTab={setDrawerTab}
        isDrawerExpanded={isDrawerExpanded}
        setIsDrawerExpanded={setIsDrawerExpanded}
        setSelectedRunId={setSelectedRunId}
        logSearchQuery={logSearchQuery}
        setLogSearchQuery={setLogSearchQuery}
        logLevelFilter={logLevelFilter}
        setLogLevelFilter={setLogLevelFilter}
        terminalFontSize={terminalFontSize}
        setTerminalFontSize={setTerminalFontSize}
        isTerminalHeightExpanded={isTerminalHeightExpanded}
        setIsTerminalHeightExpanded={setIsTerminalHeightExpanded}
        setIsTerminalFullscreen={setIsTerminalFullscreen}
        setIsReportFullscreen={setIsReportFullscreen}

        copySuccess={copySuccess}
        copyToClipboard={copyToClipboard}
        downloadLogs={downloadLogs}
        renderFormattedLogs={renderFormattedLogs}
         formatDate={formatDate}
        terminalRef={terminalRef}
      />

      {/* Fullscreen Terminal Overlay */}
      {isTerminalFullscreen && selectedRun && (

        <FullscreenTerminalOverlay
          t={t}
          lang={lang}
          selectedRun={selectedRun}
          selectedRunDetails={selectedRunDetails}
          streamedStdout={streamedStdout}
          isStreaming={isStreaming}
          filteredStdout={filteredStdout}
          filteredStderr={filteredStderr}
          filteredStreamed={filteredStreamed}
          detailsLoading={detailsLoading}
          logSearchQuery={logSearchQuery}
          setLogSearchQuery={setLogSearchQuery}
          logLevelFilter={logLevelFilter}
          setLogLevelFilter={setLogLevelFilter}
          terminalFontSize={terminalFontSize}
          setTerminalFontSize={setTerminalFontSize}
          isWordWrapEnabled={isWordWrapEnabled}
          setIsWordWrapEnabled={setIsWordWrapEnabled}
          isAutoScrollEnabled={isAutoScrollEnabled}
          setIsAutoScrollEnabled={setIsAutoScrollEnabled}
          setIsTerminalFullscreen={setIsTerminalFullscreen}
          copySuccess={copySuccess}
          copyToClipboard={copyToClipboard}
          downloadLogs={downloadLogs}
          renderFormattedLogs={renderFormattedLogs}
          fullscreenTerminalRef={fullscreenTerminalRef}
        />
      )}

      {/* Fullscreen Report Overlay */}
      {isReportFullscreen && selectedRun && (
        <FullscreenReportOverlay
          lang={lang}
          selectedRun={selectedRun}
          setIsReportFullscreen={setIsReportFullscreen}
        />
      )}


      {/* Modal: Trigger Run */}
      {isTriggerModalOpen && (
        <TriggerRunModal
          t={t}
          lang={lang}
          editingProfileId={editingProfileId}
          formError={formError}
          tests={tests}
          testsPath={testsPath}
          setTestsPath={setTestsPath}
          selectedProfileId={selectedProfileId}
          setSelectedProfileId={setSelectedProfileId}
          profiles={profiles}
          setSelectedFiles={setSelectedFiles}
          scannedFilesTree={scannedFilesTree}
          expandedFolders={expandedFolders}
          toggleFolder={toggleFolder}
          getNodeCheckState={getNodeCheckState}
          handleToggleNode={handleToggleNode}
          scannedMarkers={scannedMarkers}
          selectedMarkers={selectedMarkers}
          setSelectedMarkers={setSelectedMarkers}
          executorMode={executorMode}
          setExecutorMode={setExecutorMode}
          customArgs={customArgs}
          setCustomArgs={setCustomArgs}
          envVars={envVars}
          setEnvVars={setEnvVars}
          timeoutSeconds={timeoutSeconds}
          setTimeoutSeconds={setTimeoutSeconds}
          allureEnabled={allureEnabled}
          setAllureEnabled={setAllureEnabled}
          isSavingProfile={isSavingProfile}
          setIsSavingProfile={setIsSavingProfile}
          profileName={profileName}
          setProfileName={setProfileName}
          profileDesc={profileDesc}
          setProfileDesc={setProfileDesc}
          isSubmitting={isSubmitting}
          onClose={() => setIsTriggerModalOpen(false)}
          onTriggerRun={handleTriggerRun}
          onUpdateProfile={handleUpdateProfile}
          onSaveProfile={handleSaveProfile}
          onDeleteProfile={handleDeleteProfile}
        />
      )}

      {/* Modal: Schedule Manager */}
      {isScheduleModalOpen && scheduleProfile && (
        <ScheduleModal
          t={t}
          lang={lang}
          profile={scheduleProfile}
          schedName={schedName}
          setSchedName={setSchedName}
          schedExpression={schedExpression}
          setSchedExpression={setSchedExpression}
          schedTimezone={schedTimezone}
          setSchedTimezone={setSchedTimezone}
          schedEnabled={schedEnabled}
          setSchedEnabled={setSchedEnabled}
          previewError={previewError}
          previewNextRuns={previewNextRuns}
          schedules={schedules}
          onClose={() => setIsScheduleModalOpen(false)}
          onSave={handleSaveSchedule}
          onDelete={handleDeleteSchedule}
        />
      )}

      {/* Modal: Admin User Management */}
      {isUserModalOpen && currentUser?.role === 'admin' && (
        <UserManagementModal
          t={t}
          lang={lang}
          usersLoading={usersLoading}
          usersList={usersList}
          formatDate={formatDate}
          onClose={() => setIsUserModalOpen(false)}
          onCreateUser={handleCreateUserSubmit}
          newUserError={newUserError}
          newUsername={newUsername}
          setNewUsername={setNewUsername}
          newPassword={newPassword}
          setNewPassword={setNewPassword}
          newUserRole={newUserRole}
          setNewUserRole={setNewUserRole}
          newUserLoading={newUserLoading}
          retentionDays={retentionDays}
          setRetentionDays={setRetentionDays}
          isCleaningStorage={isCleaningStorage}
          setIsCleaningStorage={setIsCleaningStorage}
          apiFetch={apiFetch}
          fetchRuns={fetchRuns}
        />
      )}
    </div>
  )
}
