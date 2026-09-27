import type { TaskStatus } from '../types'

interface ToolbarProps {
  segmentsCount: number
  keptCount: number
  analyzeStatus?: TaskStatus | null
  exportStatus?: TaskStatus | null
  onAnalyze: () => void
  onExport: () => void
  onSave: () => void
  onDownload: () => void
}

function statusText(status?: TaskStatus | null): string {
  if (!status || status.status === 'idle') return ''
  const labels: Record<string, string> = {
    pending: '等待中',
    running: '进行中',
    done: '已完成',
    error: '失败',
  }
  const name = status.task === 'analyze' ? 'AI 分析' : '视频导出'
  return `${name}: ${labels[status.status] || status.status}`
}

export default function Toolbar({
  segmentsCount,
  keptCount,
  analyzeStatus,
  exportStatus,
  onAnalyze,
  onExport,
  onSave,
  onDownload,
}: ToolbarProps) {
  const analyzing = analyzeStatus?.status === 'running' || analyzeStatus?.status === 'pending'
  const exporting = exportStatus?.status === 'running' || exportStatus?.status === 'pending'
  const analyzeDone = analyzeStatus?.status === 'done'
  const exportDone = exportStatus?.status === 'done'
  const running = analyzing || exporting

  const analyzeProgress = analyzeStatus?.progress ?? 0
  const exportProgress = exportStatus?.progress ?? 0

  return (
    <div className="toolbar">
      <div className="toolbar-group">
        <button onClick={onAnalyze} disabled={running} className={analyzeDone ? 'secondary' : ''}>
          {analyzing ? `AI 分析中 ${analyzeProgress}%` : analyzeDone ? '重新分析' : 'AI 分析'}
        </button>

        {analyzeDone && (
          <button
            onClick={onExport}
            disabled={running || keptCount === 0}
            className="primary"
            title={keptCount === 0 ? '请至少保留一个片段' : undefined}
          >
            {exporting ? `生成成片 ${exportProgress}%` : exportDone ? '按微调重新导出' : '按当前稿导出'}
          </button>
        )}

        <button onClick={onSave} disabled={running} className="secondary">
          保存项目
        </button>
      </div>

      <div className="toolbar-status">
        {exportDone && (
          <button onClick={onDownload} className="success">
            下载成片
          </button>
        )}
        <div className="status-text">
          {segmentsCount > 0 && (
            <span style={{ marginRight: 12 }}>
              已选 {keptCount}/{segmentsCount} 片段
            </span>
          )}
          {statusText(analyzeStatus)} {statusText(exportStatus)}
          {analyzeStatus?.error && ` 错误: ${analyzeStatus.error}`}
          {exportStatus?.error && ` 错误: ${exportStatus.error}`}
        </div>
      </div>
    </div>
  )
}
