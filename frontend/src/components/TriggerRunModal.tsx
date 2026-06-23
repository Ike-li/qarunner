import { AlertTriangle, Box, Check, Cpu, Play, Plus, RotateCw, SlidersHorizontal, X } from 'lucide-react'
import type { Dispatch, FormEvent, MouseEvent, SetStateAction } from 'react'
import styles from '../App.module.css'
import { activateOnKey } from '../a11y'
import type { Lang, TranslationKey } from '../i18n'
import type { Profile, TreeNode } from '../types'
import type { NodeCheckState } from '../hooks/useFileTreeSelection'
import { useDialogA11y } from '../hooks/useDialogA11y'
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
  setSelectedFiles: (value: string[]) => void
  scannedFilesTree: TreeNode[]
  expandedFolders: string[]
  toggleFolder: (path: string) => void
  getNodeCheckState: (node: TreeNode) => NodeCheckState
  handleToggleNode: (node: TreeNode) => void
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
  setSelectedFiles,
  scannedFilesTree,
  expandedFolders,
  toggleFolder,
  getNodeCheckState,
  handleToggleNode,
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
  const dialogRef = useDialogA11y({ isOpen: true, onClose })
  return (
    <div className={styles.modalOverlay} onClick={() => onClose()}>
      <div
        ref={dialogRef}
        className={styles.modal}
        role="dialog"
        aria-modal="true"
        aria-labelledby="trigger-modal-title"
        data-testid="trigger-modal"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <div className={styles.modalTitleGroup}>
            <SlidersHorizontal size={20} className={styles.iconAccent} />
            <h2 id="trigger-modal-title">{editingProfileId ? (lang === 'zh' ? '修改预设执行方案' : 'Modify Saved Execution Profile') : t('triggerTitle')}</h2>
          </div>
          <button className={styles.modalCloseButton} onClick={() => onClose()} aria-label={lang === 'zh' ? '关闭' : 'Close'}>
            <X size={20} />
          </button>
        </div>

        <form onSubmit={editingProfileId ? onUpdateProfile : onTriggerRun} className={styles.form}>
          {formError && (
            <div className={styles.formErrorAlert} role="alert">
              <AlertTriangle size={16} aria-hidden="true" />
              <span>{t(formError as any) || formError}</span>
            </div>
          )}

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
              <div className={styles.selectWrapper}>
                <select
                  id="trigger-tests-path"
                  className={styles.select}
                  value={testsPath}
                  onChange={(e) => setTestsPath(e.target.value)}
                  required
                  disabled={!!editingProfileId}
                >
                  {tests.map(dir => (
                    <option key={dir} value={dir}>{dir}</option>
                  ))}
                </select>
              </div>
            )}
            <span className={styles.fieldHelp}>
              {t('directoryHelp')}
            </span>
          </div>

          {/* 1. Saved Execution Profiles Selection Template */}
          {!editingProfileId && (
            <div className={styles.formField}>
              <label className={styles.label} htmlFor="trigger-profile">{t('selectProfile')}</label>
              <div className={styles.profileSelectorContainer}>
                <select
                  id="trigger-profile"
                  className={styles.select}
                  value={selectedProfileId}
                  onChange={(e) => {
                    const profileId = e.target.value
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
                >
                  <option value="">-- {lang === 'zh' ? '手动配置 (自定义)' : 'Manual Configuration (Custom)'} --</option>
                  {profiles
                    .filter(p => p.tests_path === testsPath)
                    .map(p => (
                      <option key={p.id} value={p.id}>{p.name} {p.description ? `(${p.description})` : ''}</option>
                    ))
                  }
                </select>
                {selectedProfileId && (
                  <button
                  type="button"
                  className={styles.nestedProfileDeleteButton}
                  title={lang === 'zh' ? '删除方案' : 'Delete Profile'}
                  aria-label={lang === 'zh' ? '删除方案' : 'Delete Profile'}
                  onClick={(e) => onDeleteProfile(selectedProfileId, e)}
                  style={{ flexShrink: 0, width: '32px', height: '32px', borderRadius: '8px' }}
                >
                  <X size={16} />
                </button>
              )}
            </div>
          </div>
        )}

          {/* 2. Visual File Selection Tree Checklist */}
          <div className={styles.formField}>
            <label className={styles.label}>{t('testSuiteSelection')}</label>
            <div className={styles.treeContainer}>
              {scannedFilesTree.length === 0 ? (
                <div className={styles.directoryFallbackText} style={{ padding: '0.5rem' }}>
                  {lang === 'zh' ? '无可用测试文件。' : 'No pytest files discovered.'}
                </div>
              ) : (
                <TestFileTree
                  nodes={scannedFilesTree}
                  expandedFolders={expandedFolders}
                  onToggleFolder={toggleFolder}
                  getNodeCheckState={getNodeCheckState}
                  onToggleNode={handleToggleNode}
                />
              )}
            </div>
          </div>

          {/* 3. Visual Tag/Marker Picker */}
          {scannedMarkers.length > 0 && (
            <div className={styles.formField}>
              <label className={styles.label}>{t('scannedMarkersTitle')}</label>
              <div className={styles.tagContainer}>
                {scannedMarkers.map(tag => {
                  const isActive = selectedMarkers.includes(tag)
                  const toggleMarker = () =>
                    setSelectedMarkers(prev =>
                      prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]
                    )
                  return (
                    <span
                      key={tag}
                      className={`${styles.tagPill} ${isActive ? styles.tagPillActive : ''}`}
                      role="button"
                      tabIndex={0}
                      aria-pressed={isActive}
                      onClick={toggleMarker}
                      onKeyDown={activateOnKey(toggleMarker)}
                    >
                      @{tag}
                    </span>
                  )
                })}
              </div>
            </div>
          )}

          <div className={styles.formField}>
            <label className={styles.label}>
              <span>{t('executionEnvironment')}</span>
              <span className={styles.requiredIndicator}>*</span>
            </label>
            <div className={styles.segmentedControl}>
              <button
                type="button"
                className={`${styles.segmentButton} ${executorMode === 'subprocess' ? styles.segmentButtonActive : ''}`}
                onClick={() => setExecutorMode('subprocess')}
              >
                <Cpu size={16} />
                <div className={styles.segmentTextGroup}>
                  <span className={styles.segmentTitle}>{t('localSubprocessTitle')}</span>
                  <span className={styles.segmentDesc}>{t('localSubprocessDesc')}</span>
                </div>
              </button>
              <button
                type="button"
                className={`${styles.segmentButton} ${executorMode === 'docker' ? styles.segmentButtonActive : ''}`}
                onClick={() => setExecutorMode('docker')}
              >
                <Box size={16} />
                <div className={styles.segmentTextGroup}>
                  <span className={styles.segmentTitle}>{t('dockerContainerTitle')}</span>
                  <span className={styles.segmentDesc}>{t('dockerContainerDesc')}</span>
                </div>
              </button>
            </div>
            <span className={styles.fieldHelp}>
              {t('envHelp')}
            </span>
          </div>

          <div className={styles.formField}>
            <label className={styles.label} htmlFor="trigger-args">{t('pytestArgsLabel')}</label>
            <input
              id="trigger-args"
              data-testid="trigger-args-input"
              type="text"
              className={styles.input}
              placeholder={t('pytestArgsPlaceholder')}
              value={customArgs}
              onChange={(e) => setCustomArgs(e.target.value)}
            />
            <span className={styles.fieldHelp}>
              {t('pytestArgsHelp')}
            </span>
          </div>

          {/* Custom Environment Variables Grid Editor */}
          <div className={styles.formField}>
            <label className={styles.label}>
              <span>{lang === 'zh' ? '自定义环境变量' : 'Custom Environment Variables'}</span>
            </label>
            <div className={styles.envGridContainer}>
              {envVars.map((env, idx) => (
                <div key={idx} className={styles.envRow}>
                  <input
                    type="text"
                    className={styles.envInput}
                    placeholder={lang === 'zh' ? '变量名 e.g. BASE_URL' : 'Name e.g. BASE_URL'}
                    value={env.key}
                    onChange={(e) => {
                      const updated = [...envVars]
                      updated[idx].key = e.target.value
                      setEnvVars(updated)
                    }}
                  />
                  <input
                    type="text"
                    className={styles.envInput}
                    placeholder={lang === 'zh' ? '变量值' : 'Value'}
                    value={env.value}
                    onChange={(e) => {
                      const updated = [...envVars]
                      updated[idx].value = e.target.value
                      setEnvVars(updated)
                    }}
                  />
                  <button
                    type="button"
                    className={styles.envDeleteBtn}
                    title={lang === 'zh' ? '删除' : 'Delete'}
                    aria-label={lang === 'zh' ? '删除环境变量' : 'Delete variable'}
                    onClick={() => {
                      setEnvVars(envVars.filter((_, i) => i !== idx))
                    }}
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
              <button
                type="button"
                className={styles.envAddBtn}
                onClick={() => {
                  setEnvVars([...envVars, { key: '', value: '' }])
                }}
              >
                <Plus size={14} />
                <span>{lang === 'zh' ? '添加环境变量' : 'Add Variable'}</span>
              </button>
            </div>
          </div>

          <div className={styles.formFieldRow}>
            <div className={styles.formField} style={{ flex: 1 }}>
              <label className={styles.label} htmlFor="trigger-timeout">{t('timeoutLabel')}</label>
              <input
                id="trigger-timeout"
                data-testid="trigger-timeout-input"
                type="number"
                className={styles.input}
                placeholder={t('timeoutPlaceholder')}
                min="1"
                value={timeoutSeconds}
                onChange={(e) => setTimeoutSeconds(e.target.value === '' ? '' : Number(e.target.value))}
              />
              <span className={styles.fieldHelp}>
                {t('timeoutHelp')}
              </span>
            </div>

            <div className={styles.formField} style={{ flex: '0 0 auto', alignSelf: 'flex-start', paddingTop: '0.5rem' }}>
              <label className={styles.label} htmlFor="trigger-allure">{t('allureReportsLabel')}</label>
              <label className={styles.switchContainer}>
                <input
                  id="trigger-allure"
                  type="checkbox"
                  className={styles.switchInput}
                  checked={allureEnabled}
                  onChange={(e) => setAllureEnabled(e.target.checked)}
                />
                <span className={styles.switchSlider}></span>
              </label>
            </div>
          </div>

          {/* 4. Save Execution Settings as Profile Toggle & Form */}
          <div className={styles.formField}>
            {editingProfileId ? (
              <div className={styles.profileSaveForm}>
                <div className={styles.profileSaveTitleGroup}>
                  <SlidersHorizontal size={14} className={styles.iconAccent} />
                  <h4>{lang === 'zh' ? '修改预设方案信息' : 'Modify Profile Information'}</h4>
                </div>
                <p className={styles.profileSaveDesc}>
                  {lang === 'zh' ? '在此处更新当前执行方案的名称和描述信息。' : 'Update the name and description of the current execution profile here.'}
                </p>
                    
                <div className={styles.profileSaveInputs}>
                  <input
                    type="text"
                    className={styles.input}
                    placeholder={t('profileNamePlaceholder')}
                    value={profileName}
                    onChange={(e) => setProfileName(e.target.value)}
                    required
                  />
                  <input
                    type="text"
                    className={styles.input}
                    placeholder={t('profileDescPlaceholder')}
                    value={profileDesc}
                    onChange={(e) => setProfileDesc(e.target.value)}
                  />
                </div>
              </div>
            ) : !isSavingProfile ? (
              <button
                type="button"
                className={styles.profileSaveToggleBtn}
                onClick={() => setIsSavingProfile(true)}
              >
                <Plus size={14} />
                <span>{t('saveAsProfile')}</span>
              </button>
            ) : (
              <div className={styles.profileSaveForm}>
                <div className={styles.profileSaveTitleGroup}>
                  <SlidersHorizontal size={14} className={styles.iconAccent} />
                  <h4>{t('saveAsProfile')}</h4>
                </div>
                <p className={styles.profileSaveDesc}>{t('saveProfileDesc')}</p>
                    
                <div className={styles.profileSaveInputs}>
                  <input
                    type="text"
                    className={styles.input}
                    placeholder={t('profileNamePlaceholder')}
                    value={profileName}
                    onChange={(e) => setProfileName(e.target.value)}
                    required={isSavingProfile}
                  />
                  <input
                    type="text"
                    className={styles.input}
                    placeholder={t('profileDescPlaceholder')}
                    value={profileDesc}
                    onChange={(e) => setProfileDesc(e.target.value)}
                  />
                </div>
                    
                <div className={styles.profileSaveActions}>
                  <button
                    type="button"
                    className={styles.cancelButton}
                    onClick={() => {
                      setIsSavingProfile(false)
                      setProfileName('')
                      setProfileDesc('')
                    }}
                    style={{ padding: '0.25rem 0.75rem', fontSize: '0.8rem' }}
                  >
                    {t('cancel')}
                  </button>
                  <button
                    type="button"
                    className={styles.profileSaveSubmitBtn}
                    onClick={onSaveProfile}
                  >
                    {lang === 'zh' ? '确认保存' : 'Save'}
                  </button>
                </div>
              </div>
            )}
          </div>

          <div className={styles.formActions}>
            <button
              type="button"
              className={styles.cancelButton}
              data-testid="trigger-cancel-button"
              onClick={() => onClose()}
            >
              {t('cancel')}
            </button>
            <button 
              type="submit" 
              className={styles.submitButton}
              disabled={isSubmitting || tests.length === 0}
            >
              {isSubmitting ? (
                <>
                  <RotateCw size={16} className={styles.spinIcon} />
                  <span>{editingProfileId ? (lang === 'zh' ? '正在保存...' : 'Saving Changes...') : t('schedulingTask')}</span>
                </>
              ) : editingProfileId ? (
                <>
                  <Check size={16} />
                  <span>{lang === 'zh' ? '保存方案修改' : 'Save Profile Changes'}</span>
                </>
              ) : (
                <>
                  <Play size={16} fill="currentColor" />
                  <span>{t('launchRun')}</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
