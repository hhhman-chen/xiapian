import type { CaptionLine, Polish, TranscriptSentence } from '../types'

interface PolishPanelProps {
  polish: Polish
  transcript: TranscriptSentence[]
  currentTime: number
  onChange: (p: Polish) => void
  onSeek: (t: number) => void
}

export const defaultPolish = (): Polish => ({
  captions: [],
  caption_style: 'douyin',
  beauty: 0,
  look: 'none',
  transition: 'cut',
})

export function captionsFromTranscript(transcript: TranscriptSentence[]): CaptionLine[] {
  return transcript
    .filter((s) => s.keep !== false && (s.text || '').trim())
    .map((s) => ({ start: s.start, end: s.end, text: s.text, on: true }))
}

export default function PolishPanel({ polish, currentTime, onChange, onSeek }: PolishPanelProps) {
  const set = (patch: Partial<Polish>) => onChange({ ...polish, ...patch })

  const updateCap = (i: number, patch: Partial<CaptionLine>) => {
    const next = polish.captions.map((c, j) => (j === i ? { ...c, ...patch } : c))
    set({ captions: next })
  }

  return (
    <div className="polish">
      <section>
        <div className="polish-h">字幕 · 点文字可改，关掉就不烧</div>
        <div className="polish-row">
          <span>样式</span>
          <select
            value={polish.caption_style}
            onChange={(e) => set({ caption_style: e.target.value as Polish['caption_style'] })}
          >
            <option value="douyin">抖音黄字描边</option>
            <option value="white">白字黑边</option>
            <option value="none">不烧字幕</option>
          </select>
        </div>
        <div className="caption-list">
          {polish.captions.length === 0 && <p className="hint">先跑 AI 分析，字幕会从口播稿生成，再在这里改错字。</p>}
          {polish.captions.map((c, i) => (
            <div key={`${c.start}-${i}`} className={`caption-row ${c.on ? '' : 'off'} ${currentTime >= c.start && currentTime < c.end ? 'now' : ''}`}>
              <input type="checkbox" checked={c.on} onChange={(e) => updateCap(i, { on: e.target.checked })} />
              <button className="ghost" type="button" onClick={() => onSeek(c.start)}>
                {fmt(c.start)}
              </button>
              <input
                className="caption-edit"
                value={c.text}
                onChange={(e) => updateCap(i, { text: e.target.value })}
              />
            </div>
          ))}
        </div>
      </section>

      <section>
        <div className="polish-h">画面 · 美颜 / 滤镜（预览即时，导出时烧进去）</div>
        <div className="polish-row">
          <span>美颜 {polish.beauty}</span>
          <input
            type="range"
            min={0}
            max={80}
            value={polish.beauty}
            onChange={(e) => set({ beauty: Number(e.target.value) })}
          />
        </div>
        <p className="hint">0 关闭。口播自拍可拉到 30–50，磨皮提亮，不是整容级。</p>
        <div className="polish-row">
          <span>滤镜</span>
          <select value={polish.look} onChange={(e) => set({ look: e.target.value as Polish['look'] })}>
            <option value="none">原片</option>
            <option value="warm">暖调</option>
            <option value="cool">冷调</option>
            <option value="bright">通透提亮</option>
          </select>
        </div>
      </section>

      <section>
        <div className="polish-h">特效 · 片段衔接</div>
        <div className="polish-row">
          <span>转场</span>
          <select
            value={polish.transition}
            onChange={(e) => set({ transition: e.target.value as Polish['transition'] })}
          >
            <option value="cut">硬切（口播常用）</option>
            <option value="fade">淡入淡出</option>
          </select>
        </div>
      </section>
    </div>
  )
}

function fmt(s: number) {
  const m = Math.floor(s / 60)
  const x = Math.floor(s % 60)
  return `${m}:${x.toString().padStart(2, '0')}`
}
