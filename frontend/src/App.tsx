import { useEffect, useState } from 'react'
import { RotateCw } from 'lucide-react'
import styles from './App.module.css'
import { type Lang } from './i18n'
import { LoginScreen } from './components/LoginScreen'
import { DashboardLayout } from './components/DashboardLayout'
import { DashboardProvider, useDashboard } from './hooks/DashboardContext'
import { LocaleProvider } from '@douyinfe/semi-ui'
import zh_CN from '@douyinfe/semi-ui/lib/es/locale/source/zh_CN'
import en_US from '@douyinfe/semi-ui/lib/es/locale/source/en_US'

/** Inner shell: reads auth gate from context, renders spinner/login/dashboard. */
function AppShell() {
  const { auth, lang, setLang, theme, setTheme, t } = useDashboard()

  if (auth.isAuthenticated === null) {
    return (
      <LocaleProvider locale={lang === 'zh' ? zh_CN : en_US}>
        <div className={styles.loginOverlay}>
          <div className={styles.ambientGlow1} />
          <div className={styles.ambientGlow2} />
          <RotateCw size={32} className={styles.spinIcon} />
        </div>
      </LocaleProvider>
    )
  }

  if (!auth.isAuthenticated) {
    return (
      <LocaleProvider locale={lang === 'zh' ? zh_CN : en_US}>
        <LoginScreen
          t={t}
          theme={theme}
          setTheme={setTheme}
          lang={lang}
          setLang={setLang}
          loginError={auth.loginError}
          loginUsername={auth.loginUsername}
          setLoginUsername={auth.setLoginUsername}
          loginPassword={auth.loginPassword}
          setLoginPassword={auth.setLoginPassword}
          loginLoading={auth.loginLoading}
          onSubmit={auth.handleLoginSubmit}
        />
      </LocaleProvider>
    )
  }

  return (
    <LocaleProvider locale={lang === 'zh' ? zh_CN : en_US}>
      <DashboardLayout />
    </LocaleProvider>
  )
}

// ── App root ────────────────────────────────────────────────────────────────

export default function App() {
  const [lang, setLang] = useState<Lang>(
    () => (localStorage.getItem('qarunner_lang') as Lang) || 'en',
  )
  const [theme, setTheme] = useState<'dark' | 'light'>(
    () => (localStorage.getItem('qarunner_theme') as 'dark' | 'light') || 'dark',
  )

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('qarunner_theme', theme)
    const body = document.body
    if (theme === 'dark') body.setAttribute('theme-mode', 'dark')
    else body.removeAttribute('theme-mode')
  }, [theme])

  useEffect(() => {
    localStorage.setItem('qarunner_lang', lang)
  }, [lang])

  return (
    <DashboardProvider lang={lang} setLang={setLang} theme={theme} setTheme={setTheme}>
      <AppShell />
    </DashboardProvider>
  )
}
