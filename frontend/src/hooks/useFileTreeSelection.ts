import { useCallback, useState } from 'react'
import type { TreeNode } from '../types'

export type NodeCheckState = 'checked' | 'partial' | 'unchecked'

/** Collect every file path nested under a node (the node itself if it is a file). */
function collectFiles(node: TreeNode): string[] {
  if (!node.is_dir) {
    return [node.path]
  }
  let paths: string[] = []
  if (node.children) {
    for (const child of node.children) {
      paths = paths.concat(collectFiles(child))
    }
  }
  return paths
}

/**
 * Owns the test-suite tree selection state (which files are checked, which
 * folders are expanded) plus the tri-state checkbox logic. Lifted out of App so
 * the recursive <TestFileTree> stays presentational; App still reads
 * `selectedFiles`/`setSelectedFiles`/`setExpandedFolders` for the trigger &
 * profile-save flows.
 */
export function useFileTreeSelection() {
  const [selectedFiles, setSelectedFiles] = useState<string[]>([])
  const [expandedFolders, setExpandedFolders] = useState<string[]>([])

  const getNodeCheckState = useCallback(
    (node: TreeNode): NodeCheckState => {
      if (!node.is_dir) {
        return selectedFiles.includes(node.path) ? 'checked' : 'unchecked'
      }
      const descendantFiles = collectFiles(node)
      if (descendantFiles.length === 0) return 'unchecked'
      const checkedCount = descendantFiles.filter(p => selectedFiles.includes(p)).length
      if (checkedCount === descendantFiles.length) {
        return 'checked'
      } else if (checkedCount > 0) {
        return 'partial'
      }
      return 'unchecked'
    },
    [selectedFiles],
  )

  const handleToggleNode = useCallback(
    (node: TreeNode) => {
      const descendantFiles = collectFiles(node)
      const currentState = getNodeCheckState(node)

      if (currentState === 'checked') {
        setSelectedFiles(prev => prev.filter(p => !descendantFiles.includes(p)))
      } else {
        setSelectedFiles(prev => {
          const filtered = prev.filter(p => !descendantFiles.includes(p))
          return [...filtered, ...descendantFiles]
        })
      }
    },
    [getNodeCheckState],
  )

  const toggleFolder = useCallback((path: string) => {
    setExpandedFolders(prev =>
      prev.includes(path) ? prev.filter(p => p !== path) : [...prev, path],
    )
  }, [])

  return {
    selectedFiles,
    setSelectedFiles,
    expandedFolders,
    setExpandedFolders,
    getNodeCheckState,
    handleToggleNode,
    toggleFolder,
  }
}
