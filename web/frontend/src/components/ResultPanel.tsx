import type { Segment } from '../types'

interface ResultPanelProps {
  segments: Segment[]
  selectedIndex: number | null
  onSelect: (index: number) => void
  onToggleKeep: (index: number) => void
  onChangeLabel: (index: number, label: string) => void
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  const ms = Math.floor((seconds % 1) * 100)
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${ms.toString().padStart(2, '0')}`
}

export default function ResultPanel({
  segments,
  selectedIndex,
  onSelect,
  onToggleKeep,
  onChangeLabel,
}: ResultPanelProps) {
  const keptCount = segments.filter((s) => s.keep).length
  const totalDuration = segments.filter((s) => s.keep).reduce((sum, s) => sum + (s.end_time - s.start_time), 0)

  return (
    <div className="result-panel">
      <div className="result-header">
        <div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>AI 剪辑结果</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
            已选中 {keptCount}/{segments.length} 个片段，成片时长约 {formatTime(totalDuration)}
          </div>
        </div>
      </div>

      <div className="result-list">
        {segments.map((seg, idx) => (
          <div
            key={idx}
            className={`result-card ${selectedIndex === idx ? 'selected' : ''} ${!seg.keep ? 'disabled' : ''}`}
            onClick={() => onSelect(idx)}
          >
            <div className="result-card-header">
              <input
                type="checkbox"
                className="result-check"
                checked={seg.keep}
                onClick={(e) => e.stopPropagation()}
                onChange={() => onToggleKeep(idx)}
                title={seg.keep ? '保留该片段' : '删除该片段'}
              />
              <span className="result-time">
                {formatTime(seg.start_time)} - {formatTime(seg.end_time)}
              </span>
              <span className="result-duration">({formatTime(seg.end_time - seg.start_time)})</span>
              <div className="confidence-bar" title={`置信度 ${(seg.confidence * 100).toFixed(0)}%`}>
                <div className="confidence-fill" style={{ width: `${seg.confidence * 100}%` }} />
              </div>
              <span className="confidence-text">{(seg.confidence * 100).toFixed(0)}%</span>
            </div>
            <input
              className="result-reason"
              value={seg.label}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => onChangeLabel(idx, e.target.value)}
              placeholder="AI 选择理由..."
            />
          </div>
        ))}

        {segments.length === 0 && (
          <div className="result-empty">
            尚未分析。设置平台和剪辑意图后，点击左侧“AI 分析”开始。
          </div>
        )}
      </div>
    </div>
  )
}
