import type { FormEvent } from 'react'
import { Plus, RotateCw, Trash2, Users } from 'lucide-react'
import { Modal, Banner, Table, Tag, Input, Select, Row, Col, Button } from '@douyinfe/semi-ui'
import styles from '../App.module.css'
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
  
  const columns = [
    {
      title: t('username'),
      dataIndex: 'username',
      key: 'username',
      render: (text: string) => (
        <strong style={{ color: 'var(--semi-color-text-0)' }} data-testid="user-row-username">
          {text}
        </strong>
      ),
    },
    {
      title: t('role'),
      dataIndex: 'role',
      key: 'role',
      render: (role: string) => (
        <Tag color={role === 'admin' ? 'blue' : 'amber'} type="solid" style={{ textTransform: 'capitalize' }}>
          {role}
        </Tag>
      ),
    },
    {
      title: t('registeredAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      render: (created_at: string) => (
        <span style={{ color: 'var(--semi-color-text-2)' }}>
          {formatDate(created_at)}
        </span>
      ),
    },
  ]

  return (
    <Modal
      visible={true}
      onCancel={onClose}
      footer={null}
      width={720}
      bodyStyle={{ maxHeight: '80vh', overflowY: 'auto', padding: '20px' }}
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Users size={20} style={{ color: 'var(--semi-color-primary)' }} />
          <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>
            {t('userManagementTitle')}
          </h3>
        </div>
      }
    >
      <div data-testid="user-modal" style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
        {/* Section 1: User Directory List */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
            <h4 style={{ margin: 0, fontSize: '15px', fontWeight: 600 }}>{t('platformDirectory')}</h4>
          </div>

          <Table
            columns={columns}
            dataSource={usersList}
            loading={usersLoading}
            pagination={false}
            size="small"
            rowKey="username"
            style={{ border: '1px solid var(--semi-color-border)', borderRadius: '8px' }}
          />
        </div>

        {/* Section 2: Register New User Form */}
        <form onSubmit={onCreateUser} style={{ display: 'flex', flexDirection: 'column', gap: '12px', borderTop: '1px solid var(--semi-color-border)', paddingTop: '20px' }}>
          <h4 style={{ margin: '0 0 4px 0', fontSize: '15px', fontWeight: 600 }}>{t('registerNewUser')}</h4>

          {newUserError && (
            <Banner
              type="danger"
              description={t(newUserError as TranslationKey) || newUserError}
              style={{ borderRadius: '8px' }}
              closeIcon={null}
            />
          )}

          <Row gutter={16}>
            <Col span={12}>
              <div className={styles.formField}>
                <label className={styles.label} htmlFor="user-new-username">{t('username')}</label>
                <Input
                  id="user-new-username"
                  data-testid="user-new-username"
                  placeholder="e.g. testing_lead"
                  value={newUsername}
                  onChange={(val) => setNewUsername(val)}
                  disabled={newUserLoading}
                  required
                />
              </div>
            </Col>

            <Col span={12}>
              <div className={styles.formField}>
                <label className={styles.label} htmlFor="user-new-password">{t('password')}</label>
                <Input
                  id="user-new-password"
                  data-testid="user-new-password"
                  type="password"
                  placeholder="••••••••"
                  value={newPassword}
                  onChange={(val) => setNewPassword(val)}
                  disabled={newUserLoading}
                  required
                />
              </div>
            </Col>
          </Row>

          <Row gutter={16} style={{ display: 'flex', alignItems: 'flex-end' }}>
            <Col span={12}>
              <div className={styles.formField}>
                <label className={styles.label} htmlFor="user-new-role">{t('systemAccessRole')}</label>
                <Select
                  id="user-new-role"
                  value={newUserRole}
                  onChange={(val) => setNewUserRole(val as 'admin' | 'user')}
                  disabled={newUserLoading}
                  style={{ width: '100%' }}
                >
                  <Select.Option value="user">{t('userStandardAccess')}</Select.Option>
                  <Select.Option value="admin">{t('administratorFullControls')}</Select.Option>
                </Select>
              </div>
            </Col>

            <Col span={12}>
              <Button
                htmlType="submit"
                type="primary"
                theme="solid"
                icon={newUserLoading ? <RotateCw size={14} className={styles.spinIcon} /> : <Plus size={14} />}
                disabled={newUserLoading || !newUsername.trim() || !newPassword.trim()}
                data-testid="user-add-submit"
                style={{ width: '100%', height: '34px' }}
              >
                {newUserLoading ? t('registering') : t('addUserAccount')}
              </Button>
            </Col>
          </Row>
        </form>

        {/* Section 3: Storage Space & Data Retention Policy */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', borderTop: '1px solid var(--semi-color-border)', paddingTop: '20px' }}>
          <h4 style={{ margin: 0, fontSize: '15px', fontWeight: 600 }}>
            {lang === 'zh' ? '存储空间与数据保留策略' : 'Storage Space & Data Retention Policy'}
          </h4>
          <p style={{ margin: 0, color: 'var(--semi-color-text-2)', fontSize: '12px', lineHeight: '1.5' }}>
            {lang === 'zh'
              ? '配置平台保留测试日志及报告的策略。执行物理清理将永久删除指定天数之前的运行日志和 HTML 报告目录，但会保留 SQLite 中的分析指标和运行结果数据，且已锁定的记录将被安全保护，不予清理。'
              : 'Configure the storage retention policy. Running cleanup will permanently delete physical run execution directories (logs and reports) older than the specified days. SQLite metadata and run results will be preserved, and locked/pinned runs will be protected from deletion.'}
          </p>

          <Row gutter={16} style={{ display: 'flex', alignItems: 'flex-end', marginTop: '4px' }}>
            <Col span={12}>
              <div className={styles.formField}>
                <label className={styles.label} htmlFor="user-retention-days">
                  {lang === 'zh' ? '保留天数' : 'Retention Days'}
                </label>
                <Input
                  id="user-retention-days"
                  type="number"
                  min="1"
                  value={String(retentionDays)}
                  onChange={(val) => setRetentionDays(Number(val))}
                  disabled={isCleaningStorage}
                />
              </div>
            </Col>

            <Col span={12}>
              <Button
                type="danger"
                theme="solid"
                disabled={isCleaningStorage || retentionDays <= 0}
                icon={isCleaningStorage ? <RotateCw size={14} className={styles.spinIcon} /> : <Trash2 size={14} />}
                style={{ width: '100%', height: '34px' }}
                onClick={async () => {
                  if (!confirm(lang === 'zh' ? `确认要物理清理所有非锁定且早于 ${retentionDays} 天的测试记录文件吗？此操作无法撤销。` : `Are you sure you want to clean up physical files of all unlocked runs older than ${retentionDays} days? This cannot be undone.`)) {
                    return
                  }
                  setIsCleaningStorage(true)
                  try {
                    const resp = await apiFetch(`/runs/cleanup?retention_days=${retentionDays}`, {
                      method: 'POST'
                    })
                    const data = await resp.json()
                    if (resp.ok) {
                      alert(lang === 'zh'
                        ? `清理成功！已清理 ${data.cleaned_count || 0} 个记录目录。`
                        : `Cleanup successful! Purged ${data.cleaned_count || 0} runs directories.`)
                      fetchRuns()
                    } else {
                      alert(data.detail || 'Cleanup failed')
                    }
                  } catch (err) {
                    console.error('Error during cleanup:', err)
                    alert('Network error. Failed to run storage cleanup.')
                  } finally {
                    setIsCleaningStorage(false)
                  }
                }}
              >
                {isCleaningStorage ? (lang === 'zh' ? '清理中...' : 'Cleaning...') : (lang === 'zh' ? '立即执行物理清理' : 'Execute Storage Cleanup')}
              </Button>
            </Col>
          </Row>
        </div>

        {/* Modal Actions */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', borderTop: '1px solid var(--semi-color-border)', paddingTop: '20px' }}>
          <Button
            size="large"
            theme="light"
            onClick={onClose}
            data-testid="user-modal-close"
          >
            {lang === 'zh' ? '关闭' : 'Close'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
