export interface Segment {
  start_time: number
  end_time: number
  keep: boolean
  label: string
  confidence: number
}

export interface TranscriptSentence {
  text: string
  start: number
  end: number
  keep?: boolean
  reason?: string
}

export interface CaptionLine {
  start: number
  end: number
  text: string
  on: boolean
}

export interface Polish {
  captions: CaptionLine[]
  caption_style: 'douyin' | 'white' | 'none'
  beauty: number
  look: 'none' | 'warm' | 'cool' | 'bright'
  transition: 'cut' | 'fade'
}

export interface Project {
  video_path: string
  segments: Segment[]
  transcript: TranscriptSentence[]
  fps: number
  width: number
  height: number
  duration: number
  original_filename?: string
  polish?: Polish
}

export interface ProjectSummary {
  id: string
  video_path: string
  duration: number
  fps: number
  width: number
  height: number
  segments_count: number
}

export interface TaskStatus {
  task: 'analyze' | 'export'
  status: 'idle' | 'pending' | 'running' | 'done' | 'error'
  progress: number
  result?: unknown
  error?: string
}
