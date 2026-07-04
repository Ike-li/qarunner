import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Header } from './Header'

// Mock lottie-web to prevent canvas errors in jsdom
vi.mock('lottie-web', () => ({
  default: { loadAnimation: vi.fn(() => ({ play: vi.fn(), stop: vi.fn(), destroy: vi.fn() })) },
}))

const mockSetIsUserModalOpen = vi.fn()
const mockSetIsTriggerModalOpen = vi.fn()
const mockFetchUsers = vi.fn()
const mockFetchTests = vi.fn()
const mockHandleLogout = vi.fn()
const mockSetTheme = vi.fn()
const mockSetLang = vi.fn()

vi.mock('../hooks/DashboardContext', () => ({
  useDashboard: () => ({
    t: (key: string) => key,
    theme: 'light',
    setTheme: mockSetTheme,
    lang: 'en',
    setLang: mockSetLang,
    auth: {
      currentUser: { username: 'admin', role: 'admin' },
    },
    users: { fetchUsers: mockFetchUsers },
    suites: { fetchTests: mockFetchTests },
    handleLogout: mockHandleLogout,
    setIsUserModalOpen: mockSetIsUserModalOpen,
    setIsTriggerModalOpen: mockSetIsTriggerModalOpen,
  }),
}))

describe('Header', () => {
  it('renders the platform title', () => {
    render(<Header />)
    expect(screen.getByText('platformTitle')).toBeVisible()
  })

  it('displays current username and role', () => {
    render(<Header />)
    expect(screen.getByTestId('profile-username')).toHaveTextContent('admin')
    expect(screen.getByTestId('profile-role')).toHaveTextContent('admin')
  })

  it('shows the Users button for admin role', () => {
    render(<Header />)
    expect(screen.getByTestId('open-users-button')).toBeVisible()
  })

  it('shows the Trigger Run button', () => {
    render(<Header />)
    expect(screen.getByTestId('open-trigger-button')).toBeVisible()
  })

  it('opens user modal when Users button is clicked', () => {
    render(<Header />)
    fireEvent.click(screen.getByTestId('open-users-button'))
    expect(mockFetchUsers).toHaveBeenCalled()
    expect(mockSetIsUserModalOpen).toHaveBeenCalledWith(true)
  })

  it('opens trigger modal when Trigger Run button is clicked', () => {
    render(<Header />)
    fireEvent.click(screen.getByTestId('open-trigger-button'))
    expect(mockFetchTests).toHaveBeenCalled()
    expect(mockSetIsTriggerModalOpen).toHaveBeenCalledWith(true)
  })

  it('calls handleLogout on logout button click', () => {
    render(<Header />)
    // The logout button has a LogOut icon; find it by its tooltip
    const buttons = screen.getAllByRole('button')
    // Last action button before the logout one
    const logoutBtn = buttons[buttons.length - 1]
    fireEvent.click(logoutBtn)
    expect(mockHandleLogout).toHaveBeenCalled()
  })
})
