import { Box, Check, Cpu, Play, Plus, RotateCw, SlidersHorizontal, X } from 'lucide-react'
import type { Dispatch, FormEvent, MouseEvent, SetStateAction } from 'react'
import { Modal, Banner, Select, Input, Row, Col, Button, Tag, Checkbox } from '@douyinfe/semi-ui'
import styles from '../App.module.css'
import type { Lang, TranslationKey } from '../i18n'
import type { Profile, TreeNode } from '../types'
import { TestFileTree } from './TestFileTree'

interface TriggerRunModalProps {
  t: (key: TranslationKey) => string
  lang: Lang
  editingProfileId: string | null
  formError: string | null
  tests: string[]
  testsPath: string
  setTestsPath: (value: string) => void
  selectedProfileId: string
  setSelectedProfileId: (value: string) => void
  profiles: Profile[]
  selectedFiles: string[]
  setSelectedFiles: (value: string[]) => void
  scannedFilesTree: TreeNode[]
  expandedFolders: string[]
  setExpandedFolders: (value: string[]) => void
  scannedMarkers: string[]
  selectedMarkers: string[]
  setSelectedMarkers: Dispatch<SetStateAction<string[]>>
  executorMode: 'subprocess' | 'docker'
  setExecutorMode: (value: 'subprocess' | 'docker') => void
  customArgs: string
  setCustomArgs: (value: string) => void
  envVars: { key: string; value: string }[]
  setEnvVars: (value: { key: string; value: string }[]) => void
  timeoutSeconds: number | ''
  setTimeoutSeconds: (value: number | '') => void
  allureEnabled: boolean
  setAllureEnabled: (value: boolean) => void
  isSavingProfile: boolean
  setIsSavingProfile: (value: boolean) => void
  profileName: string
  setProfileName: (value: string) => void
  profileDesc: string
  setProfileDesc: (value: string) => void
  isSubmitting: boolean
  onClose: () => void
  onTriggerRun: (e: FormEvent) => void
  onUpdateProfile: (e: FormEvent) => void
  onSaveProfile: (e: FormEvent) => void
  onDeleteProfile: (profileId: string, e?: MouseEvent) => void
}

/** Trigger-run / edit-profile modal: target dir + saved-profile picker, test-file
 *  tree, marker chips, executor mode, env-var grid, and save-as-profile form. All
 *  form state lives in App; submit/save/delete handlers are passed in as props. */
