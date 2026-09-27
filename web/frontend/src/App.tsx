import { useCallback, useEffect, useRef, useState } from 'react'
import type { Polish, Project, ProjectSummary, Segment, TaskStatus, TranscriptSentence } from './types'
import * as api from './api'
import AISettingsPanel from './components/AISettingsPanel'
import UploadZone from './components/UploadZone'
import VideoPlayer from './components/VideoPlayer'
import TranscriptEditor from './components/TranscriptEditor'
import Timeline from './components/Timeline'
import Toolbar from './components/Toolbar'
import Login from './components/Login'
import Guide from './components/Guide'
import PolishPanel, { captionsFromTranscript, defaultPolish } from './components/PolishPanel'

const DEFAULT_INTENT =
  '去掉口头禅、倒计时、重复和废话，保留完整叙事。说到第一、第二时做成要点卡。'

export default function App() {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [projectId, setProjectId] = useState<string | null>(null)
  const [project, setProject] = useState<Project | null>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [analyzeStatus, setAnalyzeStatus] = useState<TaskStatus | null>(null)
  const [exportStatus, setExportStatus] = useState<TaskStatus | null>(null)
  const [status, setStatus] = useState<string>('')
  const [userPrompt, setUserPrompt] = useState(DEFAULT_INTENT)
  const [platform, setPlatform] = useState('douyin')
  const [withSubtitle, setWithSubtitle] = useState(true)
  const [rightTab, setRightTab] = useState<'script' | 'polish'>('script')
  const [targetDuration, setTargetDuration] = useState(0)
  const [user, setUser] = useState<{ username: string; name: string } | null>(null)
  const [authReady, setAuthReady] = useState(false)
  const [showGuide, setShowGuide] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    api.me()
      .then((u) => setUser({ username: u.username, name: u.name }))
      .catch(() => setUser(null))
      .finally(() => setAuthReady(true))
  }, [])

  const refreshProjects = useCallback(async () => {
    try {
      const data = await api.listProjects()
      setProjects(data.projects)
      setStatus('')
    } catch (e) {
      const msg = String(e)
      if (msg.includes('401')) {
        setUser(null)
        return
      }
      setStatus(`加载项目列表失败: ${e}`)
    }
  }, [])

  useEffect(() => {
    if (!user) return
    refreshProjects()
  }, [user, refreshProjects])

  const loadProject = useCallback(async (id: string) => {
    try {
      const p = await api.getProject(id)
      const polish = {
        ...defaultPolish(),
        ...(p.polish || {}),
        captions: p.polish?.captions?.length
          ? p.polish.captions
          : captionsFromTranscript(p.transcript || []),
      }
      setProjectId(id)
      setProject({ ...p, polish })
      setCurrentTime(0)
      setSelectedIndex(null)
      setAnalyzeStatus(null)
      setExportStatus(null)
      setStatus('项目加载成功')
    } catch (e) {
      setStatus(`加载项目失败: ${e}`)
    }
  }, [])

  const handleUpload = async (file: File | File[]) => {
    try {
      const files = Array.isArray(file) ? file : [file]
      const mb = files.reduce((s, f) => s + f.size, 0) / (1024 * 1024)
      setStatus(`上传中 0%（约 ${mb.toFixed(0)} MB）`)
      const res = await api.uploadVideos(files, (pct, label) => {
        setStatus(`上传中 ${pct}%  ${label}`)
      })
      await refreshProjects()
      await loadProject(res.id)
      setStatus(files.length > 1 ? `已拼接 ${files.length} 段素材` : '上传成功')
    } catch (e) {
      setStatus(`上传失败: ${e}`)
    }
  }

  const updateSegments = (segments: Segment[]) => {
    if (!project) return
    const next = { ...project, segments }
    setProject(next)
  }

  const updateTranscript = (transcript: TranscriptSentence[]) => {
    if (!project) return
    const prev = project.polish?.captions || []
    const captions = captionsFromTranscript(transcript).map((c) => {
      const old = prev.find((p) => Math.abs(p.start - c.start) < 0.08)
      return old ? { ...c, text: old.text, on: old.on } : c
    })
    const polish = { ...(project.polish || defaultPolish()), captions }
    setProject({ ...project, transcript, polish })
  }

  const updatePolish = (polish: Polish) => {
    if (!project) return
    setProject({ ...project, polish })
  }

  const handleToggleKeep = (index: number) => {
    if (!project) return
    const next = [...project.segments]
    next[index] = { ...next[index], keep: !next[index].keep }
    updateSegments(next)
  }


  const handleResize = (index: number, start: number, end: number) => {
    if (!project) return
    const next = [...project.segments]
    next[index] = { ...next[index], start_time: start, end_time: end }
    updateSegments(next)
  }

  const handleSave = async () => {
    if (!projectId || !project) return
    try {
      await api.saveProject(projectId, project)
      setStatus('项目已保存')
    } catch (e) {
      setStatus(`保存失败: ${e}`)
    }
  }

  const pollStatus = (
    taskType: 'analyze' | 'export',
    setter: React.Dispatch<React.SetStateAction<TaskStatus | null>>,
    fetcher: (id: string) => Promise<TaskStatus>,
  ) => {
    if (!projectId) return
    if (intervalRef.current) clearInterval(intervalRef.current)
    intervalRef.current = setInterval(async () => {
      const s = await fetcher(projectId)
      setter(s)
      if (s.status === 'done' || s.status === 'error') {
        if (intervalRef.current) clearInterval(intervalRef.current)
        if (s.status === 'done') {
          if (taskType === 'analyze') {
            const p = await api.getProject(projectId)
            const polish = {
              ...defaultPolish(),
              ...(p.polish || {}),
              captions: (p.polish?.captions?.length ? p.polish.captions : captionsFromTranscript(p.transcript || [])),
            }
            setProject({ ...p, polish })
            setRightTab('polish')
            setStatus(`AI 粗剪完成。请改字幕、调美颜，再点「按当前稿导出」。`)
          } else {
            setStatus('视频导出完成')
          }
        } else {
          setStatus(`${taskType === 'analyze' ? 'AI 分析' : '导出'}失败: ${s.error}`)
        }
      }
    }, 1000)
  }

  const handleAnalyze = async () => {
    if (!projectId) return
    try {
      await api.startV6Analyze(projectId, {
        user_prompt: userPrompt,
        platform,
        target_duration: targetDuration > 0 ? targetDuration : undefined,
      })
      setAnalyzeStatus({ task: 'analyze', status: 'pending', progress: 0 })
      pollStatus('analyze', setAnalyzeStatus, api.getV6AnalyzeStatus)
      setStatus('v6 AI 分析已启动')
    } catch (e) {
      setStatus(`启动分析失败: ${e}`)
    }
  }

  const handleExport = async () => {
    if (!projectId || !project) return
    try {
      await api.saveProject(projectId, project)
      await api.startV6Export(projectId, {
        with_subtitle: withSubtitle || (project.polish?.caption_style !== 'none'),
        segments: project.segments,
        polish: project.polish,
      })
      setExportStatus({ task: 'export', status: 'pending', progress: 0 })
      pollStatus('export', setExportStatus, api.getV6ExportStatus)
      setStatus('v6 视频导出已启动')
    } catch (e) {
      setStatus(`启动导出失败: ${e}`)
    }
  }

  const handleDownload = () => {
    if (!projectId) return
    window.open(api.downloadUrl(projectId), '_blank')
  }

  const handleSeek = (t: number) => {
    setCurrentTime(t)
  }

  const handleSelectSegment = (index: number) => {
    setSelectedIndex(index)
    if (project) {
      const seg = project.segments[index]
      setCurrentTime((seg.start_time + seg.end_time) / 2)
    }
  }

  const handleDeleteProject = async (id: string) => {
    if (!confirm('确定删除该项目吗？')) return
    try {
      await api.deleteProject(id)
      if (projectId === id) {
        setProjectId(null)
        setProject(null)
      }
      await refreshProjects()
    } catch (e) {
      setStatus(`删除失败: ${e}`)
    }
  }

  if (!authReady) {
    return <div className="login-screen"><p className="login-sub">虾片</p></div>
  }
  if (!user) {
    return <Login onLoggedIn={(u) => setUser(u)} />
  }

  return (
    <div className="app">
      <header className="header">
        <h1>虾片 <span className="tagline">文字即时间线</span></h1>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            value={projectId ?? ''}
            onChange={(e) => {
              const id = e.target.value
              if (id) loadProject(id)
            }}
          >
            <option value="">选择项目...</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.id} — {Math.round(p.duration)}s
              </option>
            ))}
          </select>
          <button className="secondary" onClick={() => setShowGuide(true)}>使用说明</button>
          <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{user?.name}</span>
          <button
            className="secondary"
            onClick={async () => {
              await api.logout()
              setUser(null)
              setProject(null)
              setProjectId(null)
            }}
          >
            退出
          </button>
          {projectId && (
            <button onClick={() => handleDeleteProject(projectId)} style={{ background: 'var(--danger)' }}>
              删除
            </button>
          )}
        </div>
      </header>

      {!project ? (
        <UploadZone onFile={handleUpload} />
      ) : (
        <>
          <div className="main">
            <div className="left-pane">
              <VideoPlayer
                projectId={projectId!}
                project={project}
                currentTime={currentTime}
                onTimeUpdate={setCurrentTime}
              />
            </div>
            <div className="right-pane">
              <AISettingsPanel
                userPrompt={userPrompt}
                onUserPromptChange={setUserPrompt}
                platform={platform}
                onPlatformChange={setPlatform}
                withSubtitle={withSubtitle}
                onWithSubtitleChange={setWithSubtitle}
                targetDuration={targetDuration}
                onTargetDurationChange={setTargetDuration}
              />
              <div className="right-tabs">
                <button type="button" className={rightTab === 'script' ? 'on' : ''} onClick={() => setRightTab('script')}>口播稿</button>
                <button type="button" className={rightTab === 'polish' ? 'on' : ''} onClick={() => setRightTab('polish')}>精修 · 字幕画面</button>
              </div>
              {rightTab === 'script' ? (
              <TranscriptEditor
                projectId={projectId!}
                transcript={project.transcript}
                segments={project.segments}
                duration={project.duration}
                selectedIndex={selectedIndex}
                targetDuration={targetDuration}
                onTranscriptChange={updateTranscript}
                onSegmentsChange={updateSegments}
                onSelectSegment={handleSelectSegment}
                onSeek={handleSeek}
              />
              ) : (
              <PolishPanel
                polish={project.polish || defaultPolish()}
                transcript={project.transcript || []}
                currentTime={currentTime}
                onChange={updatePolish}
                onSeek={handleSeek}
              />
              )}
            </div>
          </div>
          <div className="bottom-pane">
            <Timeline
              duration={project.duration}
              segments={project.segments}
              currentTime={currentTime}
              selectedIndex={selectedIndex}
              onSeek={handleSeek}
              onToggleKeep={handleToggleKeep}
              onResize={handleResize}
            />
            <Toolbar
              segmentsCount={project.segments.length}
              keptCount={project.segments.filter((s) => s.keep).length}
              analyzeStatus={analyzeStatus}
              exportStatus={exportStatus}
              onAnalyze={handleAnalyze}
              onExport={handleExport}
              onSave={handleSave}
              onDownload={handleDownload}
            />
          </div>
        </>
      )}

      {showGuide && <Guide onClose={() => setShowGuide(false)} />}
      {status && (
        <div
          style={{
            position: 'fixed',
            bottom: 12,
            left: '50%',
            transform: 'translateX(-50%)',
            background: 'var(--panel)',
            border: '1px solid var(--border)',
            padding: '8px 16px',
            borderRadius: 6,
            fontSize: 13,
            color: 'var(--text-muted)',
          }}
        >
          {status}
        </div>
      )}
    </div>
  )
}
