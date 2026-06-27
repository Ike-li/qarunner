import { Activity, LogOut, Moon, Play, Sun, Users } from 'lucide-react'
import { Button, Avatar, Tag, Tooltip, Space } from '@douyinfe/semi-ui'
import styles from '../App.module.css'
import { useDashboard } from '../hooks/DashboardContext'

/** Top navigation bar — reads everything from DashboardContext. */
export function Header() {
  const d = useDashboard()

  return (
    <header className={styles.header}>
      <div className={styles.logoGroup}>
        <div className={styles.logoIcon}><Activity /></div>
        <div className={styles.logoText}>
          <h1 style={{ margin: 0, fontSize: '18px', fontWeight: 700, color: 'var(--semi-color-text-0)' }}>{d.t('platformTitle')}</h1>
          <span style={{ fontSize: '11px', color: 'var(--semi-color-text-2)' }}>{d.t('platformSubtitleFull')}</span>
        </div>
      </div>

      <div className={styles.headerActions} style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        {d.auth.currentUser && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '4px 10px', borderRadius: '8px', backgroundColor: 'var(--semi-color-fill-0)', border: '1px solid var(--semi-color-border)' }}>
            <Avatar size="small" color="grey" style={{ fontSize: '11px', fontWeight: 600 }}>
              {d.auth.currentUser.username.substring(0, 2).toUpperCase()}
            </Avatar>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', lineHeight: 1.2 }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--semi-color-text-0)' }} data-testid="profile-username">{d.auth.currentUser.username}</span>
              <Tag size="small" color="grey" style={{ fontSize: '10px', height: '16px', padding: '0 4px', marginTop: '2px', textTransform: 'capitalize' }} data-testid="profile-role">{d.auth.currentUser.role}</Tag>
            </div>
          </div>
        )}

        <Space spacing={8}>
          {d.auth.currentUser?.role === 'admin' && (
            <Tooltip content={d.t('managePlatformUsers')}>
              <Button theme="light" type="primary" data-testid="open-users-button"
                onClick={() => { d.users.fetchUsers(); d.setIsUserModalOpen(true) }}
                icon={<Users size={16} />}>{d.t('users')}</Button>
            </Tooltip>
          )}

          <Tooltip content={d.theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}>
            <Button theme="borderless"
              onClick={() => d.setTheme(d.theme === 'dark' ? 'light' : 'dark')}
              icon={d.theme === 'dark' ? <Moon size={16} /> : <Sun size={16} />}
              style={{ color: 'var(--semi-color-text-1)' }} />
          </Tooltip>

          <Tooltip content={d.lang === 'en' ? '切换为中文' : 'Switch to English'}>
            <Button theme="borderless"
              onClick={() => d.setLang(d.lang === 'en' ? 'zh' : 'en')}
              style={{ fontWeight: 600, color: 'var(--semi-color-text-1)' }}>{d.lang === 'en' ? '中文' : 'EN'}</Button>
          </Tooltip>

          <Button type="primary" theme="solid" data-testid="open-trigger-button"
            icon={<Play size={16} fill="currentColor" />}
            onClick={() => { d.suites.fetchTests(); d.setIsTriggerModalOpen(true) }}>{d.t('triggerRun')}</Button>

          <Tooltip content={d.t('signOut')}>
            <Button theme="borderless" onClick={d.handleLogout}
              icon={<LogOut size={16} />} style={{ color: 'var(--semi-color-text-1)' }} />
          </Tooltip>
        </Space>
      </div>
    </header>
  )
}
