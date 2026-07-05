import { useState, useEffect, useCallback } from 'react'
import type { SuiteInfo, TreeNode } from '../types'

interface UseSuitesOpts {
  apiFetch: (path: string, opts?: RequestInit) => Promise<Response>
  enabled: boolean
  lang: string
}

/**
 * Suites / tests data layer — list suites, file tree, markers, and
 * git-suite lifecycle actions (clone/pull/prepare/delete).
 */
export function useSuites({ apiFetch, enabled, lang }: UseSuitesOpts) {
  const [tests, setTests] = useState<string[]>([])
  const [suites, setSuites] = useState<SuiteInfo[]>([])
  const [scannedFilesTree, setScannedFilesTree] = useState<TreeNode[]>([])
  const [scannedMarkers, setScannedMarkers] = useState<string[]>([])
  const [suiteLoadError, setSuiteLoadError] = useState(false)

  // ── fetch ──────────────────────────────────────────────────────────────

  const fetchTests = useCallback(async () => {
    setSuiteLoadError(false)
    try {
      const resp = await apiFetch('/suites')
      if (!resp.ok) throw new Error(`Suites request failed: ${resp.status}`)
      const data: SuiteInfo[] = await resp.json()
      setSuites(data)
      setTests(data.map((s) => s.name))
      setSuiteLoadError(false)
    } catch (err) {
      console.error('Error fetching test directories:', err)
      setSuites([])
      setTests([])
      setSuiteLoadError(true)
    }
  }, [apiFetch])

  useEffect(() => {
    if (enabled) fetchTests()
  }, [enabled, fetchTests])

  // ── tree + markers ─────────────────────────────────────────────────────

  const fetchSuiteMetadata = useCallback(
    async (suiteName: string) => {
      if (!suiteName) return
      try {
        const treeResp = await apiFetch(
          `/tests/${encodeURIComponent(suiteName)}/tree`,
        )
        if (treeResp.ok) {
          setScannedFilesTree(await treeResp.json())
        } else {
          setScannedFilesTree([])
        }

        const markersResp = await apiFetch(
          `/tests/${encodeURIComponent(suiteName)}/markers`,
        )
        if (markersResp.ok) {
          setScannedMarkers(await markersResp.json())
        } else {
          setScannedMarkers([])
        }
      } catch (err) {
        console.error('Error fetching suite metadata:', err)
        setScannedFilesTree([])
        setScannedMarkers([])
      }
    },
    [apiFetch],
  )

  // ── git-suite lifecycle ────────────────────────────────────────────────

  const handlePullSuite = useCallback(
    async (name: string) => {
      try {
        const resp = await apiFetch(
          `/tests/${encodeURIComponent(name)}/pull`,
          { method: 'POST' },
        )
        const data = await resp.json()
        if (resp.ok) {
          alert(data.message || (lang === 'zh' ? '更新成功' : 'Updated'))
          await fetchTests()
        } else {
          alert(
            data.detail || (lang === 'zh' ? '更新失败' : 'Failed to update suite'),
          )
        }
      } catch (err) {
        console.error('Error pulling suite:', err)
      }
    },
    [apiFetch, lang, fetchTests],
  )

  const handlePrepareSuite = useCallback(
    async (name: string) => {
      try {
        const resp = await apiFetch(
          `/tests/${encodeURIComponent(name)}/prepare`,
          { method: 'POST' },
        )
        const data = await resp.json()
        if (resp.ok) {
          alert(
            data.message ||
              (lang === 'zh' ? '依赖已准备' : 'Dependencies prepared'),
          )
        } else {
          alert(
            data.detail ||
              (lang === 'zh' ? '准备依赖失败' : 'Failed to prepare dependencies'),
          )
        }
      } catch (err) {
        console.error('Error preparing suite:', err)
      }
    },
    [apiFetch, lang],
  )

  const handleDeleteSuite = useCallback(
    async (name: string, afterDelete?: () => void) => {
      const msg =
        lang === 'zh'
          ? `确定要移除套件「${name}」吗？此操作会删除其文件。`
          : `Remove suite "${name}"? This deletes its files.`
      if (!window.confirm(msg)) return
      try {
        const resp = await apiFetch(
          `/tests/${encodeURIComponent(name)}`,
          { method: 'DELETE' },
        )
        if (resp.ok) {
          afterDelete?.()
          await fetchTests()
        } else {
          const data = await resp.json()
          alert(
            data.detail || (lang === 'zh' ? '移除失败' : 'Failed to remove suite'),
          )
        }
      } catch (err) {
        console.error('Error removing suite:', err)
      }
    },
    [apiFetch, lang, fetchTests],
  )

  return {
    tests,
    suites,
    suiteLoadError,
    scannedFilesTree,
    scannedMarkers,
    fetchTests,
    fetchSuiteMetadata,
    handlePullSuite,
    handlePrepareSuite,
    handleDeleteSuite,
    _reset: () => {
      setTests([])
      setSuites([])
      setScannedFilesTree([])
      setScannedMarkers([])
      setSuiteLoadError(false)
    },
  } as const
}
