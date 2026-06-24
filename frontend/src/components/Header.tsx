import { Activity, LogOut, Moon, Play, Sun, Users } from 'lucide-react'
import { Button, Avatar, Tag, Tooltip, Space } from '@douyinfe/semi-ui'
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
          <Activity />
        </div>
        <div className={styles.logoText}>
          <h1 style={{ margin: 0, fontSize: '18px', fontWeight: 700, color: 'var(--semi-color-text-0)' }}>{t('platformTitle')}</h1>
          <span style={{ fontSize: '11px', color: 'var(--semi-color-text-2)' }}>{t('platformSubtitleFull')}</span>
        </div>
      </div>

      <div className={styles.headerActions} style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        {/* User profile capsule */}
        {currentUser && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '4px 10px', borderRadius: '8px', backgroundColor: 'var(--semi-color-fill-0)', border: '1px solid var(--semi-color-border)' }}>
            <Avatar size="small" color="grey" style={{ fontSize: '11px', fontWeight: 600 }}>
              {currentUser.username.substring(0, 2).toUpperCase()}
            </Avatar>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', lineHeight: 1.2 }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--semi-color-text-0)' }} data-testid="profile-username">
                {currentUser.username}
              </span>
              <Tag
                size="small"
                color="grey"
                style={{ fontSize: '10px', height: '16px', padding: '0 4px', marginTop: '2px', textTransform: 'capitalize' }}
                data-testid="profile-role"
              >
                {currentUser.role}
              </Tag>
            </div>
          </div>
        )}

        <Space spacing={8}>
          {/* Admin User Management Button */}
          {currentUser?.role === 'admin' && (
            <Tooltip content={t('managePlatformUsers')}>
              <Button
                theme="light"
                type="primary"
                data-testid="open-users-button"
                onClick={() => {
                  fetchUsers()
                  setIsUserModalOpen(true)
                }}
                icon={<Users size={16} />}
              >
                {t('users')}
              </Button>
            </Tooltip>
          )}

          {/* Theme Toggle */}
          <Tooltip content={theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}>
            <Button
              theme="borderless"
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              icon={theme === 'dark' ? <Moon size={16} /> : <Sun size={16} />}
              style={{ color: 'var(--semi-color-text-1)' }}
            />
          </Tooltip>

          {/* Language Toggle */}
          <Tooltip content={lang === 'en' ? '切换为中文' : 'Switch to English'}>
            <Button
              theme="borderless"
              onClick={() => setLang(lang === 'en' ? 'zh' : 'en')}
              style={{ fontWeight: 600, color: 'var(--semi-color-text-1)' }}
            >
              {lang === 'en' ? '中文' : 'EN'}
            </Button>
          </Tooltip>

          {/* Trigger Run */}
          <Button
            type="primary"
            theme="solid"
            data-testid="open-trigger-button"
            icon={<Play size={16} fill="currentColor" />}
            onClick={() => {
              fetchTests()
              setIsTriggerModalOpen(true)
            }}
          >
            {t('triggerRun')}
          </Button>

          {/* Logout Button */}
          <Tooltip content={t('signOut')}>
            <Button
              theme="borderless"
              onClick={handleLogout}
              icon={<LogOut size={16} />}
              style={{ color: 'var(--semi-color-text-1)' }}
            />
          </Tooltip>
        </Space>
      </div>
    </header>
  )
}

