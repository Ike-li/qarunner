import { useState } from 'react'
import { Modal, Button, Input, Banner, Tabs } from '@douyinfe/semi-ui'
import { FolderPlus, Info, Terminal, Settings, Link, GitBranch } from 'lucide-react'
import type { Lang } from '../i18n'

interface AddSuiteModalProps {
  isOpen: boolean
  onClose: () => void
  lang: Lang
  apiFetch: (path: string, opts?: RequestInit) => Promise<Response>
  onSuiteLinked: () => void
}

export function AddSuiteModal({ isOpen, onClose, lang, apiFetch, onSuiteLinked }: AddSuiteModalProps) {
  const isZh = lang === 'zh'
  const [path, setPath] = useState('')
  const [linking, setLinking] = useState(false)
  const [feedback, setFeedback] = useState<{ success: boolean; message: string } | null>(null)

  // Git clone tab state (stage 5)
  const [gitUrl, setGitUrl] = useState('')
  const [gitRef, setGitRef] = useState('')
  const [gitCred, setGitCred] = useState('')
  const [cloning, setCloning] = useState(false)
  const [gitFeedback, setGitFeedback] = useState<{ success: boolean; message: string } | null>(null)

  const title = isZh ? '添加项目测试套件' : 'Add Project Test Suite'

  const handleClone = async () => {
    const url = gitUrl.trim()
    if (!url) return
    setCloning(true)
    setGitFeedback(null)
    try {
      const resp = await apiFetch('/tests/clone', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url,
          ref: gitRef.trim() || null,
          credential_ref: gitCred.trim() || null,
        }),
      })
      const data = await resp.json()
      if (resp.ok && data.success) {
        setGitFeedback({ success: true, message: data.message })
        onSuiteLinked() // Refresh the sidebar list instantly
        setGitUrl('')
        setGitRef('')
        setGitCred('')
      } else {
        setGitFeedback({ success: false, message: data.detail || (isZh ? '克隆失败' : 'Failed to clone repository') })
      }
    } catch (err: any) {
      setGitFeedback({ success: false, message: err.message || 'Network error' })
    } finally {
      setCloning(false)
    }
  }

  const handleLink = async () => {
    const trimmedPath = path.trim()
    if (!trimmedPath) return
    setLinking(true)
    setFeedback(null)
    try {
      const resp = await apiFetch('/tests/link', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ path: trimmedPath }),
      })
      const data = await resp.json()
      if (resp.ok && data.success) {
        setFeedback({ success: data.is_accessible, message: data.message })
        onSuiteLinked() // Refresh the sidebar list instantly!
        setPath('') // Clear path input on successful registration
      } else {
        setFeedback({ success: false, message: data.detail || (isZh ? '绑定失败' : 'Failed to link suite') })
      }
    } catch (err: any) {
      setFeedback({ success: false, message: err.message || 'Network error' })
    } finally {
      setLinking(false)
    }
  }

  return (
    <Modal
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <FolderPlus size={20} style={{ color: 'var(--semi-color-primary)' }} />
          <span>{title}</span>
        </div>
      }
      visible={isOpen}
      onCancel={onClose}
      footer={
        <Button size="large" theme="solid" type="primary" onClick={onClose}>
          {isZh ? '我知道了' : 'Got it'}
        </Button>
      }
      width={600}
    >
      <Tabs type="line" defaultActiveKey="git" style={{ color: 'var(--semi-color-text-0)' }}>
        <Tabs.TabPane
          itemKey="git"
          tab={
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
              <GitBranch size={15} />
              {isZh ? 'Git 仓库' : 'Git URL'}
            </span>
          }
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', paddingTop: '8px', color: 'var(--semi-color-text-0)' }}>
            <div style={{
              display: 'flex',
              gap: '12px',
              padding: '12px 16px',
              borderRadius: '8px',
              backgroundColor: 'var(--semi-color-primary-light-default)',
              color: 'var(--semi-color-primary)',
              fontSize: '13px',
              lineHeight: '1.5'
            }}>
              <Info size={18} style={{ flexShrink: 0, marginTop: '2px' }} />
              <div>
                {isZh ? (
                  <><strong>说明：</strong>平台单实例会把仓库 <code>git clone</code> 到容器内 <code>/app/external_tests/</code> 下，环境无关（dev 与服务器一致）。仅支持 <code>https://</code> 与 <code>git@</code>。</>
                ) : (
                  <><strong>Info:</strong> The platform single-instance <code>git clone</code>s the repo into <code>/app/external_tests/</code> inside the container — environment-independent (same on dev and server). Only <code>https://</code> and <code>git@</code> are allowed.</>
                )}
              </div>
            </div>

            <div style={{
              padding: '16px',
              border: '1px solid var(--semi-color-primary)',
              borderRadius: '8px',
              backgroundColor: 'var(--semi-color-primary-light-default)',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 600, color: 'var(--semi-color-primary)', fontSize: '14px' }}>
                <GitBranch size={16} />
                <span>{isZh ? '从 Git 仓库克隆' : 'Clone from Git Repository'}</span>
              </div>
              <Input
                placeholder={isZh ? '仓库 URL，例如 https://github.com/org/repo.git' : 'Repository URL, e.g. https://github.com/org/repo.git'}
                value={gitUrl}
                onChange={(val) => setGitUrl(val)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleClone() }}
              />
              <Input
                placeholder={isZh ? '分支 / 标签（可选，默认仓库默认分支）' : 'Branch / tag (optional, defaults to the repo default)'}
                value={gitRef}
                onChange={(val) => setGitRef(val)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleClone() }}
              />
              <Input
                placeholder={isZh ? '凭证引用（可选，私有仓库用）' : 'Credential reference (optional, for private repos)'}
                value={gitCred}
                onChange={(val) => setGitCred(val)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleClone() }}
              />
              <Button
                theme="solid"
                type="primary"
                loading={cloning}
                onClick={handleClone}
                disabled={!gitUrl.trim()}
                style={{ alignSelf: 'flex-start' }}
              >
                {isZh ? '克隆仓库' : 'Clone Repository'}
              </Button>
              {gitFeedback && (
                <Banner
                  type={gitFeedback.success ? 'success' : 'warning'}
                  description={gitFeedback.message}
                  closeIcon={null}
                  style={{ marginTop: '4px', borderRadius: '6px' }}
                />
              )}
              <p style={{ margin: 0, fontSize: '12px', lineHeight: '1.5', color: 'var(--semi-color-text-2)' }}>
                {isZh
                  ? '克隆后，若为 Node 套件，可在套件列表点「准备依赖」执行 npm ci（依赖只在平台安装，执行器无网络）。'
                  : 'After cloning a Node suite, click "Prepare deps" in the suite list to run npm ci (deps install on the platform only; executors have no network).'}
              </p>
            </div>
          </div>
        </Tabs.TabPane>

        <Tabs.TabPane
          itemKey="local"
          tab={
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
              <Link size={15} />
              {isZh ? '本地路径' : 'Local Path'}
            </span>
          }
        >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', paddingTop: '8px', color: 'var(--semi-color-text-0)' }}>

        {/* Banner with general explanation */}
        <div style={{
          display: 'flex',
          gap: '12px',
          padding: '12px 16px',
          borderRadius: '8px',
          backgroundColor: 'var(--semi-color-primary-light-default)',
          color: 'var(--semi-color-primary)',
          fontSize: '13px',
          lineHeight: '1.5'
        }}>
          <Info size={18} style={{ flexShrink: 0, marginTop: '2px' }} />
          <div>
            {isZh ? (
              <>
                <strong>说明：</strong>平台会自动扫描容器内 <code>/app/external_tests/</code> 目录下的所有一级子文件夹。
                您可以在下方输入宿主机的绝对目录一键绑定，平台即会自动识别，无需在数据库中进行繁杂的注册！
              </>
            ) : (
              <>
                <strong>Info:</strong> The platform dynamically scans all top-level subfolders under the container's <code>/app/external_tests/</code> directory.
                Input your host absolute directory below to link it instantly, and it will be auto-discovered immediately.
              </>
            )}
          </div>
        </div>

        {/* Quick Link Local Path Form */}
        <div style={{
          padding: '16px',
          border: '1px solid var(--semi-color-primary)',
          borderRadius: '8px',
          backgroundColor: 'var(--semi-color-primary-light-default)',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 600, color: 'var(--semi-color-primary)', fontSize: '14px' }}>
            <Link size={16} />
            <span>{isZh ? '⚡ 一键绑定本地项目路径' : '⚡ Quick Link Local Project Path'}</span>
          </div>
          <p style={{ margin: 0, fontSize: '13px', lineHeight: '1.4', color: 'var(--semi-color-text-1)' }}>
            {isZh ? '直接在下方输入您本地电脑上的自动化测试项目绝对路径，平台将自动为您创建软链接！' : 'Directly input your local project absolute path on your host machine below, and the platform will automatically link it!'}
          </p>
          <div style={{ display: 'flex', gap: '8px' }}>
            <Input
              placeholder={isZh ? '例如: ~/code/my-e2e-suite' : 'E.g. ~/code/my-e2e-suite'}
              value={path}
              onChange={(val) => setPath(val)}
              style={{ flex: 1 }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  handleLink()
                }
              }}
            />
            <Button
              theme="solid"
              type="primary"
              loading={linking}
              onClick={handleLink}
              disabled={!path.trim()}
            >
              {isZh ? '一键绑定' : 'Link Suite'}
            </Button>
          </div>
          
          {feedback && (
            <Banner
              type={feedback.success ? 'success' : 'warning'}
              description={feedback.message}
              closeIcon={null}
              style={{ marginTop: '4px', borderRadius: '6px' }}
            />
          )}
        </div>

        {/* Step-by-step guidance */}
        <h4 style={{ margin: '8px 0 4px 0', fontSize: '14px', fontWeight: 600 }}>
          {isZh ? '👉 其他添加项目的方法' : '👉 Other Ways to Link Your Project'}
        </h4>

        {/* Option 1: Docker mount (Recommended) */}
        <div style={{ border: '1px solid var(--semi-color-border)', borderRadius: '8px', overflow: 'hidden' }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '10px 14px',
            backgroundColor: 'var(--semi-color-fill-0)',
            borderBottom: '1px solid var(--semi-color-border)',
            fontWeight: 500,
            fontSize: '13px'
          }}>
            <Settings size={15} style={{ color: 'var(--semi-color-text-2)' }} />
            <span>{isZh ? '方法一：使用 Docker 挂载（强烈推荐）' : 'Method 1: Docker Volume Mount (Recommended)'}</span>
          </div>
          <div style={{ padding: '14px', fontSize: '13px', lineHeight: '1.6' }}>
            <p style={{ margin: '0 0 10px 0' }}>
              {isZh ? (
                '修改本地的 `docker-compose.yml` 文件，将您的本地项目直接作为 read-only 目录挂载进平台的扫描根目录下：'
              ) : (
                'Modify your `docker-compose.yml` to mount your local test folder as a read-only directory inside the platform:'
              )}
            </p>
            <pre style={{
              margin: '0 0 12px 0',
              padding: '12px',
              borderRadius: '6px',
              backgroundColor: 'var(--semi-color-fill-1)',
              fontFamily: 'monospace',
              fontSize: '12px',
              overflowX: 'auto',
              color: 'var(--semi-color-text-1)'
            }}>
{`services:
  platform:
    # ... 现有配置
    volumes:
      - ./artifacts:~/code/qa_platform_v2/artifacts
      - ./external_tests:/app/external_tests:ro
      # 👇 新增这行，将您的 my-e2e-suite 项目挂载进去
      - ~/code/my-e2e-suite:/app/external_tests/my-e2e-suite:ro`}
            </pre>
            <p style={{ margin: '0 0 4px 0' }}>
              {isZh ? (
                <>保存后，在命令行中重新拉起并构建服务即可：</>
              ) : (
                <>Save the file, then restart and rebuild using:</>
              )}
            </p>
            <code style={{
              display: 'block',
              padding: '8px 12px',
              borderRadius: '6px',
              backgroundColor: 'var(--semi-color-fill-1)',
              fontFamily: 'monospace',
              fontSize: '12px',
              color: 'var(--semi-color-text-1)'
            }}>
              docker compose up -d --build
            </code>
          </div>
        </div>

        {/* Option 2: Copying / Symlinking locally */}
        <div style={{ border: '1px solid var(--semi-color-border)', borderRadius: '8px', overflow: 'hidden' }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '10px 14px',
            backgroundColor: 'var(--semi-color-fill-0)',
            borderBottom: '1px solid var(--semi-color-border)',
            fontWeight: 500,
            fontSize: '13px'
          }}>
            <Terminal size={15} style={{ color: 'var(--semi-color-text-2)' }} />
            <span>{isZh ? '方法二：手动创建软链接或复制（命令行操作）' : 'Method 2: Create a Local Symlink or Copy'}</span>
          </div>
          <div style={{ padding: '14px', fontSize: '13px', lineHeight: '1.6' }}>
            <p style={{ margin: '0 0 10px 0' }}>
              {isZh ? (
                '如果您在本地非容器模式下启动开发服务器，或者想手动把代码放入扫描文件夹，可直接在项目的 `external_tests` 目录下创建软连接：'
              ) : (
                'If running in native local mode, or if you want to quickly link the folders, create a symlink inside `external_tests`:'
              )}
            </p>
            <code style={{
              display: 'block',
              padding: '10px 12px',
              borderRadius: '6px',
              backgroundColor: 'var(--semi-color-fill-1)',
              fontFamily: 'monospace',
              fontSize: '12px',
              color: 'var(--semi-color-text-1)',
              wordBreak: 'break-all'
            }}>
              ln -s ~/code/my-e2e-suite ~/code/qa_platform_v2/external_tests/my-e2e-suite
            </code>
          </div>
        </div>

      </div>
        </Tabs.TabPane>
      </Tabs>
    </Modal>
  )
}
