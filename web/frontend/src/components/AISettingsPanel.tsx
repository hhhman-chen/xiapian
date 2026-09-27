import { useEffect, useState } from 'react'
import type { Platform } from '../api'
import { listPlatforms } from '../api'

interface AISettingsPanelProps {
  userPrompt: string
  onUserPromptChange: (value: string) => void
  platform: string
  onPlatformChange: (value: string) => void
  withSubtitle: boolean
  onWithSubtitleChange: (value: boolean) => void
  targetDuration: number
  onTargetDurationChange: (value: number) => void
}

export default function AISettingsPanel({
  userPrompt,
  onUserPromptChange,
  platform,
  onPlatformChange,
  withSubtitle,
  onWithSubtitleChange,
  targetDuration,
  onTargetDurationChange,
}: AISettingsPanelProps) {
  const [platforms, setPlatforms] = useState<Platform[]>([])

  useEffect(() => {
    listPlatforms().then(setPlatforms).catch(() => setPlatforms([]))
  }, [])

  return (
    <div style={{ padding: 12, borderBottom: '1px solid var(--border)' }}>
      <div style={{ marginBottom: 10 }}>
        <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
          目标平台
        </label>
        <select
          value={platform}
          onChange={(e) => onPlatformChange(e.target.value)}
          style={{ width: '100%' }}
        >
          {platforms.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      </div>

      <div className="intent-box">
        <label>
          剪辑意图（给 AI 的要求 · 不写就按下面默认句粗剪）
        </label>
        <textarea
          rows={4}
          value={userPrompt}
          onChange={(e) => onUserPromptChange(e.target.value)}
          placeholder="去掉口头禅、倒计时、重复和废话，保留完整叙事。说到第一、第二时做成要点卡。"
        />
      </div>

      <div style={{ marginBottom: 10 }}>
        <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
          目标成片时长（秒，0 = 不强制）
        </label>
        <input
          type="number"
          min={0}
          max={3600}
          value={targetDuration}
          onChange={(e) => onTargetDurationChange(Number(e.target.value))}
          style={{ width: '100%' }}
        />
      </div>

      <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
        <input
          type="checkbox"
          checked={withSubtitle}
          onChange={(e) => onWithSubtitleChange(e.target.checked)}
        />
        导出时生成逐字字幕
      </label>
    </div>
  )
}
