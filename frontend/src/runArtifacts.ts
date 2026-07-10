// Pure helpers for the report tab's downloadable-artifact grouping. Kept in a
// .ts module (mirrors runDiff.ts/runCaseHistory.ts) so vitest covers the
// classification/sort/format logic that the .tsx drawer only renders.

import type { TranslationKey } from './i18n'
import type { RunArtifact } from './types'

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes % 1024 === 0 ? 0 : 1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export type ArtifactGroupKey = 'trace' | 'screenshot' | 'video' | 'other'

export const ARTIFACT_GROUPS: Array<{ key: ArtifactGroupKey; labelKey: TranslationKey }> = [
  { key: 'trace', labelKey: 'artifactGroupTrace' },
  { key: 'screenshot', labelKey: 'artifactGroupScreenshot' },
  { key: 'video', labelKey: 'artifactGroupVideo' },
  { key: 'other', labelKey: 'artifactGroupOther' },
]

const ARTIFACT_GROUP_ORDER: Record<ArtifactGroupKey, number> = {
  trace: 0,
  screenshot: 1,
  video: 2,
  other: 3,
}

export function artifactGroupKey(artifact: RunArtifact): ArtifactGroupKey {
  const path = artifact.path.toLowerCase()
  const contentType = artifact.content_type.toLowerCase()
  if (path.endsWith('trace.zip') || path.includes('/trace.')) return 'trace'
  if (
    contentType.startsWith('image/') ||
    /\.(png|jpe?g|webp)$/.test(path)
  ) return 'screenshot'
  if (contentType.startsWith('video/') || /\.(webm|mp4)$/.test(path)) return 'video'
  return 'other'
}

export function groupRunArtifacts(artifacts: RunArtifact[]) {
  const sorted = artifacts
    .map((artifact) => ({ artifact, group: artifactGroupKey(artifact) }))
    .sort((a, b) => {
      const groupDelta = ARTIFACT_GROUP_ORDER[a.group] - ARTIFACT_GROUP_ORDER[b.group]
      if (groupDelta !== 0) return groupDelta
      return a.artifact.path.localeCompare(b.artifact.path)
    })

  return ARTIFACT_GROUPS.map((group) => ({
    ...group,
    items: sorted
      .map((item, index) => ({ ...item, index }))
      .filter((item) => item.group === group.key),
  })).filter((group) => group.items.length > 0)
}

export function traceViewerCommand(artifact: RunArtifact): string {
  return `npx playwright show-trace ${artifact.path}`
}
