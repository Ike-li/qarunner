import type { FormEvent } from 'react'
import { AlertTriangle, Plus, RotateCw, Trash2, Users, X } from 'lucide-react'
import styles from '../App.module.css'
import { useDialogA11y } from '../hooks/useDialogA11y'
import type { Lang, TranslationKey } from '../i18n'
import type { UserProfile } from '../types'

interface UserManagementModalProps {
  t: (key: TranslationKey) => string
  lang: Lang
  usersLoading: boolean
  usersList: UserProfile[]
  formatDate: (iso: string) => string
  onClose: () => void
  onCreateUser: (e: FormEvent) => void
  newUserError: string | null
  newUsername: string
  setNewUsername: (value: string) => void
  newPassword: string
  setNewPassword: (value: string) => void
  newUserRole: 'admin' | 'user'
  setNewUserRole: (value: 'admin' | 'user') => void
  newUserLoading: boolean
  retentionDays: number
  setRetentionDays: (value: number) => void
  isCleaningStorage: boolean
  setIsCleaningStorage: (value: boolean) => void
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>
  fetchRuns: () => void
}

/** Admin-only panel: user directory, register-new-user form, and the storage
 *  retention / physical cleanup control. */
export function UserManagementModal({
  t,
  lang,
  usersLoading,
  usersList,
  formatDate,
  onClose,
  onCreateUser,
  newUserError,
  newUsername,
  setNewUsername,
  newPassword,
  setNewPassword,
  newUserRole,
  setNewUserRole,
  newUserLoading,
  retentionDays,
  setRetentionDays,
  isCleaningStorage,
  setIsCleaningStorage,
  apiFetch,
  fetchRuns,
}: UserManagementModalProps) {
  const dialogRef = useDialogA11y({ isOpen: true, onClose })
  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div
        ref={dialogRef}
        className={styles.modal}
        style={{ width: '640px' }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="user-modal-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <div className={styles.modalTitleGroup}>
            <Users size={20} className={styles.iconAccent} />
            <h2 id="user-modal-title">{t('userManagementTitle')}</h2>
          </div>
          <button className={styles.modalCloseButton} onClick={onClose} aria-label={lang === 'zh' ? '关闭' : 'Close'}>
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
        <form onSubmit={onCreateUser} className={styles.userCreateSection}>
          <div className={styles.sectionHeader}>
            <h3>{t('registerNewUser')}</h3>
          </div>

          {newUserError && (
            <div className={styles.formErrorAlert} style={{ margin: 0 }} role="alert">
              <AlertTriangle size={16} aria-hidden="true" />
              <span>{t(newUserError as TranslationKey) || newUserError}</span>
            </div>
          )}

          <div className={styles.userFormRow}>
            <div className={styles.formField}>
              <label className={styles.label} htmlFor="user-new-username">{t('username')}</label>
              <input
                id="user-new-username"
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
              <label className={styles.label} htmlFor="user-new-password">{t('password')}</label>
              <input
                id="user-new-password"
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
              <label className={styles.label} htmlFor="user-new-role">{t('systemAccessRole')}</label>
              <div className={styles.selectWrapper}>
                <select
                  id="user-new-role"
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
              <label className={styles.label} htmlFor="user-retention-days">{lang === 'zh' ? '保留天数' : 'Retention Days'}</label>
              <input
                id="user-retention-days"
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
                    const resp = await apiFetch(`/runs/cleanup?retention_days=${retentionDays}`, {
                      method: 'POST'
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
  )
}
