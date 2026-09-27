import { useEffect, useMemo, useState } from 'react'
import type { Segment, TranscriptSentence } from '../types'
import * as api from '../api'

interface TranscriptEditorProps {
  projectId: string
  transcript: TranscriptSentence[]
  segments: Segment[]
  duration: number
  selectedIndex: number | null
  targetDuration: number
  onTranscriptChange: (transcript: TranscriptSentence[]) => void
  onSegmentsChange: (segments: Segment[]) => void
  onSelectSegment: (index: number) => void
  onSeek: (time: number) => void
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

export default function TranscriptEditor({
  projectId,
  transcript,
  segments,
  duration: _duration,
  selectedIndex: _selectedIndex,
  targetDuration,
  onTranscriptChange,
  onSegmentsChange,
  onSelectSegment,
  onSeek,
}: TranscriptEditorProps) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)

  const keptDuration = useMemo(() => {
    return segments.filter(s => s.keep).reduce((sum, s) => sum + (s.end_time - s.start_time), 0)
  }, [segments])

  // 当 transcript keep 状态变化时，重新计算 segments
  useEffect(() => {
    if (!transcript.length) return
    let cancelled = false
    setLoading(true)
    api
      .previewTranscriptSegments(projectId, transcript)
      .then(res => {
        if (cancelled) return
        const next = res.segments.map(s => ({
          start_time: s.time_range[0],
          end_time: s.time_range[1],
          keep: s.action === 'retain',
          label: s.label,
          confidence: 0.9,
        }))
        onSegmentsChange(next)
      })
      .catch(() => {
        // 降级：本地合并
        const next = localTranscriptToSegments(transcript)
        onSegmentsChange(next)
      })
      .finally(() => setLoading(false))
    return () => {
      cancelled = true
    }
  }, [transcript, projectId])

  const toggleSentence = (idx: number) => {
    const next = [...transcript]
    next[idx] = { ...next[idx], keep: !next[idx].keep }
    onTranscriptChange(next)
  }

  const handleSeek = (sent: TranscriptSentence) => {
    onSeek((sent.start + sent.end) / 2)
    // 找到包含这个时间的 segment index
    const segIdx = segments.findIndex(s => s.start_time <= sent.start && s.end_time >= sent.end)
    if (segIdx >= 0) onSelectSegment(segIdx)
  }

  if (!transcript.length) {
    return (
      <div className="transcript-editor empty">
        尚未分析。设置平台和剪辑意图后，点击“AI 分析”开始。
      </div>
    )
  }

  return (
    <div className="transcript-editor">
      <div className="transcript-header">
        <div>
          <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: '0.14em' }}>口播稿 · 点句即刀</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
            成片 {formatTime(keptDuration)}
            {targetDuration > 0 ? ` / 目标 ${formatTime(targetDuration)}` : ''}
            {' · '}
            {transcript.filter(s => s.keep !== false).length}/{transcript.length} 句保留
            {' · '}勾选后直接导出，不用重跑 AI
          </div>
        </div>
        {loading && <span className="transcript-loading">计算中...</span>}
      </div>

      <div className="transcript-list">
        {transcript.map((sent, idx) => {
          const keep = sent.keep !== false
          const isHovered = hoveredIdx === idx
          return (
            <div
              key={idx}
              className={`transcript-row ${keep ? 'keep' : 'drop'} ${isHovered ? 'hover' : ''}`}
              onMouseEnter={() => setHoveredIdx(idx)}
              onMouseLeave={() => setHoveredIdx(null)}
              onClick={() => handleSeek(sent)}
            >
              <input
                type="checkbox"
                className="transcript-check"
                checked={keep}
                onClick={e => e.stopPropagation()}
                onChange={() => toggleSentence(idx)}
              />
              <span className="transcript-time">{formatTime(sent.start)}</span>
              <span className={`transcript-text ${keep ? '' : 'strikethrough'}`}>{sent.text}</span>
              {sent.reason && <span className="transcript-reason" title={sent.reason}>{sent.reason}</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function localTranscriptToSegments(transcript: TranscriptSentence[]): Segment[] {
  if (!transcript.length) return []
  const out: Segment[] = []
  let currentKeep: boolean | null = null
  let start = 0
  let end = 0
  let labels: string[] = []

  for (const sent of transcript) {
    const keep = sent.keep !== false
    if (currentKeep === null) {
      currentKeep = keep
      start = sent.start
      end = sent.end
      labels.push(sent.text.slice(0, 20))
    } else if (keep === currentKeep && sent.start <= end + 0.3) {
      end = sent.end
      labels.push(sent.text.slice(0, 20))
    } else {
      out.push({
        start_time: start,
        end_time: end,
        keep: currentKeep,
        label: labels.slice(0, 3).join(' + ') || (currentKeep ? '保留' : '删除'),
        confidence: 0.9,
      })
      currentKeep = keep
      start = sent.start
      end = sent.end
      labels = [sent.text.slice(0, 20)]
    }
  }

  if (currentKeep !== null) {
    out.push({
      start_time: start,
      end_time: end,
      keep: currentKeep,
      label: labels.slice(0, 3).join(' + ') || (currentKeep ? '保留' : '删除'),
      confidence: 0.9,
    })
  }
  return out
}
