import { useState } from 'react'
import { Modal, Button, Input, Banner, Tabs, Select } from '@douyinfe/semi-ui'
import { FolderPlus, GitBranch, KeyRound, Link, Settings, Terminal } from 'lucide-react'
import { useDashboard } from '../hooks/DashboardContext'
import { useDialogA11y } from '../hooks/useDialogA11y'

interface AddSuiteModalProps {
  isOpen: boolean
  onClose: () => void
}

export function AddSuiteModal({ isOpen, onClose }: AddSuiteModalProps) {
  const d = useDashboard()
  const dialogRef = useDialogA11y({ isOpen, onClose })
  const isZh = d.lang === 'zh'
  const [path, setPath] = useState('')
  const [linking, setLinking] = useState(false)
  const [feedback, setFeedback] = useState<{ success: boolean; message: string } | null>(null)

  // Git clone tab state
  const [gitUrl, setGitUrl] = useState('')
  const [gitRef, setGitRef] = useState('')
  const [gitCred, setGitCred] = useState('') // selected credential id ('' = none)
  const [cloning, setCloning] = useState(false)
  const [gitFeedback, setGitFeedback] = useState<{ success: boolean; message: string } | null>(null)
  const [activeTab, setActiveTab] = useState<'local' | 'git'>('local')

  // Inline "new credential" state (secret is write-only; never read back)
  const [newCredName, setNewCredName] = useState('')
  const [newCredSecret, setNewCredSecret] = useState('')
  const [savingCred, setSavingCred] = useState(false)

  const handleCreateCredential = async () => {
    if (!newCredName.trim() || !newCredSecret.trim()) return
    setSavingCred(true)
    try {
      const cred = await d.credentials.createCredential(newCredName.trim(), newCredSecret)
      if (cred) {
        setGitCred(cred.id) // auto-select the freshly created credential
        setNewCredName('')
        setNewCredSecret('')
      }
    } finally {
      setSavingCred(false)
    }
  }

  const handleLink = async () => {
    const trimmedPath = path.trim()
    if (!trimmedPath) return
    setLinking(true)
    setFeedback(null)
    try {
      const resp = await d.apiFetch('/tests/link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: trimmedPath }),
      })
      const data = await resp.json()
      if (resp.ok && data.success) {
        setFeedback({ success: data.is_accessible, message: data.message })
        d.suites.fetchTests()
        setPath('')
      } else {
        setFeedback({ success: false, message: data.detail || (isZh ? '绑定失败' : 'Failed to link suite') })
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Network error'
      setFeedback({ success: false, message })
    } finally {
      setLinking(false)
    }
  }

  const handleClone = async () => {
    const trimmedUrl = gitUrl.trim()
    if (!trimmedUrl) return
    setCloning(true)
    setGitFeedback(null)
    try {
      const resp = await d.apiFetch('/tests/clone', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: trimmedUrl,
          ref: gitRef.trim() || null,
          credential_ref: gitCred.trim() || null,
        }),
      })
      const data = await resp.json()
      if (resp.ok && data.success) {
        setGitFeedback({ success: true, message: data.message })
        d.suites.fetchTests()
        setGitUrl('')
        setGitRef('')
        setGitCred('')
      } else {
        setGitFeedback({ success: false, message: data.detail || (isZh ? '克隆失败' : 'Failed to clone repository') })
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Network error'
      setGitFeedback({ success: false, message })
    } finally {
      setCloning(false)
    }
  }

  return (
    <Modal
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <FolderPlus size={18} style={{ color: 'var(--semi-color-primary)' }} />
          {isZh ? '添加测试套件' : 'Add Test Suite'}
        </div>
      }
      visible={isOpen}
      onCancel={onClose}
      footer={null}
      width={520}
      data-testid="add-suite-modal"
    >
      <div ref={dialogRef} role="dialog" aria-modal="true" tabIndex={-1}>
      <Tabs activeKey={activeTab} onChange={(k) => setActiveTab(k as 'local' | 'git')}>
        <Tabs.TabPane tab={<span data-testid="suite-tab-local"><Link size={14} /> {isZh ? '本地路径' : 'Local Path'}</span>} itemKey="local">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', padding: '1rem 0' }}>
            <Input
              placeholder={isZh ? '输入本地项目目录的绝对路径' : 'Enter absolute path to local project directory'}
              value={path}
              onChange={setPath}
              prefix={<FolderPlus size={14} />}
              data-testid="link-path-input"
              aria-label={isZh ? '本地项目目录路径' : 'Local project directory path'}
            />
            {feedback && (
              <Banner
                type={feedback.success ? 'success' : 'danger'}
                description={feedback.message}
                onClose={() => setFeedback(null)}
              />
            )}
            <Button type="primary" theme="solid" loading={linking} onClick={handleLink} block data-testid="link-submit">
              {isZh ? '绑定套件' : 'Link Suite'}
            </Button>
          </div>
        </Tabs.TabPane>

        <Tabs.TabPane tab={<span data-testid="suite-tab-git"><Terminal size={14} /> {isZh ? 'Git 克隆' : 'Git Clone'}</span>} itemKey="git">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', padding: '1rem 0' }}>
            <Input
              placeholder="https://github.com/user/repo.git"
              value={gitUrl}
              onChange={setGitUrl}
              prefix={<GitBranch size={14} />}
              data-testid="clone-url-input"
              aria-label={isZh ? 'Git 仓库 URL' : 'Git repository URL'}
            />
            <Input
              placeholder={isZh ? '分支/标签 (可选)' : 'Branch / tag (optional)'}
              value={gitRef}
              onChange={setGitRef}
              prefix={<Settings size={14} />}
              data-testid="clone-ref-input"
              aria-label={isZh ? 'Git 分支或标签' : 'Git branch or tag'}
            />
            <Select
              placeholder={isZh ? '凭证 (私有仓库, 可选)' : 'Credential (private repos, optional)'}
              value={gitCred}
              onChange={(v) => setGitCred(v as string)}
              style={{ width: '100%' }}
              prefix={<KeyRound size={14} />}
              data-testid="clone-cred-select"
              aria-label={isZh ? 'Git 凭证' : 'Git credential'}
            >
              <Select.Option value="">{isZh ? '— 无 (公开仓库) —' : '— None (public repo) —'}</Select.Option>
              {d.credentials.credentials.map((c) => (
                <Select.Option key={c.id} value={c.id}>
                  {c.name}
                </Select.Option>
              ))}
            </Select>
            {d.credentials.credentialsLoadError && (
              <Banner
                type="danger"
                description={d.t('credentialLoadErrorDesc')}
                data-testid="credential-load-error"
              />
            )}
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <Input
                size="small"
                placeholder={isZh ? '新凭证名' : 'New credential name'}
                value={newCredName}
                onChange={setNewCredName}
                data-testid="clone-newcred-name"
                aria-label={isZh ? '新凭证名' : 'New credential name'}
              />
              <Input
                size="small"
                type="password"
                placeholder={isZh ? 'HTTPS token' : 'HTTPS token'}
                value={newCredSecret}
                onChange={setNewCredSecret}
                data-testid="clone-newcred-secret"
                aria-label={isZh ? 'HTTPS token' : 'HTTPS token'}
              />
              <Button
                size="small"
                loading={savingCred}
                onClick={handleCreateCredential}
                data-testid="clone-newcred-save"
              >
                {isZh ? '保存凭证' : 'Save'}
              </Button>
            </div>
            {gitFeedback && (
              <Banner
                type={gitFeedback.success ? 'success' : 'danger'}
                description={gitFeedback.message}
                                onClose={() => setGitFeedback(null)}
                data-testid="clone-feedback"
              />
            )}
            <Button type="primary" theme="solid" loading={cloning} onClick={handleClone} block data-testid="clone-submit">
              {isZh ? '克隆仓库' : 'Clone Repository'}
            </Button>
          </div>
        </Tabs.TabPane>
      </Tabs>
      </div>
    </Modal>
  )
}
