import { useEffect, useMemo, useRef } from 'react'
import type { Project } from '../types'
import { videoUrl } from '../api'

interface VideoPlayerProps {
  projectId: string
  project: Project
  currentTime: number
  onTimeUpdate: (t: number) => void
  onDurationChange?: (d: number) => void
}

export default function VideoPlayer({
  projectId,
  project,
  currentTime,
  onTimeUpdate,
  onDurationChange,
}: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (Math.abs(video.currentTime - currentTime) > 0.3) {
      video.currentTime = currentTime
    }
  }, [currentTime])

  const handleTimeUpdate = () => {
    const t = videoRef.current?.currentTime ?? 0
    onTimeUpdate(t)
  }

  const displayName = project.original_filename || project.video_path?.split('/').pop() || '未知视频'
  const polish = project.polish
  const cap = useMemo(() => {
    if (!polish || polish.caption_style === 'none') return null
    return (polish.captions || []).find((c) => c.on && currentTime >= c.start && currentTime < c.end) || null
  }, [polish, currentTime])
  const cssFilter = useMemo(() => {
    const b = polish?.beauty ?? 0
    const look = polish?.look || 'none'
    const blur = b > 0 ? `blur(${(b / 80) * 0.55}px) brightness(${1 + b / 280}) saturate(${1 + b / 400})` : ''
    const looks: Record<string, string> = {
      none: '',
      warm: 'sepia(0.18) saturate(1.15)',
      cool: 'hue-rotate(12deg) saturate(0.95)',
      bright: 'brightness(1.12) contrast(1.05)',
    }
    return [blur, looks[look] || ''].filter(Boolean).join(' ') || 'none'
  }, [polish])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 12 }}>
      <div className="player-stage">
        <video
          ref={videoRef}
          src={videoUrl(projectId)}
          controls
          style={{
            width: '100%',
            maxHeight: '100%',
            background: '#000',
            filter: cssFilter,
          }}
          onTimeUpdate={handleTimeUpdate}
          onLoadedMetadata={() => {
            onDurationChange?.(videoRef.current?.duration ?? project.duration)
          }}
        />
        {cap && polish && (
          <div className={`live-cap live-cap-${polish.caption_style}`}>{cap.text}</div>
        )}
      </div>
      <div
        style={{
          marginTop: 8,
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 12,
          color: 'var(--text-muted)',
        }}
      >
        <span>{displayName}</span>
        <span>{Math.round(project.duration)}s · {project.width}x{project.height} · {Math.round(project.fps)}fps</span>
      </div>
    </div>
  )
}
