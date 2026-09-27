interface GuideProps {
  onClose: () => void
}

export default function Guide({ onClose }: GuideProps) {
  return (
    <div className="guide-mask" onClick={onClose}>
      <div className="guide-sheet" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>五步出片</h2>
          <button className="secondary" onClick={onClose}>关闭</button>
        </header>
        <ol>
          <li>
            <strong>上传毛片。</strong>
            一段或多段都可以，多段按你选文件的顺序拼成一条时间线。
          </li>
          <li>
            <strong>告诉 AI 意图。</strong>
            右侧选平台（抖音/小红书等），可选填目标秒数。意图例如：「去掉倒计时和口头禅，按第一第二第三做成片」。点 <em>AI 分析</em>，等一两分钟。
          </li>
          <li>
            <strong>精修。</strong>
            「口播稿」里勾选句子；切到「精修 · 字幕画面」改字幕错字、关某句字幕、拉美颜、选滤镜和转场。
            预览里能直接看到。AI 不会一次剪到 100%。
          </li>
          <li>
            <strong>按当前稿导出。</strong>
            不用重新分析。有「第一、第二」会自动出黑底黄字要点卡。
          </li>
          <li>
            <strong>下载成片。</strong>
            不满意就回到第 3 步再改，再导出。
          </li>
        </ol>
        <p className="guide-note">
          不要把账号发到群里。成片会走云端语音识别和画面理解，请只用自己的口播素材。
        </p>
      </div>
    </div>
  )
}
