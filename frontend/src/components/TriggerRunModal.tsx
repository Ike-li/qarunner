import { Check, Play, Plus, RotateCw, SlidersHorizontal, X } from 'lucide-react'
import { Modal, Banner, Select, Input, Row, Col, Button, Tag, Checkbox } from '@douyinfe/semi-ui'
import styles from '../App.module.css'
import { TestFileTree } from './TestFileTree'
import { useDashboard } from '../hooks/DashboardContext'
import { useDialogA11y } from '../hooks/useDialogA11y'

/** Trigger-run / edit-profile modal: target dir + saved-profile picker, test-file
 *  tree, marker chips, executor mode, env-var grid, and save-as-profile form. */
export function TriggerRunModal() {
  const d = useDashboard()
  const isPlaywrightRunner = d.form.selectedRunner === 'playwright'
  const argsLabelKey = isPlaywrightRunner ? 'playwrightArgsLabel' : 'pytestArgsLabel'
  const argsPlaceholderKey = isPlaywrightRunner
    ? 'playwrightArgsPlaceholder'
    : 'pytestArgsPlaceholder'
  const argsHelpKey = isPlaywrightRunner ? 'playwrightArgsHelp' : 'pytestArgsHelp'

  const onClose = () => d.setIsTriggerModalOpen(false)
  const dialogRef = useDialogA11y({ isOpen: d.isTriggerModalOpen, onClose })
  const onTriggerRun = (e: React.FormEvent) =>
    d.form.handleTriggerRun(e, d.selectedFiles, d.selectedMarkers, (runId: string) => {
      d.setIsTriggerModalOpen(false)
      d.runs.fetchRuns()
      d.runs.setSelectedRunId(runId)
    })
  const onUpdateProfile = (e: React.FormEvent) =>
    d.form.handleUpdateProfile(e, d.selectedFiles, d.selectedMarkers, () => {
      d.profiles.fetchProfiles()
      d.schedules.fetchSchedules()
      d.setIsTriggerModalOpen(false)
    })
  const onSaveProfile = (e: React.FormEvent) =>
    d.form.handleSaveProfile(e, d.selectedFiles, d.selectedMarkers, () => {
      d.profiles.fetchProfiles()
    })
  const onDeleteProfile = async (profileId: string, e?: React.MouseEvent) => {
    // B3: same fix as ProjectSidebar — only clear the selection if the
    // delete actually happened, not on a cancelled confirm or a failure.
    const deleted = await d.profiles.handleDeleteProfile(profileId, e)
    if (!deleted) return
    if (d.form.selectedProfileId === profileId) d.form.setSelectedProfileId('')
  }

  return (
    <Modal
      visible={true}
      onCancel={onClose}
      footer={null}
      width={720}
      bodyStyle={{ maxHeight: '80vh', overflowY: 'auto', padding: '20px' }}
      closeIcon={(
        <span data-testid="trigger-modal-close" style={{ display: 'inline-flex' }} aria-hidden="true">
          <X size={16} />
        </span>
      )}
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <SlidersHorizontal size={20} style={{ color: 'var(--semi-color-primary)' }} />
          <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>
            {d.form.editingProfileId ? (d.lang === 'zh' ? '修改预设执行方案' : 'Modify Saved Execution Profile') : d.t('triggerTitle')}
          </h3>
        </div>
      }
    >
      <div ref={dialogRef} role="dialog" aria-modal="true" tabIndex={-1} data-testid="trigger-modal">
        <form onSubmit={d.form.editingProfileId ? onUpdateProfile : onTriggerRun}>
        {d.form.formError && (
          <Banner
            type="danger"
            description={d.t(d.form.formError as any) || d.form.formError}
            style={{ marginBottom: '16px', borderRadius: '8px' }}
            data-testid="trigger-form-error"
          />
        )}

        {/* Target Directory */}
        <div className={styles.formField}>
          <label className={styles.label} htmlFor="trigger-tests-path">
            <span>{d.t('targetDirectory')}</span>
            <span className={styles.requiredIndicator}>*</span>
          </label>
          {d.suites.tests.length === 0 ? (
            <div className={styles.directoryFallbackText}>
              {d.t('scanningDirectory')}
            </div>
          ) : (
            <Select
              id="trigger-tests-path"
              value={d.form.testsPath}
              onChange={(val) => d.form.setTestsPath(val as string)}
              disabled={!!d.form.editingProfileId}
              style={{ width: '100%' }}
              data-testid="trigger-tests-path-select"
            >
              {d.suites.tests.map(dir => (
                <Select.Option key={dir} value={dir} data-testid={`target-option-${dir}`}>
                  {dir}
                </Select.Option>
              ))}
            </Select>
          )}
          <span className={styles.fieldHelp}>
            {d.t('directoryHelp')}
          </span>
        </div>

        {/* Runner Engine */}
        <div className={styles.formField}>
          <label className={styles.label} htmlFor="trigger-runner">
            <span>Runner</span>
            <span className={styles.requiredIndicator}>*</span>
          </label>
          <Select
            id="trigger-runner"
            data-testid="trigger-runner-select"
            value={d.form.selectedRunner}
            onChange={(val) => d.form.setSelectedRunner(val as string)}
            style={{ width: '100%' }}
          >
            <Select.Option value="pytest" data-testid="runner-option-pytest">pytest</Select.Option>
            <Select.Option value="playwright" data-testid="runner-option-playwright">playwright</Select.Option>
          </Select>
          <span className={styles.fieldHelp}>
            {d.lang === 'zh' ? '选择测试执行引擎（Pytest 或 Playwright）' : 'Choose the test runner engine (Pytest or Playwright)'}
          </span>
        </div>

        {/* 1. Saved Execution Profiles Selection Template */}
        {!d.form.editingProfileId && (
          <div className={styles.formField}>
            <label className={styles.label} htmlFor="trigger-profile">{d.t('selectProfile')}</label>
            <div style={{ display: 'flex', gap: '8px', width: '100%' }}>
              <Select
                id="trigger-profile"
                data-testid="trigger-profile-select"
                value={d.form.selectedProfileId}
                onChange={(val) => {
                  const profileId = val as string
                  d.form.setSelectedProfileId(profileId)
                  if (profileId) {
                    const prof = d.profiles.profiles.find(p => p.id === profileId)
                    if (prof) {
                      d.setSelectedFiles(prof.selected_files || [])
                      d.setSelectedMarkers(prof.selected_markers || [])
                      d.form.setSelectedRunner(prof.runner || 'pytest')
                      d.form.setCustomArgs(prof.extra_args || '')
                      d.form.setTimeoutSeconds(prof.timeout || '')
                      const env_data = prof.env || {}
                      const mappedVars = Object.entries(env_data).map(([key, value]) => ({ key, value: String(value) }))
                      d.form.setEnvVars(mappedVars)
                    }
                  } else {
                    // Reset to default / manual
                    d.setSelectedFiles([])
                    d.setSelectedMarkers([])
                    d.form.setSelectedRunner('pytest')
                    d.form.setCustomArgs('')
                    d.form.setTimeoutSeconds('')
                    d.form.setEnvVars([])
                  }
                }}
                placeholder={d.lang === 'zh' ? '-- 手动配置 (自定义) --' : '-- Manual Configuration (Custom) --'}
                style={{ flex: 1 }}
                showClear={true}
                onClear={() => {
                  d.form.setSelectedProfileId('')
                  d.setSelectedFiles([])
                  d.setSelectedMarkers([])
                  d.form.setSelectedRunner('pytest')
                  d.form.setCustomArgs('')
                  d.form.setTimeoutSeconds('')
                  d.form.setEnvVars([])
                }}
              >
                {d.profiles.profiles
                  .filter(p => p.tests_path === d.form.testsPath)
                  .map(p => (
                    <Select.Option key={p.id} value={p.id} data-testid={`profile-option-${p.id}`}>
                      {p.name} {p.description ? `(${p.description})` : ''}
                    </Select.Option>
                  ))
                }
              </Select>
              {d.form.selectedProfileId && (
                <Button
                  type="danger"
                  theme="light"
                  icon={<X size={16} />}
                  title={d.lang === 'zh' ? '删除方案' : 'Delete Profile'}
                  aria-label={d.lang === 'zh' ? '删除方案' : 'Delete Profile'}
                  onClick={(e) => onDeleteProfile(d.form.selectedProfileId, e as any)}
                  style={{ flexShrink: 0 }}
                />
              )}
            </div>
          </div>
        )}

        {/* 2. Visual File Selection Tree Checklist */}
        <div className={styles.formField}>
          <label className={styles.label} id="trigger-tree-label">{d.t('testSuiteSelection')}</label>
          <div className={styles.treeContainer} style={{ border: '1px solid var(--semi-color-border)', borderRadius: '8px', padding: '12px', maxHeight: '300px', overflowY: 'auto', backgroundColor: 'var(--semi-color-fill-0)' }}>
            {d.suites.scannedFilesTree.length === 0 ? (
              <div className={styles.directoryFallbackText} style={{ padding: '0.5rem' }}>
                {d.lang === 'zh' ? '无可用测试文件。' : 'No pytest files discovered.'}
              </div>
            ) : (
              <TestFileTree
                nodes={d.suites.scannedFilesTree}
                selectedFiles={d.selectedFiles}
                setSelectedFiles={d.setSelectedFiles}
                expandedFolders={d.expandedFolders}
                setExpandedFolders={d.setExpandedFolders}
              />
            )}
          </div>
        </div>

        {/* 3. Visual Tag/Marker Picker */}
        {d.suites.scannedMarkers.length > 0 && (
          <div className={styles.formField}>
            <label className={styles.label} id="trigger-markers-label">{d.t('scannedMarkersTitle')}</label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', padding: '4px 0' }}>
              {d.suites.scannedMarkers.map(tag => {
                const isActive = d.selectedMarkers.includes(tag)
                const toggleMarker = () =>
                  d.setSelectedMarkers(prev =>
                    prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]
                  )
	                return (
	                  <Tag
	                    key={tag}
	                    color={isActive ? 'blue' : 'grey'}
	                    type={isActive ? 'solid' : 'light'}
	                    style={{ cursor: 'pointer', padding: '6px 12px', borderRadius: '16px', userSelect: 'none' }}
	                    onClick={toggleMarker}
	                    aria-pressed={isActive}
	                    data-testid={`marker-tag-${tag}`}
	                  >
                    @{tag}
                  </Tag>
                )
              })}
            </div>
          </div>
        )}

        {/* Runner Arguments */}
        <div className={styles.formField}>
          <label className={styles.label} htmlFor="trigger-args" data-testid="trigger-args-label">
            {d.t(argsLabelKey)}
          </label>
          <Input
            id="trigger-args"
            data-testid="trigger-args-input"
            placeholder={d.t(argsPlaceholderKey)}
            value={d.form.customArgs}
            onChange={(val) => d.form.setCustomArgs(val)}
          />
          <span className={styles.fieldHelp}>
            {d.t(argsHelpKey)}
          </span>
        </div>

        {/* Custom Environment Variables Grid Editor */}
        <div className={styles.formField}>
          <label className={styles.label} id="trigger-customenv-label">
            <span>{d.lang === 'zh' ? '自定义环境变量' : 'Custom Environment Variables'}</span>
          </label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {d.form.envVars.map((env, idx) => (
              <Row key={idx} gutter={8} style={{ display: 'flex', alignItems: 'center' }}>
                <Col span={11}>
                  <Input
                    placeholder={d.lang === 'zh' ? '变量名 e.g. BASE_URL' : 'Name e.g. BASE_URL'}
                    value={env.key}
                    onChange={(val) => {
                      const updated = [...d.form.envVars]
                      updated[idx].key = val
                      d.form.setEnvVars(updated)
                    }}
                    aria-label={d.lang === 'zh' ? `环境变量名 ${idx + 1}` : `Environment variable name ${idx + 1}`}
                  />
                </Col>
                <Col span={11}>
                  <Input
                    placeholder={d.lang === 'zh' ? '变量值' : 'Value'}
                    value={env.value}
                    onChange={(val) => {
                      const updated = [...d.form.envVars]
                      updated[idx].value = val
                      d.form.setEnvVars(updated)
                    }}
                    aria-label={d.lang === 'zh' ? `环境变量值 ${idx + 1}` : `Environment variable value ${idx + 1}`}
                  />
                </Col>
                <Col span={2} style={{ display: 'flex', justifyContent: 'center' }}>
                  <Button
                    type="danger"
                    theme="borderless"
                    icon={<X size={16} />}
                    aria-label={d.lang === 'zh' ? '删除环境变量' : 'Remove environment variable'}
                    onClick={() => {
                      d.form.setEnvVars(d.form.envVars.filter((_, i) => i !== idx))
                    }}
                  />
                </Col>
              </Row>
            ))}
            <Button
              type="primary"
              theme="light"
              icon={<Plus size={14} />}
              onClick={() => {
                d.form.setEnvVars([...d.form.envVars, { key: '', value: '' }])
              }}
              style={{ alignSelf: 'flex-start', marginTop: '4px' }}
            >
              {d.lang === 'zh' ? '添加环境变量' : 'Add Variable'}
            </Button>
          </div>
        </div>

        {/* Timeout and Allure */}
        <Row gutter={16} style={{ marginBottom: '16px' }}>
          <Col span={14}>
            <label className={styles.label} htmlFor="trigger-timeout">{d.t('timeoutLabel')}</label>
            <Input
              id="trigger-timeout"
              data-testid="trigger-timeout-input"
              type="number"
              placeholder={d.t('timeoutPlaceholder')}
              min="1"
              value={d.form.timeoutSeconds === '' ? '' : String(d.form.timeoutSeconds)}
              onChange={(val) => d.form.handleTimeoutSecondsChange(val)}
            />
            <span className={styles.fieldHelp}>
              {d.t('timeoutHelp')}
            </span>
          </Col>
          <Col span={10} style={{ display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', paddingBottom: '4px' }}>
            <label className={styles.label} htmlFor="trigger-allure">{d.t('allureReportsLabel')}</label>
            <Checkbox
              id="trigger-allure"
              checked={d.form.allureEnabled}
              onChange={(e) => d.form.setAllureEnabled(!!e.target.checked)}
              style={{ marginTop: '8px' }}
            >
              {d.lang === 'zh' ? '生成 HTML 全阶测试报告' : 'Enable HTML reports'}
            </Checkbox>
          </Col>
        </Row>

        {/* 4. Save Execution Settings as Profile Toggle & Form */}
        <div className={styles.formField}>
          {d.form.editingProfileId ? (
            <div style={{ border: '1px solid var(--semi-color-border)', borderRadius: '8px', padding: '16px', backgroundColor: 'var(--semi-color-fill-0)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                <SlidersHorizontal size={14} style={{ color: 'var(--semi-color-primary)' }} />
                <h4 style={{ margin: 0 }}>{d.lang === 'zh' ? '修改预设方案信息' : 'Modify Profile Information'}</h4>
              </div>
              <p style={{ margin: '0 0 12px 0', fontSize: '12px', color: 'var(--semi-color-text-2)' }}>
                {d.lang === 'zh' ? '在此处更新当前执行方案的名称和描述信息。' : 'Update the name and description of the current execution profile here.'}
              </p>
                  
              <Row gutter={12}>
                <Col span={12}>
                  <Input
                    placeholder={d.t('profileNamePlaceholder')}
                    value={d.form.profileName}
                    onChange={(val) => d.form.setProfileName(val)}
                    required
                    aria-label={d.lang === 'zh' ? '方案名称' : 'Profile name'}
                  />
                </Col>
                <Col span={12}>
                  <Input
                    placeholder={d.t('profileDescPlaceholder')}
                    value={d.form.profileDesc}
                    onChange={(val) => d.form.setProfileDesc(val)}
                    aria-label={d.lang === 'zh' ? '方案描述' : 'Profile description'}
                  />
                </Col>
              </Row>
            </div>
          ) : !d.form.isSavingProfile ? (
            <Button
              type="primary"
              theme="light"
              icon={<Plus size={14} />}
              onClick={() => d.form.setIsSavingProfile(true)}
            >
              {d.t('saveAsProfile')}
            </Button>
          ) : (
            <div style={{ border: '1px solid var(--semi-color-border)', borderRadius: '8px', padding: '16px', backgroundColor: 'var(--semi-color-fill-0)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                <SlidersHorizontal size={14} style={{ color: 'var(--semi-color-primary)' }} />
                <h4 style={{ margin: 0 }}>{d.t('saveAsProfile')}</h4>
              </div>
              <p style={{ margin: '0 0 12px 0', fontSize: '12px', color: 'var(--semi-color-text-2)' }}>{d.t('saveProfileDesc')}</p>
                  
              <Row gutter={12} style={{ marginBottom: '12px' }}>
                <Col span={12}>
                  <Input
                    placeholder={d.t('profileNamePlaceholder')}
                    value={d.form.profileName}
                    onChange={(val) => d.form.setProfileName(val)}
                    required={d.form.isSavingProfile}
                    aria-label={d.lang === 'zh' ? '方案名称' : 'Profile name'}
                  />
                </Col>
                <Col span={12}>
                  <Input
                    placeholder={d.t('profileDescPlaceholder')}
                    value={d.form.profileDesc}
                    onChange={(val) => d.form.setProfileDesc(val)}
                    aria-label={d.lang === 'zh' ? '方案描述' : 'Profile description'}
                  />
                </Col>
              </Row>
                  
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                <Button
                  size="small"
                  theme="light"
                  onClick={() => {
                    d.form.setIsSavingProfile(false)
                    d.form.setProfileName('')
                    d.form.setProfileDesc('')
                  }}
                >
                  {d.t('cancel')}
                </Button>
                <Button
                  size="small"
                  theme="solid"
                  disabled={d.form.isSubmitting}
                  onClick={(e) => onSaveProfile(e as any)}
                >
                  {d.lang === 'zh' ? '确认保存' : 'Save'}
                </Button>
              </div>
            </div>
          )}
        </div>

        {/* Modal Actions */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '24px' }}>
          <Button
            size="large"
            theme="light"
            onClick={onClose}
            data-testid="trigger-cancel-button"
          >
            {d.t('cancel')}
          </Button>
          <Button
            htmlType="submit"
            size="large"
            theme="solid"
            type="primary"
            disabled={d.form.isSubmitting || d.suites.tests.length === 0}
            data-testid="trigger-submit-button"
            icon={
              d.form.isSubmitting ? (
                <RotateCw size={16} className={styles.spinIcon} />
              ) : d.form.editingProfileId ? (
                <Check size={16} />
              ) : (
                <Play size={16} fill="currentColor" />
              )
            }
          >
            {d.form.isSubmitting ? (
              <span>{d.form.editingProfileId ? (d.lang === 'zh' ? '正在保存...' : 'Saving Changes...') : d.t('schedulingTask')}</span>
            ) : d.form.editingProfileId ? (
              <span>{d.lang === 'zh' ? '保存方案修改' : 'Save Profile Changes'}</span>
            ) : (
              <span>{d.t('launchRun')}</span>
            )}
          </Button>
        </div>
      </form>
    </div>
    </Modal>
  )
}
