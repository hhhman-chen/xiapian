import type { Project, ProjectSummary, Segment, TaskStatus, TranscriptSentence } from './types'

const API_BASE = '/api'

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, { credentials: 'include', ...options })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

export async function login(username: string, password: string): Promise<{ username: string; name: string; role: string }> {
  return fetchJson(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
}

export async function logout(): Promise<void> {
  await fetchJson(`${API_BASE}/auth/logout`, { method: 'POST' })
}

export async function me(): Promise<{ username: string; name: string; role: string }> {
  return fetchJson(`${API_BASE}/auth/me`)
}

export async function listProjects(): Promise<{ projects: ProjectSummary[] }> {
  return fetchJson(`${API_BASE}/projects/`)
}

export async function uploadVideo(file: File): Promise<{ id: string; message: string }> {
  return uploadVideos([file])
}

const CHUNK_BYTES = 8 * 1024 * 1024 // 8MB，躲开 Cloudflare 100MB 上限

export async function uploadVideos(
  files: File[],
  onProgress?: (pct: number, label: string) => void,
): Promise<{ id: string; message: string }> {
  const ids: string[] = []
  let doneBytes = 0
  const totalBytes = files.reduce((s, f) => s + f.size, 0) || 1

  for (const file of files) {
    const chunks = Math.max(1, Math.ceil(file.size / CHUNK_BYTES))
    const init = await fetchJson<{ upload_id: string }>(`${API_BASE}/projects/upload/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, size: file.size, chunks }),
    })
    for (let i = 0; i < chunks; i++) {
      const blob = file.slice(i * CHUNK_BYTES, Math.min(file.size, (i + 1) * CHUNK_BYTES))
      const form = new FormData()
      form.append('upload_id', init.upload_id)
      form.append('index', String(i))
      form.append('chunk', blob, `part-${i}`)
      await fetchJson(`${API_BASE}/projects/upload/chunk`, { method: 'POST', body: form })
      doneBytes += blob.size
      const pct = Math.min(99, Math.round((doneBytes / totalBytes) * 100))
      onProgress?.(pct, `${file.name} ${i + 1}/${chunks}`)
    }
    ids.push(init.upload_id)
  }

  onProgress?.(99, '正在创建项目…')
  return fetchJson(`${API_BASE}/projects/upload/finish`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ upload_ids: ids, name: files[0]?.name || 'clips' }),
  })
}

export async function getProject(id: string): Promise<Project> {
  return fetchJson(`${API_BASE}/projects/${id}`)
}

export async function saveProject(id: string, project: Project): Promise<{ id: string; message: string }> {
  return fetchJson(`${API_BASE}/projects/${id}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(project),
  })
}

export async function deleteProject(id: string): Promise<{ id: string; message: string }> {
  return fetchJson(`${API_BASE}/projects/${id}`, { method: 'DELETE' })
}

export function videoUrl(id: string): string {
  return `${API_BASE}/projects/${id}/video`
}

export function frameUrl(id: string, t: number, width?: number): string {
  const params = new URLSearchParams({ t: t.toString() })
  if (width) params.append('width', width.toString())
  return `${API_BASE}/projects/${id}/frame?${params.toString()}`
}

export async function replaceSegments(id: string, segments: Segment[]): Promise<{ message: string; segments_count: number }> {
  return fetchJson(`${API_BASE}/projects/${id}/segments/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(segments),
  })
}

// ─── v6 K3 多模态接口 ──────────────────────────────────

export interface Platform {
  id: string
  label: string
}

export interface AnalyzeOptions {
  user_prompt?: string
  platform?: string
  target_duration?: number
}

export async function listPlatforms(): Promise<Platform[]> {
  return fetchJson(`${API_BASE}/platforms/`)
}

export async function startV6Analyze(id: string, options: AnalyzeOptions = {}): Promise<{ message: string; project_id: string }> {
  return fetchJson(`${API_BASE}/projects/${id}/v6/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_prompt: options.user_prompt || '',
      platform: options.platform || 'general',
      target_duration: options.target_duration ?? 60,
    }),
  })
}

export async function getV6AnalyzeStatus(id: string): Promise<TaskStatus> {
  return fetchJson(`${API_BASE}/projects/${id}/v6/analyze/status`)
}

export interface ExportOptions {
  with_subtitle?: boolean
  segments?: Segment[]
  polish?: unknown
}

export async function startV6Export(id: string, options: ExportOptions = {}): Promise<{ message: string; project_id: string }> {
  const body: Record<string, unknown> = {
    with_subtitle: options.with_subtitle ?? false,
  }
  if (options.polish) body.polish = options.polish
  if (options.segments) {
    body.segments = options.segments.map(seg => ({
      time_range: [seg.start_time, seg.end_time],
      action: seg.keep ? 'retain' : 'delete',
      label: seg.label,
    }))
  }
  return fetchJson(`${API_BASE}/projects/${id}/v6/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function getV6ExportStatus(id: string): Promise<TaskStatus> {
  return fetchJson(`${API_BASE}/projects/${id}/v6/export/status`)
}

export async function previewTranscriptSegments(
  id: string,
  transcript: TranscriptSentence[],
): Promise<{ segments: { time_range: [number, number]; action: string; label: string }[]; retain_duration: number; retain_count: number }> {
  return fetchJson(`${API_BASE}/projects/${id}/v6/transcript/segments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript }),
  })
}

// ─── 旧 Ollama 接口（保留兼容）────────────────────────────

export async function startAnalyze(id: string): Promise<{ message: string; project_id: string }> {
  return fetchJson(`${API_BASE}/projects/${id}/analyze`, { method: 'POST' })
}

export async function getAnalyzeStatus(id: string): Promise<TaskStatus> {
  return fetchJson(`${API_BASE}/projects/${id}/analyze/status`)
}

export async function startExport(id: string): Promise<{ message: string; project_id: string }> {
  return fetchJson(`${API_BASE}/projects/${id}/export`, { method: 'POST' })
}

export async function getExportStatus(id: string): Promise<TaskStatus> {
  return fetchJson(`${API_BASE}/projects/${id}/export/status`)
}

export function downloadUrl(id: string): string {
  return `${API_BASE}/projects/${id}/download`
}
