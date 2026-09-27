import { useEffect, useRef, useCallback } from 'react'
import type { Segment } from '../types'

interface TimelineProps {
  duration: number
  segments: Segment[]
  currentTime: number
  selectedIndex: number | null
  onSeek: (t: number) => void
  onToggleKeep: (index: number) => void
  onResize: (index: number, start: number, end: number) => void
}

const RULER_HEIGHT = 28
const SEGMENT_HEIGHT = 44
const ROW_SPACING = 8
const BASE_PPS = 40
const MIN_SEGMENT_WIDTH = 12

export default function Timeline({
  duration,
  segments,
  currentTime,
  selectedIndex,
  onSeek,
  onToggleKeep,
  onResize,
}: TimelineProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const stateRef = useRef({
    zoom: 1,
    scrollX: 0,
    drag: null as
      | { type: 'move' | 'left' | 'right'; index: number; startX: number; originalStart: number; originalEnd: number }
      | null,
    hoverIndex: null as number | null,
    resizeHover: null as 'left' | 'right' | null,
  })

  const getMetrics = useCallback(() => {
    const pps = BASE_PPS * stateRef.current.zoom
    const totalWidth = Math.max(duration * pps, 1)
    return { pps, totalWidth }
  }, [duration])

  const timeToX = useCallback(
    (t: number) => {
      const { pps } = getMetrics()
      return t * pps - stateRef.current.scrollX
    },
    [getMetrics],
  )

  const xToTime = useCallback(
    (x: number) => {
      const { pps } = getMetrics()
      return (x + stateRef.current.scrollX) / pps
    },
    [getMetrics],
  )

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return

    const dpr = window.devicePixelRatio || 1
    const cssWidth = canvas.clientWidth
    const cssHeight = canvas.clientHeight
    canvas.width = cssWidth * dpr
    canvas.height = cssHeight * dpr
    ctx.scale(dpr, dpr)

    const { pps, totalWidth } = getMetrics()

    // 背景
    ctx.fillStyle = '#16161d'
    ctx.fillRect(0, 0, cssWidth, cssHeight)

    // 时间标尺
    ctx.fillStyle = '#121214'
    ctx.fillRect(0, 0, cssWidth, RULER_HEIGHT)
    ctx.strokeStyle = '#33333d'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(0, RULER_HEIGHT)
    ctx.lineTo(cssWidth, RULER_HEIGHT)
    ctx.stroke()

    ctx.fillStyle = '#9ca3af'
    ctx.font = '11px sans-serif'
    ctx.textAlign = 'center'
    const step = Math.max(1, Math.floor(20 / pps))
    for (let t = 0; t <= duration; t += step) {
      const x = timeToX(t)
      if (x >= -20 && x <= cssWidth + 20) {
        ctx.fillText(`${Math.floor(t)}s`, x, RULER_HEIGHT - 8)
        ctx.beginPath()
        ctx.moveTo(x, RULER_HEIGHT - 4)
        ctx.lineTo(x, RULER_HEIGHT)
        ctx.stroke()
      }
    }

    // 片段
    segments.forEach((seg, idx) => {
      const x = timeToX(seg.start_time)
      const w = Math.max((seg.end_time - seg.start_time) * pps, MIN_SEGMENT_WIDTH)
      const y = RULER_HEIGHT + 10 + idx * (SEGMENT_HEIGHT + ROW_SPACING)

      const isSelected = selectedIndex === idx
      const isHover = stateRef.current.hoverIndex === idx

      ctx.fillStyle = seg.keep ? '#22c55e' : '#ef4444'
      if (isHover) ctx.fillStyle = seg.keep ? '#4ade80' : '#f87171'
      if (isSelected) ctx.fillStyle = seg.keep ? '#16a34a' : '#dc2626'

      ctx.beginPath()
      ctx.roundRect(x, y, w, SEGMENT_HEIGHT, 4)
      ctx.fill()

      // 边界拖拽区
      ctx.fillStyle = 'rgba(255,255,255,0.2)'
      ctx.fillRect(x, y, 6, SEGMENT_HEIGHT)
      ctx.fillRect(x + w - 6, y, 6, SEGMENT_HEIGHT)

      // 文字
      ctx.fillStyle = '#fff'
      ctx.font = '12px sans-serif'
      ctx.textAlign = 'left'
      const label = seg.label || (seg.keep ? '保留' : '删除')
      ctx.fillText(label, x + 10, y + SEGMENT_HEIGHT / 2 + 4)
    })

    // 当前时间线
    const cx = timeToX(currentTime)
    ctx.strokeStyle = '#6366f1'
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(cx, RULER_HEIGHT)
    ctx.lineTo(cx, cssHeight)
    ctx.stroke()

    // 滚动条背景（底部）
    if (totalWidth > cssWidth) {
      const sbHeight = 6
      const sbY = cssHeight - sbHeight - 2
      ctx.fillStyle = '#33333d'
      ctx.fillRect(0, sbY, cssWidth, sbHeight)
      const sbWidth = Math.max((cssWidth / totalWidth) * cssWidth, 20)
      const sbX = (stateRef.current.scrollX / (totalWidth - cssWidth)) * (cssWidth - sbWidth)
      ctx.fillStyle = '#6366f1'
      ctx.fillRect(sbX, sbY, sbWidth, sbHeight)
    }
  }, [duration, segments, currentTime, selectedIndex, getMetrics, timeToX])

  useEffect(() => {
    draw()
  }, [draw])

  const getSegmentAt = (clientX: number, clientY: number) => {
    const canvas = canvasRef.current
    if (!canvas) return null
    const rect = canvas.getBoundingClientRect()
    const x = clientX - rect.left
    const y = clientY - rect.top
    const t = xToTime(x)
    if (t < 0 || t > duration) return null

    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i]
      const sx = timeToX(seg.start_time)
      const sw = Math.max((seg.end_time - seg.start_time) * getMetrics().pps, MIN_SEGMENT_WIDTH)
      const sy = RULER_HEIGHT + 10 + i * (SEGMENT_HEIGHT + ROW_SPACING)
      if (y >= sy && y <= sy + SEGMENT_HEIGHT && t >= seg.start_time && t <= seg.end_time) {
        const nearLeft = Math.abs(x - sx) < 8
        const nearRight = Math.abs(x - (sx + sw)) < 8
        let edge: 'left' | 'right' | null = null
        if (nearLeft) edge = 'left'
        else if (nearRight) edge = 'right'
        return { index: i, edge, seg }
      }
    }
    return null
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const x = e.clientX - rect.left

    if (stateRef.current.drag) {
      const { type, index, startX, originalStart, originalEnd } = stateRef.current.drag
      const { pps } = getMetrics()
      const dx = (x - startX) / pps
      const seg = segments[index]
      let newStart = seg.start_time
      let newEnd = seg.end_time

      if (type === 'left') {
        newStart = Math.max(0, Math.min(originalStart + dx, originalEnd - 0.5))
      } else if (type === 'right') {
        newEnd = Math.min(duration, Math.max(originalEnd + dx, originalStart + 0.5))
      } else {
        const len = originalEnd - originalStart
        newStart = Math.max(0, originalStart + dx)
        newEnd = newStart + len
        if (newEnd > duration) {
          newEnd = duration
          newStart = duration - len
        }
      }
      onResize(index, newStart, newEnd)
      return
    }

    const hit = getSegmentAt(e.clientX, e.clientY)
    stateRef.current.hoverIndex = hit?.index ?? null
    stateRef.current.resizeHover = hit?.edge ?? null
    canvas.style.cursor = hit?.edge ? 'col-resize' : hit ? 'pointer' : 'default'
    draw()
  }

  const handleMouseDown = (e: React.MouseEvent) => {
    const hit = getSegmentAt(e.clientX, e.clientY)
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const x = e.clientX - rect.left

    if (hit) {
      const type = hit.edge || 'move'
      stateRef.current.drag = {
        type,
        index: hit.index,
        startX: x,
        originalStart: hit.seg.start_time,
        originalEnd: hit.seg.end_time,
      }
    } else {
      const t = Math.max(0, Math.min(xToTime(x), duration))
      onSeek(t)
    }
  }

  const handleMouseUp = () => {
    stateRef.current.drag = null
  }

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    const { totalWidth } = getMetrics()
    const canvas = canvasRef.current
    const cssWidth = canvas?.clientWidth ?? 0
    if (e.ctrlKey || e.metaKey) {
      const oldZoom = stateRef.current.zoom
      const newZoom = Math.max(0.3, Math.min(5, oldZoom * (e.deltaY > 0 ? 0.9 : 1.1)))
      stateRef.current.zoom = newZoom
    } else {
      stateRef.current.scrollX = Math.max(
        0,
        Math.min(stateRef.current.scrollX + e.deltaX, Math.max(0, totalWidth - cssWidth)),
      )
    }
    draw()
  }

  return (
    <div
      ref={containerRef}
      style={{
        flex: 1,
        overflow: 'hidden',
        position: 'relative',
        minHeight: 160,
      }}
    >
      <canvas
        ref={canvasRef}
        style={{
          width: '100%',
          height: '100%',
          display: 'block',
        }}
        onMouseMove={handleMouseMove}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={() => {
          stateRef.current.drag = null
          stateRef.current.hoverIndex = null
        }}
        onWheel={handleWheel}
        onDoubleClick={(e) => {
          const hit = getSegmentAt(e.clientX, e.clientY)
          if (hit) onToggleKeep(hit.index)
        }}
      />
    </div>
  )
}
