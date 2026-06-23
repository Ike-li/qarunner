import { ChevronRight, FolderGit2, SlidersHorizontal } from 'lucide-react'
import styles from '../App.module.css'
import { activateOnKey } from '../a11y'
import type { TreeNode } from '../types'
import type { NodeCheckState } from '../hooks/useFileTreeSelection'

interface TestFileTreeProps {
  nodes: TreeNode[]
  expandedFolders: string[]
  onToggleFolder: (path: string) => void
  getNodeCheckState: (node: TreeNode) => NodeCheckState
  onToggleNode: (node: TreeNode) => void
}

/** Recursive folder/file checkbox tree for selecting which tests to run.
 *  Selection/expansion state is owned by useFileTreeSelection in App. */
export function TestFileTree({
  nodes,
  expandedFolders,
  onToggleFolder,
  getNodeCheckState,
  onToggleNode,
}: TestFileTreeProps) {
  const renderNode = (node: TreeNode, depth = 0) => {
    const isFolder = node.is_dir
    const isExpanded = expandedFolders.includes(node.path)
    const checkState = getNodeCheckState(node)

    return (
      <div key={node.path} className={styles.treeNode} style={{ marginLeft: `${depth * 0.75}rem` }}>
        <div className={styles.treeRow}>
          {isFolder ? (
            <button
              type="button"
              className={`${styles.treeExpandButton} ${isExpanded ? styles.treeExpandButtonExpanded : ''}`}
              onClick={() => onToggleFolder(node.path)}
            >
              <ChevronRight size={14} />
            </button>
          ) : (
            <div style={{ width: '16px' }} />
          )}

          {/* Mouse-only redundant hit target; keyboard toggling lives on the named label below (one tab stop per node). */}
          <div className={styles.treeCheckboxWrapper} onClick={() => onToggleNode(node)}>
            <div className={`${styles.treeCheckbox} ${checkState === 'checked' ? styles.treeCheckboxChecked : checkState === 'partial' ? styles.treeCheckboxPartial : ''}`} />
          </div>

          <div
            className={`${styles.treeLabel} ${isFolder ? styles.treeNodeFolder : styles.treeNodeFile}`}
            role="button"
            tabIndex={0}
            onClick={() => onToggleNode(node)}
            onKeyDown={activateOnKey(() => onToggleNode(node))}
          >
            {isFolder ? <FolderGit2 size={14} className={styles.treeIcon} /> : <SlidersHorizontal size={12} className={styles.treeIcon} />}
            <span>{node.name}</span>
          </div>
        </div>

        {isFolder && isExpanded && node.children && (
          <div className={styles.treeChildren}>
            {node.children.map((child: TreeNode) => renderNode(child, depth + 1))}
          </div>
        )}
      </div>
    )
  }

  return <>{nodes.map(node => renderNode(node))}</>
}