export function TriggerRunModal({
  t,
  lang,
  editingProfileId,
  formError,
  tests,
  testsPath,
  setTestsPath,
  selectedProfileId,
  setSelectedProfileId,
  profiles,
  selectedFiles,
  setSelectedFiles,
  scannedFilesTree,
  expandedFolders,
  setExpandedFolders,
  scannedMarkers,
  selectedMarkers,
  setSelectedMarkers,
  executorMode,
  setExecutorMode,
  customArgs,
  setCustomArgs,
  envVars,
  setEnvVars,
  timeoutSeconds,
  setTimeoutSeconds,
  allureEnabled,
  setAllureEnabled,
  isSavingProfile,
  setIsSavingProfile,
  profileName,
  setProfileName,
  profileDesc,
  setProfileDesc,
  isSubmitting,
  onClose,
  onTriggerRun,
  onUpdateProfile,
  onSaveProfile,
  onDeleteProfile,
}: TriggerRunModalProps) {
  return (
    <Modal
      visible={true}
      onCancel={onClose}
      footer={null}
      width={720}
      bodyStyle={{ maxHeight: '80vh', overflowY: 'auto', padding: '20px' }}
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <SlidersHorizontal size={20} style={{ color: 'var(--semi-color-primary)' }} />
          <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>
            {editingProfileId ? (lang === 'zh' ? '修改预设执行方案' : 'Modify Saved Execution Profile') : t('triggerTitle')}
          </h3>
        </div>
      }
    >
      <div data-testid="trigger-modal">
        <form onSubmit={editingProfileId ? onUpdateProfile : onTriggerRun}>
        {formError && (
          <Banner
            type="danger"
            description={t(formError as any) || formError}
            style={{ marginBottom: '16px', borderRadius: '8px' }}
          />
        )}

        {/* Target Directory */}
        <div className={styles.formField}>
          <label className={styles.label} htmlFor="trigger-tests-path">
            <span>{t('targetDirectory')}</span>
            <span className={styles.requiredIndicator}>*</span>
          </label>
          {tests.length === 0 ? (
            <div className={styles.directoryFallbackText}>
              {t('scanningDirectory')}
            </div>
          ) : (
            <Select
              id="trigger-tests-path"
              value={testsPath}
              onChange={(val) => setTestsPath(val as string)}
              disabled={!!editingProfileId}
              style={{ width: '100%' }}
            >
              {tests.map(dir => (
                <Select.Option key={dir} value={dir}>{dir}</Select.Option>
              ))}
            </Select>
          )}
          <span className={styles.fieldHelp}>
            {t('directoryHelp')}
          </span>
        </div>

        {/* 1. Saved Execution Profiles Selection Template */}
        {!editingProfileId && (
          <div className={styles.formField}>
            <label className={styles.label} htmlFor="trigger-profile">{t('selectProfile')}</label>
            <div style={{ display: 'flex', gap: '8px', width: '100%' }}>
              <Select
                id="trigger-profile"
                value={selectedProfileId}
                onChange={(val) => {
                  const profileId = val as string
                  setSelectedProfileId(profileId)
                  if (profileId) {
                    const prof = profiles.find(p => p.id === profileId)
                    if (prof) {
                      setSelectedFiles(prof.selected_files || [])
                      setSelectedMarkers(prof.selected_markers || [])
                      setCustomArgs(prof.extra_args || '')
                      setExecutorMode(prof.executor_mode || 'subprocess')
                      setTimeoutSeconds(prof.timeout || '')
                      const env_data = prof.env || {}
                      const mappedVars = Object.entries(env_data).map(([key, value]) => ({ key, value: String(value) }))
                      setEnvVars(mappedVars)
                    }
                  } else {
                    // Reset to default / manual
                    setSelectedFiles([])
                    setSelectedMarkers([])
                    setCustomArgs('')
                    setExecutorMode('subprocess')
                    setTimeoutSeconds('')
                    setEnvVars([])
                  }
                }}
                placeholder={lang === 'zh' ? '-- 手动配置 (自定义) --' : '-- Manual Configuration (Custom) --'}
                style={{ flex: 1 }}
                showClear={true}
                onClear={() => {
                  setSelectedProfileId('')
                  setSelectedFiles([])
                  setSelectedMarkers([])
                  setCustomArgs('')
                  setExecutorMode('subprocess')
                  setTimeoutSeconds('')
                  setEnvVars([])
                }}
              >
                {profiles
                  .filter(p => p.tests_path === testsPath)
                  .map(p => (
                    <Select.Option key={p.id} value={p.id}>
                      {p.name} {p.description ? `(${p.description})` : ''}
                    </Select.Option>
                  ))
                }
              </Select>
              {selectedProfileId && (
                <Button
                  type="danger"
                  theme="light"
                  icon={<X size={16} />}
                  title={lang === 'zh' ? '删除方案' : 'Delete Profile'}
                  onClick={(e) => onDeleteProfile(selectedProfileId, e as any)}
                  style={{ flexShrink: 0 }}
                />
              )}
            </div>
          </div>
        )}

        {/* 2. Visual File Selection Tree Checklist */}
        <div className={styles.formField}>
          <label className={styles.label} id="trigger-tree-label">{t('testSuiteSelection')}</label>
          <div className={styles.treeContainer} style={{ border: '1px solid var(--semi-color-border)', borderRadius: '8px', padding: '12px', maxHeight: '300px', overflowY: 'auto', backgroundColor: 'var(--semi-color-fill-0)' }}>
            {scannedFilesTree.length === 0 ? (
              <div className={styles.directoryFallbackText} style={{ padding: '0.5rem' }}>
                {lang === 'zh' ? '无可用测试文件。' : 'No pytest files discovered.'}
              </div>
            ) : (
              <TestFileTree
                nodes={scannedFilesTree}
                selectedFiles={selectedFiles}
                setSelectedFiles={setSelectedFiles}
                expandedFolders={expandedFolders}
                setExpandedFolders={setExpandedFolders}
              />
            )}
          </div>
        </div>

        {/* 3. Visual Tag/Marker Picker */}
        {scannedMarkers.length > 0 && (
          <div className={styles.formField}>
            <label className={styles.label} id="trigger-markers-label">{t('scannedMarkersTitle')}</label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', padding: '4px 0' }}>
              {scannedMarkers.map(tag => {
                const isActive = selectedMarkers.includes(tag)
                const toggleMarker = () =>
                  setSelectedMarkers(prev =>
                    prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]
                  )
                return (
                  <Tag
                    key={tag}
                    color={isActive ? 'blue' : 'grey'}
                    type={isActive ? 'solid' : 'light'}
                    style={{ cursor: 'pointer', padding: '6px 12px', borderRadius: '16px', userSelect: 'none' }}
                    onClick={toggleMarker}
                  >
                    @{tag}
                  </Tag>
                )
              })}
            </div>
          </div>
        )}

        {/* Execution Environment */}
        <div className={styles.formField}>
          <label className={styles.label} id="trigger-env-label">
            <span>{t('executionEnvironment')}</span>
            <span className={styles.requiredIndicator}>*</span>
          </label>
          <div className={styles.segmentedControl} style={{ display: 'flex', gap: '12px' }}>
            <button
              type="button"
              className={`${styles.segmentButton} ${executorMode === 'subprocess' ? styles.segmentButtonActive : ''}`}
              onClick={() => setExecutorMode('subprocess')}
              style={{ flex: 1, padding: '12px', display: 'flex', alignItems: 'center', gap: '12px', textAlign: 'left', borderRadius: '8px', cursor: 'pointer' }}
            >
              <Cpu size={20} style={{ color: executorMode === 'subprocess' ? 'var(--semi-color-primary)' : 'var(--semi-color-text-2)' }} />
              <div className={styles.segmentTextGroup}>
                <span className={styles.segmentTitle} style={{ fontWeight: 600, display: 'block' }}>{t('localSubprocessTitle')}</span>
                <span className={styles.segmentDesc} style={{ fontSize: '11px', color: 'var(--semi-color-text-2)' }}>{t('localSubprocessDesc')}</span>
              </div>
            </button>
            <button
              type="button"
              className={`${styles.segmentButton} ${executorMode === 'docker' ? styles.segmentButtonActive : ''}`}
              onClick={() => setExecutorMode('docker')}
              style={{ flex: 1, padding: '12px', display: 'flex', alignItems: 'center', gap: '12px', textAlign: 'left', borderRadius: '8px', cursor: 'pointer' }}
            >
              <Box size={20} style={{ color: executorMode === 'docker' ? 'var(--semi-color-primary)' : 'var(--semi-color-text-2)' }} />
              <div className={styles.segmentTextGroup}>
                <span className={styles.segmentTitle} style={{ fontWeight: 600, display: 'block' }}>{t('dockerContainerTitle')}</span>
                <span className={styles.segmentDesc} style={{ fontSize: '11px', color: 'var(--semi-color-text-2)' }}>{t('dockerContainerDesc')}</span>
              </div>
            </button>
          </div>
          <span className={styles.fieldHelp}>
            {t('envHelp')}
          </span>
        </div>

        {/* Pytest Arguments */}
        <div className={styles.formField}>
          <label className={styles.label} htmlFor="trigger-args">{t('pytestArgsLabel')}</label>
          <Input
            id="trigger-args"
            data-testid="trigger-args-input"
            placeholder={t('pytestArgsPlaceholder')}
            value={customArgs}
            onChange={(val) => setCustomArgs(val)}
          />
          <span className={styles.fieldHelp}>
            {t('pytestArgsHelp')}
          </span>
        </div>

        {/* Custom Environment Variables Grid Editor */}
        <div className={styles.formField}>
          <label className={styles.label} id="trigger-customenv-label">
            <span>{lang === 'zh' ? '自定义环境变量' : 'Custom Environment Variables'}</span>
          </label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {envVars.map((env, idx) => (
              <Row key={idx} gutter={8} style={{ display: 'flex', alignItems: 'center' }}>
                <Col span={11}>
                  <Input
                    placeholder={lang === 'zh' ? '变量名 e.g. BASE_URL' : 'Name e.g. BASE_URL'}
                    value={env.key}
                    onChange={(val) => {
                      const updated = [...envVars]
                      updated[idx].key = val
                      setEnvVars(updated)
                    }}
                  />
                </Col>
                <Col span={11}>
                  <Input
                    placeholder={lang === 'zh' ? '变量值' : 'Value'}
                    value={env.value}
                    onChange={(val) => {
                      const updated = [...envVars]
                      updated[idx].value = val
                      setEnvVars(updated)
                    }}
                  />
                </Col>
                <Col span={2} style={{ display: 'flex', justifyContent: 'center' }}>
                  <Button
                    type="danger"
                    theme="borderless"
                    icon={<X size={16} />}
                    onClick={() => {
                      setEnvVars(envVars.filter((_, i) => i !== idx))
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
                setEnvVars([...envVars, { key: '', value: '' }])
              }}
              style={{ alignSelf: 'flex-start', marginTop: '4px' }}
            >
              {lang === 'zh' ? '添加环境变量' : 'Add Variable'}
            </Button>
          </div>
        </div>

        {/* Timeout and Allure */}
        <Row gutter={16} style={{ marginBottom: '16px' }}>
          <Col span={14}>
            <label className={styles.label} htmlFor="trigger-timeout">{t('timeoutLabel')}</label>
            <Input
              id="trigger-timeout"
              data-testid="trigger-timeout-input"
              type="number"
              placeholder={t('timeoutPlaceholder')}
              min="1"
              value={timeoutSeconds === '' ? '' : String(timeoutSeconds)}
              onChange={(val) => setTimeoutSeconds(val === '' ? '' : Number(val))}
            />
            <span className={styles.fieldHelp}>
              {t('timeoutHelp')}
            </span>
          </Col>
          <Col span={10} style={{ display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', paddingBottom: '4px' }}>
            <label className={styles.label} htmlFor="trigger-allure">{t('allureReportsLabel')}</label>
            <Checkbox
              id="trigger-allure"
              checked={allureEnabled}
              onChange={(e) => setAllureEnabled(!!e.target.checked)}
              style={{ marginTop: '8px' }}
            >
              {lang === 'zh' ? '生成 HTML 全阶测试报告' : 'Enable HTML reports'}
            </Checkbox>
          </Col>
        </Row>

        {/* 4. Save Execution Settings as Profile Toggle & Form */}
        <div className={styles.formField}>
          {editingProfileId ? (
            <div style={{ border: '1px solid var(--semi-color-border)', borderRadius: '8px', padding: '16px', backgroundColor: 'var(--semi-color-fill-0)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                <SlidersHorizontal size={14} style={{ color: 'var(--semi-color-primary)' }} />
                <h4 style={{ margin: 0 }}>{lang === 'zh' ? '修改预设方案信息' : 'Modify Profile Information'}</h4>
              </div>
              <p style={{ margin: '0 0 12px 0', fontSize: '12px', color: 'var(--semi-color-text-2)' }}>
                {lang === 'zh' ? '在此处更新当前执行方案的名称和描述信息。' : 'Update the name and description of the current execution profile here.'}
              </p>
                  
              <Row gutter={12}>
                <Col span={12}>
                  <Input
                    placeholder={t('profileNamePlaceholder')}
                    value={profileName}
                    onChange={(val) => setProfileName(val)}
                    required
                  />
                </Col>
                <Col span={12}>
                  <Input
                    placeholder={t('profileDescPlaceholder')}
                    value={profileDesc}
                    onChange={(val) => setProfileDesc(val)}
                  />
                </Col>
              </Row>
            </div>
          ) : !isSavingProfile ? (
            <Button
              type="primary"
              theme="light"
              icon={<Plus size={14} />}
              onClick={() => setIsSavingProfile(true)}
            >
              {t('saveAsProfile')}
            </Button>
          ) : (
            <div style={{ border: '1px solid var(--semi-color-border)', borderRadius: '8px', padding: '16px', backgroundColor: 'var(--semi-color-fill-0)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                <SlidersHorizontal size={14} style={{ color: 'var(--semi-color-primary)' }} />
                <h4 style={{ margin: 0 }}>{t('saveAsProfile')}</h4>
              </div>
              <p style={{ margin: '0 0 12px 0', fontSize: '12px', color: 'var(--semi-color-text-2)' }}>{t('saveProfileDesc')}</p>
                  
              <Row gutter={12} style={{ marginBottom: '12px' }}>
                <Col span={12}>
                  <Input
                    placeholder={t('profileNamePlaceholder')}
                    value={profileName}
                    onChange={(val) => setProfileName(val)}
                    required={isSavingProfile}
                  />
                </Col>
                <Col span={12}>
                  <Input
                    placeholder={t('profileDescPlaceholder')}
                    value={profileDesc}
                    onChange={(val) => setProfileDesc(val)}
                  />
                </Col>
              </Row>
                  
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                <Button
                  size="small"
                  theme="light"
                  onClick={() => {
                    setIsSavingProfile(false)
                    setProfileName('')
                    setProfileDesc('')
                  }}
                >
                  {t('cancel')}
                </Button>
                <Button
                  size="small"
                  theme="solid"
                  onClick={(e) => onSaveProfile(e as any)}
                >
                  {lang === 'zh' ? '确认保存' : 'Save'}
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
            {t('cancel')}
          </Button>
          <Button
            htmlType="submit"
            size="large"
            theme="solid"
            type="primary"
            disabled={isSubmitting || tests.length === 0}
            icon={
              isSubmitting ? (
                <RotateCw size={16} className={styles.spinIcon} />
              ) : editingProfileId ? (
                <Check size={16} />
              ) : (
                <Play size={16} fill="currentColor" />
              )
            }
          >
            {isSubmitting ? (
              <span>{editingProfileId ? (lang === 'zh' ? '正在保存...' : 'Saving Changes...') : t('schedulingTask')}</span>
            ) : editingProfileId ? (
              <span>{lang === 'zh' ? '保存方案修改' : 'Save Profile Changes'}</span>
            ) : (
              <span>{t('launchRun')}</span>
            )}
          </Button>
        </div>
      </form>
    </div>
    </Modal>
  )
}

