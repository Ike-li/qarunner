import { createContext, useContext, useEffect, useCallback, useRef, useState } from 'react'
import { type Lang, type TranslationKey, translations } from '../i18n'
import { useApi } from './useApi'
import { useAuth } from './useAuth'
import { useRuns } from './useRuns'
import { useSchedules } from './useSchedules'
import { useProfiles } from './useProfiles'
import { useSuites } from './useSuites'
import { useUsers } from './useUsers'
import { useTriggerForm } from './useTriggerForm'
import { useTerminalView } from './useTerminalView'
import { useFileTreeSelection } from './useFileTreeSelection'

// ── Types ──────────────────────────────────────────────────────────────────

interface DashboardCtx {
  // Foundational
  apiFetch: ReturnType<typeof useApi>['apiFetch']
  handleLogout: ReturnType<typeof useApi>['handleLogout']
  lang: Lang
  setLang: (l: Lang) => void
  theme: 'dark' | 'light'
  setTheme: (t: 'dark' | 'light') => void
  t: (key: TranslationKey) => string
  // Domain hooks
  auth: ReturnType<typeof useAuth>
  runs: ReturnType<typeof useRuns>
  profiles: ReturnType<typeof useProfiles>
  schedules: ReturnType<typeof useSchedules>
  suites: ReturnType<typeof useSuites>
  users: ReturnType<typeof useUsers>
  form: ReturnType<typeof useTriggerForm>
  terminal: ReturnType<typeof useTerminalView>
  // File-tree selection
  selectedFiles: string[]
  setSelectedFiles: (files: string[]) => void
  selectedMarkers: string[]
  setSelectedMarkers: React.Dispatch<React.SetStateAction<string[]>>
  expandedFolders: string[]
  setExpandedFolders: (folders: string[]) => void
  // Modal/filter state
  isTriggerModalOpen: boolean
  setIsTriggerModalOpen: (v: boolean) => void
  isAddSuiteModalOpen: boolean
  setIsAddSuiteModalOpen: (v: boolean) => void
  isUserModalOpen: boolean
  setIsUserModalOpen: (v: boolean) => void
  selectedSuiteFilter: string | null
  setSelectedSuiteFilter: (v: string | null) => void
  logFilterTab: 'All' | 'Manual' | 'Scheduled'
  setLogFilterTab: (v: 'All' | 'Manual' | 'Scheduled') => void
  searchRunId: string
  setSearchRunId: (v: string) => void
  filterStatus: string
  setFilterStatus: (v: string) => void
  filterEngine: string
  setFilterEngine: (v: string) => void
  filterOwner: string
  setFilterOwner: (v: string) => void
  // Refs
  terminalRef: React.RefObject<HTMLDivElement>
  fullscreenTerminalRef: React.RefObject<HTMLDivElement>
  // Session
  clearSession: () => void
}

const Ctx = createContext<DashboardCtx | null>(null)

export function useDashboard(): DashboardCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useDashboard must be used inside <DashboardProvider>')
  return ctx
}

// ── Provider ───────────────────────────────────────────────────────────────

