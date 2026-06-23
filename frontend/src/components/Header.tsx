import { Activity, LogOut, Moon, Play, Sun, Users } from 'lucide-react'
import styles from '../App.module.css'
import type { Lang, TranslationKey } from '../i18n'
import type { UserProfile } from '../types'

interface HeaderProps {
  t: (key: TranslationKey) => string
  currentUser: UserProfile | null
  theme: 'dark' | 'light'
  setTheme: (value: 'dark' | 'light') => void
  lang: Lang
  setLang: (value: Lang) => void
  fetchUsers: () => void
  setIsUserModalOpen: (value: boolean) => void
  fetchTests: () => void
  setIsTriggerModalOpen: (value: boolean) => void
  handleLogout: () => void
}

/** Top navigation bar: logo, user capsule, admin/theme/lang toggles,
 *  trigger-run button, logout. All actions are passed in from App. */
export function Header({
  t,
  currentUser,
  theme,
  setTheme,
  lang,
  setLang,
  fetchUsers,
  setIsUserModalOpen,
  fetchTests,
  setIsTriggerModalOpen,
  handleLogout,
}: HeaderProps) {
  return (
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
              <span className={styles.profileUsername} data-testid="profile-username">{currentUser.username}</span>
              <span className={`${styles.profileRoleTag} ${styles[`profileRole_${currentUser.role}`]}`} data-testid="profile-role">
                {currentUser.role}
              </span>
            </div>
          </div>
        )}

        {/* Admin User Management Button */}
        {currentUser?.role === 'admin' && (
          <button
            className={styles.manageUsersButton}
            data-testid="open-users-button"
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
          aria-label={theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
        >
          {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
        </button>

        {/* Language Toggle */}
        <button
          className={styles.actionIconButton}
          onClick={() => setLang(lang === 'en' ? 'zh' : 'en')}
          title={lang === 'en' ? '切换为中文' : 'Switch to English'}
          aria-label={lang === 'en' ? '切换为中文' : 'Switch to English'}
        >
          <span className={styles.langText}>{lang === 'en' ? 'ZH' : 'EN'}</span>
        </button>

        <button
          className={styles.triggerButton}
          data-testid="open-trigger-button"
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
          aria-label={t('signOut')}
        >
          <LogOut size={18} />
        </button>
      </div>
    </header>
  )
}
