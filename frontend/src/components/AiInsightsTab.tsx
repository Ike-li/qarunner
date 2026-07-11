import { useEffect, type CSSProperties } from 'react'
import { AlertTriangle, RefreshCw, Shuffle, Sparkles } from 'lucide-react'
import styles from '../App.module.css'
import type { ApiFetch } from '../hooks/useApi'
import { useAiAnalysis } from '../hooks/useAiAnalysis'
import type { TranslationKey } from '../i18n'

interface Props {
  runId: string
  apiFetch: ApiFetch
  t: (key: TranslationKey) => string
}

const CATEGORY_KEY: Record<string, TranslationKey> = {
  new_failure: 'aiCatNewFailure',
  historical_flaky: 'aiCatHistoricalFlaky',
  environment: 'aiCatEnvironment',
  assertion: 'aiCatAssertion',
  timeout: 'aiCatTimeout',
  permission_path: 'aiCatPermissionPath',
}

const badgeStyle: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '4px',
  padding: '2px 10px',
  borderRadius: '999px',
  fontSize: '12px',
  fontWeight: 600,
  border: '1px solid var(--color-accent)',
  color: 'var(--color-accent)',
}

const buttonStyle: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '6px',
  marginTop: '12px',
}

/**
 * The "AI Analysis" drawer tab: fetches a run's cached structured diagnosis and
 * renders it as a card (root-cause badge, confidence, evidence, next steps), or
 * offers to generate one. Read-only — it never mutates tests or code.
 */
export function AiInsightsTab({ runId, apiFetch, t }: Props) {
  const ai = useAiAnalysis(apiFetch)
  // Load the cached diagnosis whenever the run changes.
  useEffect(() => {
    void ai.load(runId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  if (ai.loading) {
    return (
      <div className={styles.summaryPlaceholder} data-testid="ai-loading">
        <Sparkles size={28} />
        <p>{t('aiAnalyzing')}</p>
      </div>
    )
  }
  if (!ai.enabled) {
    return (
      <div className={styles.summaryPlaceholder} data-testid="ai-disabled">
        <Sparkles size={28} />
        <p>{t('aiNotConfigured')}</p>
      </div>
    )
  }
  if (ai.error) {
    return (
      <div className={styles.summaryPlaceholder} data-testid="ai-error" role="alert">
        <AlertTriangle size={28} />
        <p>{t('aiError')}</p>
      </div>
    )
  }
  if (!ai.diagnosis) {
    return (
      <div className={styles.summaryPlaceholder} data-testid="ai-empty">
        <Sparkles size={28} />
        <p>{ai.detail ?? t('aiNoDiagnosis')}</p>
        {!ai.detail && (
          <button type="button" data-testid="ai-generate" onClick={() => void ai.generate(runId)} style={buttonStyle}>
            <RefreshCw size={14} /> {t('aiGenerate')}
          </button>
        )}
      </div>
    )
  }

  const d = ai.diagnosis
  return (
    <div data-testid="ai-diagnosis" style={{ padding: '4px 2px', color: 'var(--text-main)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
        <span style={badgeStyle}>{t(CATEGORY_KEY[d.category] ?? 'aiCatEnvironment')}</span>
        <span style={{ fontSize: '12px', opacity: 0.8 }}>
          {t('aiConfidence')}: {d.confidence}
        </span>
        {d.is_likely_regression && (
          <span style={{ ...badgeStyle, borderColor: '#f59e0b', color: '#f59e0b' }}>
            <Shuffle size={12} /> {t('aiLikelyRegression')}
          </span>
        )}
      </div>
      <p style={{ marginTop: '10px', lineHeight: 1.5 }}>{d.summary}</p>
      {d.evidence.length > 0 && (
        <div style={{ marginTop: '8px' }}>
          <h4 style={{ margin: '4px 0' }}>{t('aiEvidence')}</h4>
          <ul style={{ margin: 0, paddingLeft: '18px' }}>
            {d.evidence.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {d.next_steps.length > 0 && (
        <div style={{ marginTop: '8px' }}>
          <h4 style={{ margin: '4px 0' }}>{t('aiNextSteps')}</h4>
          <ul style={{ margin: 0, paddingLeft: '18px' }}>
            {d.next_steps.map((s, i) => (
              <li key={i}>
                <strong>{s.action}</strong>
                {s.reference ? ` (${s.reference})` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      <button type="button" data-testid="ai-regenerate" onClick={() => void ai.generate(runId)} style={buttonStyle}>
        <RefreshCw size={14} /> {t('aiRegenerate')}
      </button>
    </div>
  )
}
