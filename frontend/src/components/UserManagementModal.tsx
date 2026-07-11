import { useState } from 'react'
import { Plus, RotateCw, Trash2, Users } from 'lucide-react'
import { Modal, Banner, Table, Tag, Input, Select, Row, Col, Button } from '@douyinfe/semi-ui'
import styles from '../App.module.css'
import { useDashboard } from '../hooks/DashboardContext'

const formatDate = (iso: string) => new Date(iso).toLocaleString()

/** Admin-only panel — reads everything from DashboardContext. */
export function UserManagementModal() {
  const d = useDashboard()
  const u = d.users
  const [isUpdatingPassword, setIsUpdatingPassword] = useState(false)

  return (
    <Modal
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Users size={18} style={{ color: 'var(--semi-color-primary)' }} />
          {d.t('userMgmtTitle')}
        </div>
      }
      visible={true}
      onCancel={() => d.setIsUserModalOpen(false)}
      footer={null}
      width={640}
      data-testid="user-modal"
      closeIcon={<span data-testid="user-modal-close" />}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', padding: '0.5rem 0' }}>
        {/* User directory */}
        <div>
          <h4 style={{ margin: '0 0 0.5rem', fontSize: '0.9rem', fontWeight: 600 }}>
            {d.t('userMgmtRegisteredUsers')}
          </h4>
          <Table
            dataSource={u.usersList}
            loading={u.usersLoading}
            pagination={false}
            size="small"
            columns={[
              {
                title: d.t('username'),
                dataIndex: 'username',
                key: 'username',
                render: (name: string) => <strong data-testid="user-row-username">{name}</strong>,
              },
              {
                title: d.t('role'),
                dataIndex: 'role',
                key: 'role',
                render: (role: string) => (
                  <Tag color={role === 'admin' ? 'red' : 'blue'} size="small" style={{ textTransform: 'capitalize' }}>
                    {role}
                  </Tag>
                ),
              },
              {
                title: d.t('createdAt'),
                dataIndex: 'created_at',
                key: 'created_at',
                render: (iso: string) => formatDate(iso),
              },
              {
                title: d.t('userMgmtActions'),
                key: 'actions',
                render: (_: unknown, record: { username: string; role: string }) => {
                  const isSelf = record.username === u.currentUsername
                  return (
                    <div
                      style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}
                      data-testid={`user-actions-${record.username}`}
                    >
                      <Button
                        size="small"
                        theme="borderless"
                        data-testid="user-change-password"
                        disabled={isUpdatingPassword}
                        onClick={async () => {
                          if (isUpdatingPassword) return
                          const pw = window.prompt(
                            `${d.t('userMgmtNewPasswordPromptPrefix')}${record.username}${d.t('userMgmtNewPasswordPromptSuffix')}`,
                          )
                          if (pw && pw.trim()) {
                            setIsUpdatingPassword(true)
                            try {
                              const ok = await u.handleUpdateUserPassword(record.username, pw)
                              if (ok)
                                alert(d.t('userMgmtPasswordUpdated'))
                            } finally {
                              setIsUpdatingPassword(false)
                            }
                          }
                        }}
                      >
                        {d.t('userMgmtChangePassword')}
                      </Button>
                      {!isSelf && (
                        <Button
                          size="small"
                          theme="borderless"
                          data-testid="user-toggle-role"
                          onClick={() =>
                            u.handleUpdateUserRole(
                              record.username,
                              record.role === 'admin' ? 'user' : 'admin',
                            )
                          }
                        >
                          {record.role === 'admin'
                            ? d.t('userMgmtMakeUser')
                            : d.t('userMgmtMakeAdmin')}
                        </Button>
                      )}
                      {!isSelf && (
                        <Button
                          size="small"
                          theme="borderless"
                          type="danger"
                          icon={<Trash2 size={13} />}
                          data-testid="user-delete"
                          aria-label={`${d.t('userMgmtDeleteUserAriaLabelPrefix')}${record.username}`}
                          onClick={() => {
                            if (
                              window.confirm(
                                `${d.t('userMgmtDeleteUserConfirmPrefix')}${record.username}${d.t('userMgmtDeleteUserConfirmSuffix')}`,
                              )
                            ) {
                              u.handleDeleteUser(record.username)
                            }
                          }}
                        />
                      )}
                    </div>
                  )
                },
              },
            ]}
          />
        </div>

        {/* Register new user */}
        <div>
          <h4 style={{ margin: '0 0 0.5rem', fontSize: '0.9rem', fontWeight: 600 }}>
            {d.t('registerNewUser')}
          </h4>
          <form onSubmit={u.handleCreateUserSubmit}>
            <Row gutter={12} style={{ marginBottom: '0.75rem' }}>
              <Col span={9}>
                <Input
                  placeholder={d.t('username')}
                  value={u.newUsername}
                  onChange={u.setNewUsername}
                  data-testid="user-new-username"
                  aria-label={d.t('userMgmtNewUsername')}
                />
              </Col>
              <Col span={9}>
                <Input
                  placeholder={d.t('password')}
                  type="password"
                  value={u.newPassword}
                  onChange={u.setNewPassword}
                  data-testid="user-new-password"
                  aria-label={d.t('userMgmtNewPassword')}
                />
              </Col>
              <Col span={6}>
                <Select
                  value={u.newUserRole}
                  onChange={(v) => u.setNewUserRole(v as 'admin' | 'user')}
                  style={{ width: '100%' }}
                  aria-label={d.t('userMgmtUserRole')}
                  data-testid="user-role-select"
                >
                  <Select.Option value="user" data-testid="user-role-option-user">User</Select.Option>
                  <Select.Option value="admin" data-testid="user-role-option-admin">Admin</Select.Option>
                </Select>
              </Col>
            </Row>
            {u.newUserError && (
              <Banner type="danger" description={u.newUserError} onClose={() => u.setNewUserError(null)} style={{ marginBottom: '0.75rem' }} />
            )}
            <Button type="primary" theme="solid" htmlType="submit" loading={u.newUserLoading} icon={<Plus size={14} />} data-testid="user-add-submit">
              {d.t('userMgmtCreateUser')}
            </Button>
          </form>
        </div>

        {/* Storage cleanup */}
        <div>
          <h4 style={{ margin: '0 0 0.5rem', fontSize: '0.9rem', fontWeight: 600 }}>
            {d.t('userMgmtStorageCleanup')}
          </h4>
          <Row gutter={12} style={{ alignItems: 'center' }}>
            <Col span={9}>
              <Input
                type="number"
                value={String(u.retentionDays)}
                onChange={(v) => u.handleRetentionDaysChange(v)}
                addonAfter={d.t('userMgmtDaysUnit')}
                aria-label={d.t('userMgmtRetentionDays')}
                data-testid="retention-days-input"
              />
            </Col>
            <Col span={6}>
              <Button
                type="danger"
                theme="light"
                icon={u.isCleaningStorage ? <RotateCw size={14} className={styles.spinIcon} /> : <Trash2 size={14} />}
                loading={u.isCleaningStorage}
                data-testid="storage-cleanup-button"
                onClick={async () => {
                  if (u.isCleaningStorage) return
                  u.setIsCleaningStorage(true)
                  try {
                    const resp = await d.apiFetch(`/runs/cleanup?retention_days=${u.retentionDays}`, { method: 'POST' })
                    if (resp.ok) {
                      const data = await resp.json()
                      alert(data.cleaned_runs
                        ? `${d.t('userMgmtCleanedRunsPrefix')}${data.cleaned_runs}${d.t('userMgmtCleanedRunsSuffix')}`
                        : d.t('userMgmtNothingToClean'))
                      d.runs.fetchRuns()
                    } else {
                      // B5: a non-ok response (e.g. bad retention_days, server
                      // error) fell through silently, same as the network-error case.
                      const err = await resp.json()
                      alert(err.detail || d.t('userMgmtCleanupFailed'))
                    }
                  } catch (err) {
                    // B5: this catch had no user-visible failure path — a network
                    // error looked like the button silently did nothing.
                    console.error('Cleanup failed', err)
                    alert(d.t('userMgmtCleanupFailed'))
                  } finally {
                    u.setIsCleaningStorage(false)
                  }
                }}
              >
                {d.t('userMgmtCleanOldRuns')}
              </Button>
            </Col>
          </Row>
        </div>
      </div>
    </Modal>
  )
}
