import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LoginScreen } from './LoginScreen'
import type { Lang, TranslationKey } from '../i18n'

// Minimal translation stub
const translations: Record<string, string> = {
  platformTitle: 'qarunner',
  platformSubtitle: 'Test Automation Platform',
  username: 'Username',
  usernamePlaceholder: 'Enter username',
  password: 'Password',
  passwordPlaceholder: 'Enter password',
  signIn: 'Sign In',
  authenticating: 'Authenticating...',
}
const t = (key: TranslationKey) => translations[key] ?? key

function renderLogin(overrides: Partial<Parameters<typeof LoginScreen>[0]> = {}) {
  const defaults = {
    t,
    theme: 'light' as const,
    setTheme: vi.fn(),
    lang: 'en' as Lang,
    setLang: vi.fn(),
    loginError: null as string | null,
    loginUsername: '',
    setLoginUsername: vi.fn(),
    loginPassword: '',
    setLoginPassword: vi.fn(),
    loginLoading: false,
    onSubmit: vi.fn(),
  }
  return render(<LoginScreen {...defaults} {...overrides} />)
}

describe('LoginScreen', () => {
  it('renders the platform title and subtitle', () => {
    renderLogin()
    expect(screen.getByTestId('login-title')).toHaveTextContent('qarunner')
    expect(screen.getByTestId('login-subtitle')).toBeVisible()
  })

  it('renders username and password inputs', () => {
    renderLogin()
    expect(screen.getByTestId('login-username')).toBeVisible()
    expect(screen.getByTestId('login-password')).toBeVisible()
  })

  it('renders the submit button with sign-in text', () => {
    renderLogin()
    const btn = screen.getByTestId('login-submit')
    expect(btn).toBeVisible()
    expect(btn).toHaveTextContent('Sign In')
  })

  it('calls setLoginUsername on username input change', () => {
    const setLoginUsername = vi.fn()
    renderLogin({ setLoginUsername })
    fireEvent.change(screen.getByTestId('login-username'), { target: { value: 'alice' } })
    expect(setLoginUsername).toHaveBeenCalledWith('alice')
  })

  it('calls setLoginPassword on password input change', () => {
    const setLoginPassword = vi.fn()
    renderLogin({ setLoginPassword })
    fireEvent.change(screen.getByTestId('login-password'), { target: { value: 'secret' } })
    expect(setLoginPassword).toHaveBeenCalledWith('secret')
  })

  it('calls onSubmit when form is submitted', () => {
    const onSubmit = vi.fn()
    renderLogin({ onSubmit })
    fireEvent.submit(screen.getByTestId('login-submit').closest('form')!)
    expect(onSubmit).toHaveBeenCalled()
  })

  it('displays error alert when loginError is set', () => {
    renderLogin({ loginError: 'Incorrect username or password' })
    const error = screen.getByTestId('login-error')
    expect(error).toBeVisible()
    expect(error).toHaveTextContent('Incorrect username or password')
  })

  it('does not display error alert when loginError is null', () => {
    renderLogin({ loginError: null })
    expect(screen.queryByTestId('login-error')).not.toBeInTheDocument()
  })

  it('shows loading state when loginLoading is true', () => {
    renderLogin({ loginLoading: true })
    const btn = screen.getByTestId('login-submit')
    expect(btn).toBeDisabled()
    expect(btn).toHaveTextContent('Authenticating...')
  })

  it('disables inputs when loading', () => {
    renderLogin({ loginLoading: true })
    expect(screen.getByTestId('login-username')).toBeDisabled()
    expect(screen.getByTestId('login-password')).toBeDisabled()
  })

  it('calls setTheme when theme button is clicked', () => {
    const setTheme = vi.fn()
    renderLogin({ setTheme, theme: 'dark' })
    const themeBtn = screen.getByRole('button', { name: /switch to light mode/i })
    fireEvent.click(themeBtn)
    expect(setTheme).toHaveBeenCalledWith('light')
  })

  it('calls setLang when language button is clicked', () => {
    const setLang = vi.fn()
    renderLogin({ setLang, lang: 'en' })
    const langBtn = screen.getByRole('button', { name: /切换为中文/i })
    fireEvent.click(langBtn)
    expect(setLang).toHaveBeenCalledWith('zh')
  })

  it('populates inputs with provided values', () => {
    renderLogin({ loginUsername: 'admin', loginPassword: 'pass123' })
    expect(screen.getByTestId('login-username')).toHaveValue('admin')
    expect(screen.getByTestId('login-password')).toHaveValue('pass123')
  })
})
