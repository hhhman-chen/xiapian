import type { Segment } from '../types'

interface SegmentListProps {
  segments: Segment[]
  selectedIndex: number | null
  onSelect: (index: number) => void
  onToggleKeep: (index: number) => void
  onChangeLabel: (index: number, label: string) => void
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

export default function SegmentList({
  segments,
  selectedIndex,
  onSelect,
  onToggleKeep,
  onChangeLabel,
}: SegmentListProps) {
  return (
    <div className="segment-list">
      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
        片段列表 ({segments.length})
      </div>
      {segments.map((seg, idx) => (
        <div
          key={idx}
          className={`segment-item ${selectedIndex === idx ? 'selected' : ''}`}
          onClick={() => onSelect(idx)}
        >
          <input
            type="checkbox"
            className="seg-check"
            checked={seg.keep}
            onClick={(e) => e.stopPropagation()}
            onChange={() => onToggleKeep(idx)}
          />
          <input
            className="seg-label"
            value={seg.label}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => onChangeLabel(idx, e.target.value)}
          />
          <span className="seg-time">
            {formatTime(seg.start_time)}-{formatTime(seg.end_time)}
          </span>
        </div>
      ))}
    </div>
  )
}
