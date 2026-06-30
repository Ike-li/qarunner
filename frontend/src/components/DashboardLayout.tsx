import styles from '../App.module.css'
import { Header } from './Header'
import { StatsCards } from './StatsCards'
import { SuiteTrend } from './SuiteTrend'
import { ProjectSidebar } from './ProjectSidebar'
import { RunsTable } from './RunsTable'
import { RunDetailsDrawer } from './RunDetailsDrawer'
import { FullscreenTerminalOverlay } from './FullscreenTerminalOverlay'
import { FullscreenReportOverlay } from './FullscreenReportOverlay'
import { TriggerRunModal } from './TriggerRunModal'
import { AddSuiteModal } from './AddSuiteModal'
import { ScheduleModal } from './ScheduleModal'
import { UserManagementModal } from './UserManagementModal'
import { useDashboard } from '../hooks/DashboardContext'

/** The authenticated dashboard shell — pure layout composition. */
export function DashboardLayout() {
  const d = useDashboard()

  return (
    <div className={styles.appContainer}>
      <div className={styles.ambientGlow1} />
      <div className={styles.ambientGlow2} />

      <Header />
      <StatsCards />
      <SuiteTrend />

      <main className={styles.mainContent}>
        <ProjectSidebar />
        <RunsTable />
      </main>

      <RunDetailsDrawer />

      {d.terminal.isTerminalFullscreen && d.runs.selectedRun && (
        <FullscreenTerminalOverlay />
      )}

      {d.terminal.isReportFullscreen && d.runs.selectedRun && (
        <FullscreenReportOverlay />
      )}

      {d.isTriggerModalOpen && <TriggerRunModal />}

      <AddSuiteModal
        isOpen={d.isAddSuiteModalOpen}
        onClose={() => d.setIsAddSuiteModalOpen(false)}
      />

      {d.schedules.isScheduleModalOpen && d.schedules.scheduleProfile && (
        <ScheduleModal />
      )}

      {d.isUserModalOpen && d.auth.currentUser?.role === 'admin' && (
        <UserManagementModal />
      )}
    </div>
  )
}
