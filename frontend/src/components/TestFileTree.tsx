import { useMemo } from 'react'
import { Tree } from '@douyinfe/semi-ui'
import type { TreeNodeData } from '@douyinfe/semi-ui/lib/es/tree/interface'
import { IconFolder, IconFile } from '@douyinfe/semi-icons'
import type { TreeNode } from '../types'

interface TestFileTreeProps {
  nodes: TreeNode[]
  selectedFiles: string[]
  setSelectedFiles: (files: string[]) => void
  expandedFolders: string[]
  setExpandedFolders: (folders: string[]) => void
}

/**
 * Enterprise-grade tree view for selecting pytest files to execute.
 * Leverages Semi UI's Tree component with native checkbox management,
 * keyboard accessibility, and custom icons.
 */
export function TestFileTree({
  nodes,
  selectedFiles,
  setSelectedFiles,
  expandedFolders,
  setExpandedFolders,
}: TestFileTreeProps) {
  
  // Transform our TreeNode format to Semi UI's treeData format
  const treeData = useMemo(() => {
    const transform = (items: TreeNode[]): TreeNodeData[] => {
      return items.map(node => ({
        label: node.name,
        key: node.path,
        isLeaf: !node.is_dir,
        icon: node.is_dir ? (
          <IconFolder style={{ color: 'var(--semi-color-warning)' }} />
        ) : (
          <IconFile style={{ color: 'var(--semi-color-primary)' }} />
        ),
        children: node.children && node.children.length > 0 ? transform(node.children) : undefined,
      }))
    }
    return transform(nodes)
  }, [nodes])

  // Collect all file paths (leaves) to filter out directories from checkedKeys
  const filePathsSet = useMemo(() => {
    const fileSet = new Set<string>()
    const traverse = (items: TreeNode[]) => {
      items.forEach(node => {
        if (!node.is_dir) {
          fileSet.add(node.path)
        }
        if (node.children) {
          traverse(node.children)
        }
      })
    }
    traverse(nodes)
    return fileSet
  }, [nodes])

  const handleExpand = (expandedKeys: string[]) => {
    setExpandedFolders(expandedKeys || [])
  }

  return (
    <Tree
      treeData={treeData}
      multiple
      value={selectedFiles}
      onChange={(checkedKeys) => {
        const keysArray = Array.isArray(checkedKeys) ? checkedKeys : []
        const onlyFiles = keysArray.filter((key): key is string => typeof key === 'string' && filePathsSet.has(key))
        setSelectedFiles(onlyFiles)
      }}
      expandedKeys={expandedFolders}
      onExpand={handleExpand}
      style={{
        width: '100%',
        padding: '0.5rem',
        maxHeight: '320px',
        overflowY: 'auto'
      }}
    />
  )
}
