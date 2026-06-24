import { useState } from 'react'

/**
 * Hook to manage test-suite tree selection state (checked files and expanded folders).
 * Simplified since Tree state is now managed directly by Semi UI.
 */
export function useFileTreeSelection() {
  const [selectedFiles, setSelectedFiles] = useState<string[]>([])
  const [expandedFolders, setExpandedFolders] = useState<string[]>([])

  return {
    selectedFiles,
    setSelectedFiles,
    expandedFolders,
    setExpandedFolders,
  }
}