export function DashboardProvider({
  children,
  lang,
  setLang,
  theme,
  setTheme,
}: {
  children: React.ReactNode
  lang: Lang
  setLang: (l: Lang) => void
  theme: 'dark' | 'light'
  setTheme: (t: 'dark' | 'light') => void
}) {
  const t = useCallback((key: TranslationKey) => translations[lang][key] || key, [lang])

  // UI state
  const [isTriggerModalOpen, setIsTriggerModalOpen] = useState(false)
  const [isAddSuiteModalOpen, setIsAddSuiteModalOpen] = useState(false)
  const [isUserModalOpen, setIsUserModalOpen] = useState(false)
  const [selectedSuiteFilter, setSelectedSuiteFilter] = useState<string | null>(null)
  const [logFilterTab, setLogFilterTab] = useState<'All' | 'Manual' | 'Scheduled'>('All')
  const [searchRunId, setSearchRunId] = useState('')
  const [filterStatus, setFilterStatus] = useState('ALL')
  const [filterEngine, setFilterEngine] = useState('ALL')
  const [filterOwner, setFilterOwner] = useState('ALL')
  const { selectedFiles, setSelectedFiles, expandedFolders, setExpandedFolders } =
    useFileTreeSelection()
  const [selectedMarkers, setSelectedMarkers] = useState<string[]>([])

  // Session reset
  const resetFnsRef = useRef<Array<() => void>>([])
  const registerReset = useCallback((fn: () => void) => {
    resetFnsRef.current.push(fn)
    return () => { resetFnsRef.current = resetFnsRef.current.filter((f) => f !== fn) }
  }, [])
  const clearSession = useCallback(() => {
    for (const fn of resetFnsRef.current) fn()
    setSelectedSuiteFilter(null)
    setIsTriggerModalOpen(false)
    setIsAddSuiteModalOpen(false)
    setIsUserModalOpen(false)
    setLogFilterTab('All')
    setSearchRunId('')
    setFilterStatus('ALL')
    setFilterEngine('ALL')
    setFilterOwner('ALL')
    setSelectedFiles([])
    setSelectedMarkers([])
    setExpandedFolders([])
  }, [setExpandedFolders, setSelectedFiles])

  // Domain hooks
  const { apiFetch, handleLogout } = useApi(clearSession)
  const auth = useAuth({ apiFetch })
  const runs = useRuns({ apiFetch, enabled: auth.isAuthenticated === true })
  const profiles = useProfiles({
    apiFetch, enabled: auth.isAuthenticated === true, lang,
    onProfileChanged: () => schedules.fetchSchedules(),
  })
  const schedules = useSchedules({ apiFetch, enabled: auth.isAuthenticated === true, lang })
  const suites = useSuites({ apiFetch, enabled: auth.isAuthenticated === true, lang })
  const users = useUsers({ apiFetch, currentUser: auth.currentUser })
  const form = useTriggerForm({ apiFetch })
  const terminal = useTerminalView()
  const terminalRef = useRef<HTMLDivElement>(null)
  const fullscreenTerminalRef = useRef<HTMLDivElement>(null)

  // Register reset fns
  useEffect(() => registerReset(auth._reset), [registerReset, auth._reset])
  useEffect(() => registerReset(runs._reset), [registerReset, runs._reset])
  useEffect(() => registerReset(profiles._reset), [registerReset, profiles._reset])
  useEffect(() => registerReset(schedules._reset), [registerReset, schedules._reset])
  useEffect(() => registerReset(suites._reset), [registerReset, suites._reset])
  useEffect(() => registerReset(users._reset), [registerReset, users._reset])
  useEffect(() => registerReset(form.resetForm), [registerReset, form.resetForm])

  // Effects
  useEffect(() => { auth.probeAuth() }, []) // eslint-disable-line

  useEffect(() => {
    if (form.testsPath) {
      suites.fetchSuiteMetadata(form.testsPath)
      setSelectedFiles([])
      setSelectedMarkers([])
      form.setSelectedProfileId('')
      form.setProfileName('')
      form.setProfileDesc('')
      form.setIsSavingProfile(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.testsPath])

  useEffect(() => {
    if (runs.selectedRunId) {
      terminal.setDrawerTab('logs')
      terminal.setLogSearchQuery('')
      terminal.setIsTerminalFullscreen(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs.selectedRunId])

  useEffect(() => {
    if (terminal.isAutoScrollEnabled) {
      if (terminalRef.current) terminalRef.current.scrollTop = terminalRef.current.scrollHeight
      if (fullscreenTerminalRef.current)
        fullscreenTerminalRef.current.scrollTop = fullscreenTerminalRef.current.scrollHeight
    }
  }, [
    runs.selectedRun?.stdout, runs.selectedRun?.stderr, runs.streamedStdout,
    runs.isStreaming, terminal.drawerTab, terminal.logSearchQuery,
    terminal.isAutoScrollEnabled, terminal.isTerminalFullscreen,
  ])

  useEffect(() => {
    if (!isTriggerModalOpen) {
      form.setEditingProfileId(null)
      form.setIsSavingProfile(false)
      form.setProfileName('')
      form.setProfileDesc('')
      form.setEnvVars([])
    }
    // `form` is a fresh object every render and `setEnvVars([])` always makes a
    // new array, so keeping `form` in the deps fired this effect every render →
    // setState → re-render → infinite loop ("Maximum update depth exceeded"),
    // which froze event handling (e.g. the run-details drawer wouldn't close).
    // The setters are stable useState fns; depend only on the modal-open toggle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isTriggerModalOpen])

  return (
    <Ctx.Provider
      value={{
        apiFetch, handleLogout, lang, setLang, theme, setTheme, t,
        auth, runs, profiles, schedules, suites, users, form, terminal,
        selectedFiles, setSelectedFiles,
        selectedMarkers, setSelectedMarkers,
        expandedFolders, setExpandedFolders,
        isTriggerModalOpen, setIsTriggerModalOpen,
        isAddSuiteModalOpen, setIsAddSuiteModalOpen,
        isUserModalOpen, setIsUserModalOpen,
        selectedSuiteFilter, setSelectedSuiteFilter,
        logFilterTab, setLogFilterTab,
        searchRunId, setSearchRunId,
        filterStatus, setFilterStatus,
        filterEngine, setFilterEngine,
        filterOwner, setFilterOwner,
        terminalRef, fullscreenTerminalRef,
        clearSession,
      }}
    >
      {children}
    </Ctx.Provider>
  )
}
