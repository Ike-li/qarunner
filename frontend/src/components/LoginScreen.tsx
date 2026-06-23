import type { FormEvent } from 'react'
import { Activity, AlertTriangle, Lock, Moon, RotateCw, Sun } from 'lucide-react'
import styles from '../App.module.css'
import type { Lang, TranslationKey } from '../i18n'

interface LoginScreenProps {
  t: (key: TranslationKey) => string
  theme: 'dark' | 'light'
  setTheme: (theme: 'dark' | 'light') => void
  lang: Lang
  setLang: (lang: Lang) => void
  loginError: string | null
  loginUsername: string
  setLoginUsername: (value: string) => void
  loginPassword: string
  setLoginPassword: (value: string) => void
  loginLoading: boolean
  onSubmit: (e: FormEvent) => void
}

/** Full-screen glassmorphism login overlay shown when unauthenticated (SEC-6:
 *  cookie-only auth, no token handling here). */
export function LoginScreen({
  t,
  theme,
  setTheme,
  lang,
  setLang,
  loginError,
  loginUsername,
  setLoginUsername,
  loginPassword,
  setLoginPassword,
  loginLoading,
  onSubmit,
}: LoginScreenProps) {
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
          aria-label={theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
        >
          {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
        </button>
        <button
          type="button"
          className={styles.actionIconButton}
          onClick={() => setLang(lang === 'en' ? 'zh' : 'en')}
          title={lang === 'en' ? '切换为中文' : 'Switch to English'}
          aria-label={lang === 'en' ? '切换为中文' : 'Switch to English'}
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
            <h1 data-testid="login-title">{t('platformTitle')}</h1>
            <span data-testid="login-subtitle">{t('platformSubtitle')}</span>
          </div>
        </div>

        <form onSubmit={onSubmit} className={styles.form} style={{ padding: 0 }}>
          {loginError && (
            <div className={styles.formErrorAlert} style={{ marginBottom: '1rem' }} role="alert" data-testid="login-error">
              <AlertTriangle size={16} aria-hidden="true" />
              <span>{loginError}</span>
            </div>
          )}

          <div className={styles.formField}>
            <label className={styles.label} htmlFor="login-username">
              <span>{t('username')}</span>
            </label>
            <input
              id="login-username"
              data-testid="login-username"
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
            <label className={styles.label} htmlFor="login-password">
              <span>{t('password')}</span>
            </label>
            <input
              id="login-password"
              data-testid="login-password"
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
            data-testid="login-submit"
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
