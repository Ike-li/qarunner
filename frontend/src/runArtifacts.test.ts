import { describe, expect, it } from 'vitest'

import { artifactGroupKey, formatBytes, groupRunArtifacts, traceViewerCommand } from './runArtifacts'
import type { RunArtifact } from './types'

const mkArtifact = (path: string, content_type: string, size_bytes = 100): RunArtifact => ({
  path,
  content_type,
  size_bytes,
})

describe('formatBytes', () => {
  it('renders sub-KB sizes as whole bytes', () => {
    expect(formatBytes(512)).toBe('512 B')
  })

  it('renders exact KB sizes without a decimal', () => {
    expect(formatBytes(2048)).toBe('2 KB')
  })

  it('renders non-exact KB sizes with one decimal', () => {
    expect(formatBytes(1536)).toBe('1.5 KB')
  })

  it('renders MB sizes with one decimal', () => {
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
  })
})

describe('artifactGroupKey', () => {
  it('classifies a trace.zip by filename', () => {
    expect(artifactGroupKey(mkArtifact('failed-case/trace.zip', 'application/zip'))).toBe('trace')
  })

  it('classifies images by content type', () => {
    expect(artifactGroupKey(mkArtifact('shot.png', 'image/png'))).toBe('screenshot')
  })

  it('classifies images by extension when content type is generic', () => {
    expect(artifactGroupKey(mkArtifact('shot.jpg', 'application/octet-stream'))).toBe('screenshot')
  })

  it('classifies videos by content type', () => {
    expect(artifactGroupKey(mkArtifact('run.webm', 'video/webm'))).toBe('video')
  })

  it('falls back to other for unrecognised artifacts', () => {
    expect(artifactGroupKey(mkArtifact('metadata.json', 'application/json'))).toBe('other')
  })
})

describe('groupRunArtifacts', () => {
  it('orders groups trace, screenshot, video, other and drops empty groups', () => {
    const artifacts = [
      mkArtifact('shot.png', 'image/png'),
      mkArtifact('trace.zip', 'application/zip'),
      mkArtifact('metadata.json', 'application/json'),
    ]
    const groups = groupRunArtifacts(artifacts)
    expect(groups.map((g) => g.key)).toEqual(['trace', 'screenshot', 'other'])
  })

  it('sorts artifacts within a group by path', () => {
    const artifacts = [mkArtifact('b.png', 'image/png'), mkArtifact('a.png', 'image/png')]
    const groups = groupRunArtifacts(artifacts)
    expect(groups[0].items.map((i) => i.artifact.path)).toEqual(['a.png', 'b.png'])
  })

  it('assigns a stable overall index across groups', () => {
    const artifacts = [
      mkArtifact('b.png', 'image/png'),
      mkArtifact('trace.zip', 'application/zip'),
      mkArtifact('a.png', 'image/png'),
    ]
    const groups = groupRunArtifacts(artifacts)
    const indexByPath = Object.fromEntries(
      groups.flatMap((g) => g.items.map((i) => [i.artifact.path, i.index])),
    )
    // trace group (index 0) sorts first, then screenshot group a.png/b.png.
    expect(indexByPath['trace.zip']).toBe(0)
    expect(indexByPath['a.png']).toBe(1)
    expect(indexByPath['b.png']).toBe(2)
  })

  it('returns no groups for an empty artifact list', () => {
    expect(groupRunArtifacts([])).toEqual([])
  })
})

describe('traceViewerCommand', () => {
  it('builds the playwright show-trace command for the artifact path', () => {
    expect(traceViewerCommand(mkArtifact('failed-case/trace.zip', 'application/zip'))).toBe(
      'npx playwright show-trace failed-case/trace.zip',
    )
  })
})
