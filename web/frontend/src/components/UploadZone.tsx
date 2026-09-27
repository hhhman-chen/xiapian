import React from 'react'

interface UploadZoneProps {
  onFile: (file: File | File[]) => void
}

export default function UploadZone({ onFile }: UploadZoneProps) {
  const inputRef = React.useRef<HTMLInputElement>(null)

  const handleClick = () => inputRef.current?.click()

  const take = (list: FileList | null) => {
    if (!list || !list.length) return
    const files = Array.from(list)
    onFile(files.length === 1 ? files[0] : files)
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    take(e.target.files)
    if (inputRef.current) inputRef.current.value = ''
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    take(e.dataTransfer.files)
  }

  return (
    <div
      className="upload-zone"
      onClick={handleClick}
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
    >
      <p className="upload-kicker">新成片</p>
      <p>拖入一段或多段毛片</p>
      <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
        多段按选择顺序拼接 · 分析后点稿微调 · 不必重跑 AI
      </p>
      <input
        ref={inputRef}
        type="file"
        accept="video/*"
        multiple
        style={{ display: 'none' }}
        onChange={handleChange}
      />
    </div>
  )
}
