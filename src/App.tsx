import { useEffect, useId, useRef, useState, type CSSProperties, type FormEvent, type PointerEvent, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import logo from '../gta6.png'

type Line = { start: number; end: number; text: string }
type Clip = {
  start: number
  end: number
  hook: string
  title: string
  why: string
  heat?: number
  instagram: number | null
  youtube: number | null
  tiktok: number | null
  lines: Line[]
  thumb?: string
}
type Tokens = { prompt: number; output: number; thinking: number; total: number }
type ReelFile = {
  file: string
  title: string
  quality: string
  start?: number
  path?: string
  folder?: string
  meta_path?: string
  thumb_path?: string
  poster?: string
  tags?: string
  instagram?: string
  youtube_title?: string
  youtube?: string
  tiktok?: string
  thumb?: string
}
type Source = { title?: string; channel?: string; description?: string; tags?: string[]; categories?: string[] }
type Job = {
  id: string
  status: 'running' | 'done' | 'error'
  progress: number
  step: string
  detail: string
  tokens: Tokens
  clips: Clip[]
  title: string
  thumbnail: string
  error: string
  file: string
  files: ReelFile[]
  quality: string
  source?: Source
  words_read: number
  words_sent: number
  cost?: number
  spend?: number
}
type Model = { id: string; label: string }
type Choice = { id: string; label: string }

const PROVIDERS: Choice[] = [
  { id: 'google', label: 'Google AI Studio' },
  { id: 'openai', label: 'ChatGPT' },
]

const NOTES: Record<string, string> = {
  google: 'From aistudio.google.com. The key stays in this browser tab and is sent only to AnuClipz on this computer.',
  openai: 'From platform.openai.com. The key stays in this browser tab and is sent only to AnuClipz on this computer.',
}

const emptyTokens: Tokens = { prompt: 0, output: 0, thinking: 0, total: 0 }

function money(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '$0.00'
  if (value < 0.01) return `$${value.toFixed(4)}`
  return `$${value.toFixed(2)}`
}
const RISK = /\b(?:pornographic|pornography|motherfucker|onlyfans|fucking|fucked|fucker|rapist|raping|nigga|nigger|faggot|porno|p0rn|pr0n|sexual|naked|nudity|nudes|boobs|titty|pussy|whore|retard|porn|sexy|nsfw|nude|boob|dick|cock|slut|rape|fuck|fag|sex|xxx)\b/gi

function storedKey(name: string) {
  return localStorage.getItem('clipper-key-' + name) || sessionStorage.getItem('clipper-key-' + name) || ''
}

function keyIsSaved(name: string, key: string) {
  const trimmed = key.trim()
  return trimmed.length >= 10 && localStorage.getItem('clipper-key-ok-' + name) === trimmed
}

function rememberKey(name: string, key: string, ok: boolean) {
  const trimmed = key.trim()
  sessionStorage.setItem('clipper-key-' + name, trimmed)
  if (!ok) {
    if (localStorage.getItem('clipper-key-ok-' + name) === trimmed) localStorage.removeItem('clipper-key-ok-' + name)
    return
  }
  localStorage.setItem('clipper-key-' + name, trimmed)
  localStorage.setItem('clipper-key-ok-' + name, trimmed)
}

function maskText(text: string) {
  return text.replace(RISK, (word) => (word.length < 2 ? '*' : word[0] + '*' + word.slice(2)))
}
const rise = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: [0.22, 1, 0.36, 1] as const } },
}

function clock(value: number) {
  const total = Math.max(0, Math.floor(value))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  const body = `${minutes}:${String(seconds).padStart(2, '0')}`
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}` : body
}

function youtubeId(value: string) {
  const text = value.trim()
  const short = text.match(/youtu\.be\/([\w-]{6,})/)
  if (short) return short[1]
  const watch = text.match(/[?&]v=([\w-]{6,})/)
  return watch ? watch[1] : ''
}

function momentKey(clip: { start: number; end: number }) {
  return `${clip.start}-${clip.end}`
}

type LayoutName = 'blur' | 'full' | 'split'
type Region = { x: number; y: number; w: number; h: number }

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

// Each half of the 9:16 reel is 9:8. On the 16:9 source frame, that is this width/height.
const SPLIT_RATIO = (9 / 8) / (16 / 9)

function round4(value: number) {
  return Math.round(value * 10000) / 10000
}

function lockSplit(box: Region): Region {
  const cx = box.x + box.w / 2
  const cy = box.y + box.h / 2
  let height = clamp(box.h, 0.28, 1)
  let width = height * SPLIT_RATIO
  if (width > 1) {
    width = 1
    height = width / SPLIT_RATIO
  }
  const x = clamp(cx - width / 2, 0, 1 - width)
  const y = clamp(cy - height / 2, 0, 1 - height)
  return { x: round4(x), y: round4(y), w: round4(width), h: round4(height) }
}

function resizeSplit(origin: Region, dx: number, dy: number): Region {
  const fromWidth = Math.abs(dx) >= Math.abs(dy)
  let width = fromWidth ? origin.w + dx : (origin.h + dy) * SPLIT_RATIO
  const maxW = Math.max(0.12, 1 - origin.x)
  const maxH = Math.max(0.12, 1 - origin.y)
  const fitW = Math.min(maxW, maxH * SPLIT_RATIO)
  width = clamp(width, Math.min(0.22, fitW), fitW)
  return { x: origin.x, y: origin.y, w: round4(width), h: round4(width / SPLIT_RATIO) }
}

function cropStyle(image: string, box: Region): CSSProperties {
  const w = clamp(box.w, 0.12, 1)
  const h = clamp(box.h, 0.12, 1)
  const x = clamp(box.x, 0, 1 - w)
  const y = clamp(box.y, 0, 1 - h)
  const posX = w >= 0.999 ? '0%' : `${(x / (1 - w)) * 100}%`
  const posY = h >= 0.999 ? '0%' : `${(y / (1 - h)) * 100}%`
  const safe = image.replace(/"/g, '')
  return {
    backgroundImage: safe ? `url("${safe}")` : undefined,
    backgroundSize: `${100 / w}% ${100 / h}%`,
    backgroundPosition: `${posX} ${posY}`,
    backgroundRepeat: 'no-repeat',
  }
}

function RegionFrame({ image, regions, onChange }: { image: string; regions: [Region, Region]; onChange: (next: [Region, Region]) => void }) {
  const frame = useRef<HTMLDivElement>(null)
  function drag(index: 0 | 1, event: PointerEvent<HTMLElement>, mode: 'move' | 'resize') {
    const host = frame.current
    if (!host) return
    event.preventDefault()
    event.stopPropagation()
    const bounds = host.getBoundingClientRect()
    const origin = regions[index]
    const startX = event.clientX
    const startY = event.clientY
    const move = (ev: globalThis.PointerEvent) => {
      const dx = (ev.clientX - startX) / bounds.width
      const dy = (ev.clientY - startY) / bounds.height
      const next = regions.map((box, item) => {
        if (item !== index) return box
        if (mode === 'resize') return resizeSplit(origin, dx, dy)
        return { ...origin, x: clamp(origin.x + dx, 0, 1 - origin.w), y: clamp(origin.y + dy, 0, 1 - origin.h) }
      }) as [Region, Region]
      onChange(next)
    }
    const stop = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
  }
  return (
    <div className="region-frame" ref={frame}>
      {image ? <img src={image} alt="" /> : <div className="region-empty" />}
      {regions.map((box, index) => (
        <div
          key={index}
          className={index === 0 ? 'region top' : 'region bottom'}
          style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }}
          onPointerDown={(event) => drag(index as 0 | 1, event, 'move')}
        >
          <span>{index === 0 ? 'Top' : 'Bottom'}</span>
          <button type="button" aria-label={index === 0 ? 'Resize the top section' : 'Resize the bottom section'} onPointerDown={(event) => drag(index as 0 | 1, event, 'resize')} />
        </div>
      ))}
    </div>
  )
}

function scoreOf(clip: Clip) {
  return clip.heat ?? clip.youtube ?? clip.tiktok ?? clip.instagram ?? 0
}

function passingIndexes(clips: Clip[], score: number) {
  return clips.flatMap((clip, index) => scoreOf(clip) >= score ? [index] : [])
}

type PlayMark = { start: number; end: number; nonce: number; hook: string; caption: string; showTitle: boolean; showCaptions: boolean }

type Paintable = CanvasImageSource & { videoWidth?: number; naturalWidth?: number; videoHeight?: number; naturalHeight?: number }

function sourceSize(media: Paintable) {
  return {
    width: media.videoWidth || media.naturalWidth || 0,
    height: media.videoHeight || media.naturalHeight || 0,
  }
}

function paintLayout(ctx: CanvasRenderingContext2D, media: Paintable, layout: LayoutName, regions: [Region, Region], width: number, height: number) {
  const { width: sw, height: sh } = sourceSize(media)
  if (!sw || !sh) return
  ctx.clearRect(0, 0, width, height)
  const cover = (box: Region | null, dx: number, dy: number, dw: number, dh: number) => {
    const sx = (box ? box.x : 0) * sw
    const sy = (box ? box.y : 0) * sh
    const cw = Math.max(1, (box ? box.w : 1) * sw)
    const ch = Math.max(1, (box ? box.h : 1) * sh)
    const scale = Math.max(dw / cw, dh / ch)
    const visW = dw / scale
    const visH = dh / scale
    ctx.drawImage(media, sx + (cw - visW) / 2, sy + (ch - visH) / 2, visW, visH, dx, dy, dw, dh)
  }
  if (layout === 'full') {
    cover(null, 0, 0, width, height)
    return
  }
  if (layout === 'split') {
    cover(regions[0], 0, 0, width, height / 2)
    cover(regions[1], 0, height / 2, width, height / 2)
    return
  }
  ctx.save()
  ctx.filter = 'blur(18px)'
  cover(null, width * -0.08, height * -0.08, width * 1.16, height * 1.16)
  ctx.restore()
  const band = width * (sh / sw)
  ctx.drawImage(media, 0, (height - band) / 2, width, band)
}

function useLiveFrame(pageUrl: string, on: boolean, fallbackEnd: number) {
  const [shown, setShown] = useState(0)
  const [asked, setAsked] = useState<number | null>(null)
  const [duration, setDuration] = useState(0)
  const [image, setImage] = useState('')
  const [note, setNote] = useState('')
  const touched = useRef(false)
  const ticket = useRef(0)

  useEffect(() => {
    touched.current = false
    setAsked(null)
    setShown(0)
    setDuration(0)
    setImage('')
    setNote('')
  }, [pageUrl])

  useEffect(() => {
    if (!touched.current) return
    const timer = window.setTimeout(() => setAsked(shown), 350)
    return () => window.clearTimeout(timer)
  }, [shown])

  useEffect(() => {
    if (!on || !pageUrl) return
    const id = ++ticket.current
    const controller = new AbortController()
    setNote('Opening a frame from the video.')
    const params = new URLSearchParams({ url: pageUrl })
    if (asked !== null) params.set('at', String(Math.round(asked)))
    const finish = (next: string, point: number, length: number) => {
      if (id !== ticket.current) return
      setImage((prev) => {
        if (prev.startsWith('blob:')) URL.revokeObjectURL(prev)
        return next
      })
      if (length > 0) setDuration(length)
      if (!touched.current) setShown(point)
      setNote('')
    }
    const snapshot = (start: number, length: number) => {
      const video = document.createElement('video')
      video.muted = true
      video.playsInline = true
      video.preload = 'auto'
      video.src = `/api/preview?url=${encodeURIComponent(pageUrl)}&start=${start}&end=${start + 1.5}`
      const drop = () => {
        video.onloadeddata = null
        video.onerror = null
        video.src = ''
      }
      video.onerror = () => {
        drop()
        if (id === ticket.current) setNote('This frame could not be opened.')
      }
      video.onloadeddata = () => {
        const canvas = document.createElement('canvas')
        canvas.width = video.videoWidth || 640
        canvas.height = video.videoHeight || 360
        canvas.getContext('2d')?.drawImage(video, 0, 0)
        drop()
        canvas.toBlob((blob) => {
          if (!blob || id !== ticket.current) return
          finish(URL.createObjectURL(blob), start, length)
        }, 'image/jpeg', 0.82)
      }
      return drop
    }
    let dropSnapshot = () => {}
    let cancelled = false
    void fetch(`/api/frame?${params}`, { signal: controller.signal })
      .then(async (res) => {
        if (cancelled || id !== ticket.current) return
        if (!res.ok) throw new Error('frame')
        const length = Number(res.headers.get('X-Duration') || 0)
        const point = Number(res.headers.get('X-At') || asked || 0)
        const blob = await res.blob()
        if (cancelled || id !== ticket.current) return
        finish(URL.createObjectURL(blob), point, length)
      })
      .catch(() => {
        if (cancelled || id !== ticket.current || controller.signal.aborted) return
        const length = fallbackEnd > 1 ? fallbackEnd : 0
        const start = asked ?? (length > 1 ? length / 2 : 0)
        if (length < 1 && asked === null) {
          setNote('Find the moments, then move through the video to place the two sections.')
          return
        }
        dropSnapshot = snapshot(Math.max(0, start), length || fallbackEnd)
      })
    return () => {
      cancelled = true
      controller.abort()
      dropSnapshot()
    }
  }, [on, pageUrl, asked, fallbackEnd])

  return {
    image,
    shown,
    duration: duration || fallbackEnd,
    note,
    move(value: number) {
      touched.current = true
      setShown(value)
    },
  }
}

function SourcePlayer({
  pageUrl, play, layout, regions, still,
}: {
  pageUrl: string
  play: PlayMark | null
  layout: LayoutName
  regions: [Region, Region]
  still: string
}) {
  const node = useRef<HTMLVideoElement>(null)
  const board = useRef<HTMLCanvasElement>(null)
  const stillNode = useRef<HTMLImageElement | null>(null)
  const drawRef = useRef<() => void>(() => {})
  const [note, setNote] = useState('Play a moment to watch that part here.')
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const src = play ? `/api/preview?url=${encodeURIComponent(pageUrl)}&start=${play.start}&end=${play.end}` : ''
  useEffect(() => {
    if (!still) {
      stillNode.current = null
      drawRef.current()
      return
    }
    const img = new Image()
    img.onload = () => {
      stillNode.current = img
      drawRef.current()
    }
    img.src = still
  }, [still])
  useEffect(() => {
    const canvas = board.current
    const video = node.current
    if (!canvas) return
    let raf = 0
    let alive = true
    const draw = () => {
      if (!alive) return
      const ctx = canvas.getContext('2d')
      const rect = canvas.getBoundingClientRect()
      if (!ctx || rect.width < 2) return
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      const width = Math.round(rect.width * dpr)
      const height = Math.round(rect.height * dpr)
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width
        canvas.height = height
      }
      const media = video && video.readyState >= 2 ? video : stillNode.current
      if (media) paintLayout(ctx, media, layout, regions, width, height)
    }
    drawRef.current = draw
    const loop = () => {
      draw()
      if (video && !video.paused && !video.ended) raf = requestAnimationFrame(loop)
    }
    loop()
    const onPlay = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(loop)
    }
    video?.addEventListener('play', onPlay)
    video?.addEventListener('seeked', draw)
    video?.addEventListener('loadeddata', draw)
    return () => {
      alive = false
      cancelAnimationFrame(raf)
      video?.removeEventListener('play', onPlay)
      video?.removeEventListener('seeked', draw)
      video?.removeEventListener('loadeddata', draw)
    }
  }, [layout, regions, play, still])
  useEffect(() => {
    const video = node.current
    if (!video || !play) {
      setNote('Play a moment to watch that part here.')
      setPlaying(false)
      setTime(0)
      setDuration(0)
      return
    }
    const span = `${play.start}-${play.end}`
    if (video.dataset.span === span && video.readyState >= 2) {
      video.currentTime = 0
      setNote('')
      void video.play().catch(() => setNote('Press play on the video.'))
      return
    }
    video.dataset.span = span
    setNote('Opening that part of the video.')
    const onReady = () => {
      setNote('')
      void video.play().catch(() => setNote('Press play on the video.'))
    }
    const onError = () => setNote('This part could not be opened.')
    video.addEventListener('loadeddata', onReady)
    video.addEventListener('error', onError)
    return () => {
      video.removeEventListener('loadeddata', onReady)
      video.removeEventListener('error', onError)
    }
  }, [play])
  function seek(value: number) {
    const video = node.current
    if (!video || !duration) return
    const next = Math.min(duration, Math.max(0, value))
    video.currentTime = next
    setTime(next)
  }
  return (
    <div className="source-reel">
      <div className={`phone layout-${layout} source-live`}>
        <canvas ref={board} />
        {src && (
          <video
            key={src}
            ref={node}
            className="source-media"
            src={src}
            playsInline
            preload="auto"
            onTimeUpdate={() => setTime(node.current?.currentTime || 0)}
            onDurationChange={() => setDuration(Number.isFinite(node.current?.duration) ? node.current!.duration : 0)}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
          />
        )}
        {play?.showTitle && play.hook && <p className="hook">{maskText(play.hook)}</p>}
        {play?.showCaptions && play.caption && <p className="cap">{maskText(play.caption)}</p>}
        {note && <p className="source-note">{note}</p>}
      </div>
      <div className="source-bar">
        <input
          className="reel-seek"
          type="range"
          min={0}
          max={duration || 0}
          step={0.05}
          value={Math.min(time, duration || 0)}
          aria-label="Move through this moment"
          aria-valuetext={`${clock(time)} of ${clock(duration)}`}
          disabled={!duration}
          style={{ ['--fill' as string]: `${duration ? (time / duration) * 100 : 0}%` }}
          onInput={(event) => seek(Number(event.currentTarget.value))}
        />
        <div className="reel-controls">
          <div className="reel-meta">
            <button type="button" aria-label={playing ? 'Pause' : 'Play'} disabled={!src} onClick={() => {
              const video = node.current
              if (!video) return
              if (video.paused) void video.play().catch(() => setNote('Press play on the video.'))
              else video.pause()
            }}>
              <PlayerIcon name={playing ? 'pause' : 'play'} />
            </button>
            <span className="reel-time">{clock(time)} / {clock(duration)}</span>
          </div>
          <div className="reel-actions">
            <button type="button" aria-label={muted ? 'Unmute' : 'Mute'} disabled={!src} onClick={() => {
              const video = node.current
              if (!video) return
              video.muted = !video.muted
              setMuted(video.muted)
            }}>
              <PlayerIcon name={muted ? 'mute' : 'sound'} />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function MomentCard({
  item, image, on, captions, showTitle, layout, regions, onToggle, onCaptions, onTitle, onPlay,
}: {
  item: Clip
  image: string
  on: boolean
  captions: boolean
  showTitle: boolean
  layout: LayoutName
  regions: [Region, Region]
  onToggle: () => void
  onCaptions: () => void
  onTitle: () => void
  onPlay: () => void
}) {
  const [open, setOpen] = useState(false)
  const quote = maskText(item.lines.map((line) => line.text).join(' ') || item.why)
  const long = quote.length > 120
  const safe = image.replace(/"/g, '')
  return (
    <motion.article
      className={on ? 'moment on' : 'moment'}
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 8 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
    >
      <div className="moment-reel">
        <div className={`phone layout-${layout}`}>
          {layout === 'split' ? (
            <>
              <div className="split-band top" style={cropStyle(safe, regions[0])} />
              <div className="split-band bottom" style={cropStyle(safe, regions[1])} />
            </>
          ) : (
            <>
              <div className="wash" style={safe ? { backgroundImage: `url("${safe}")` } : undefined} />
              {safe ? <img className="sharp" src={safe} alt="" /> : <div className="sharp blank" />}
            </>
          )}
          {showTitle && <p className="hook">{maskText(item.hook)}</p>}
          {captions && item.lines[0]?.text && <p className="cap">{maskText(item.lines[0].text)}</p>}
          <button className="moment-play" type="button" aria-label={`Play ${maskText(item.title)}`} onClick={onPlay}>
            <PlayerIcon name="play" />
          </button>
        </div>
      </div>
      <div className="moment-copy">
        <div className="moment-title">
          <h3>{maskText(item.title)}</h3>
          <span className="moment-score">{scoreOf(item)}</span>
        </div>
        <p className="when">{clock(item.start)} → {clock(item.end)} · {Math.round(item.end - item.start)}s</p>
        <p className={open || !long ? 'quote' : 'quote clamp'}>{quote}</p>
        {long && (
          <button type="button" className="quote-more" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
            {open ? 'Less' : 'More'}
          </button>
        )}
        <div className="moment-flags">
          <label>
            <input type="checkbox" checked={on} onChange={onToggle} />
            Keep
          </label>
          <label>
            <input type="checkbox" checked={captions} onChange={onCaptions} />
            Show captions
          </label>
          <label>
            <input type="checkbox" checked={showTitle} onChange={onTitle} />
            Show title
          </label>
        </div>
      </div>
    </motion.article>
  )
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error || 'Request failed.')
  return data
}

function PlayerIcon({ name }: { name: 'play' | 'pause' | 'sound' | 'mute' | 'full' | 'exit' | 'close' | 'copy' | 'check' }) {
  if (name === 'play') return <svg className="play" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M9 5.8v12.4L18.2 12 9 5.8Z" /></svg>
  if (name === 'pause') return <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M7 5h3.2v14H7V5Zm6.8 0H17v14h-3.2V5Z" /></svg>
  if (name === 'close') return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
  if (name === 'copy') return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 8.5h9.5V18a1.5 1.5 0 0 1-1.5 1.5H8A1.5 1.5 0 0 1 6.5 18V10A1.5 1.5 0 0 1 8 8.5Z" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M9.5 8.5V7A1.5 1.5 0 0 1 11 5.5h6A1.5 1.5 0 0 1 18.5 7v8a1.5 1.5 0 0 1-1.5 1.5h-1.5" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
  if (name === 'check') return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12.5 10 16.5 18 8" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
  const d = name === 'sound'
    ? 'M4 10h3.2L12 6v12l-4.8-4H4v-4Zm9.2-1.6a4 4 0 0 1 0 7.2'
    : name === 'mute'
      ? 'M4 10h3.2L12 6v12l-4.8-4H4v-4Zm8.2 1.4 5.6 5.6M17.8 11.4l-5.6 5.6'
      : name === 'full'
        ? 'M5 9V5h4M15 5h4v4M19 15v4h-4M9 19H5v-4'
        : 'M9 5H5v4M15 9V5h4M19 15h-4v4M9 19v-4H5'
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function ReelStill({ src }: { src: string }) {
  const video = useRef<HTMLVideoElement>(null)
  return (
    <video
      ref={video}
      className="reel"
      src={src}
      muted
      playsInline
      preload="metadata"
      onLoadedData={() => {
        const node = video.current
        if (node && node.currentTime < 0.05) node.currentTime = 0.4
      }}
    />
  )
}

function ReelPlayer({ src, poster = '' }: { src: string; poster?: string }) {
  const video = useRef<HTMLVideoElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const hide = useRef(0)
  const linger = useRef(0)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [mark, setMark] = useState<'play' | 'pause' | null>(null)
  const [full, setFull] = useState(false)
  const [hot, setHot] = useState(false)

  useEffect(() => {
    const node = video.current
    if (!node) return
    node.muted = false
    const start = () => { void node.play().then(() => setPlaying(true)).catch(() => setPlaying(false)) }
    if (node.readyState >= 2) start()
    else node.addEventListener('canplay', start, { once: true })
    return () => {
      node.removeEventListener('canplay', start)
      window.clearTimeout(hide.current)
      window.clearTimeout(linger.current)
    }
  }, [src])

  useEffect(() => {
    function onChange() { setFull(document.fullscreenElement === stage.current) }
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  function flash(kind: 'play' | 'pause') {
    setMark(kind)
    window.clearTimeout(hide.current)
    hide.current = window.setTimeout(() => setMark(null), 700)
  }

  function toggle() {
    const node = video.current
    if (!node) return
    if (node.paused) {
      void node.play()
      setPlaying(true)
      flash('play')
    } else {
      node.pause()
      setPlaying(false)
      flash('pause')
    }
  }

  function toggleMute() {
    const node = video.current
    if (!node) return
    node.muted = !node.muted
    setMuted(node.muted)
  }

  function toggleFull() {
    const node = stage.current
    if (!node) return
    if (document.fullscreenElement) void document.exitFullscreen()
    else void node.requestFullscreen()
  }

  function holdChrome(event: PointerEvent) {
    if (event.pointerType === 'mouse') return
    setHot(true)
    window.clearTimeout(linger.current)
    linger.current = window.setTimeout(() => setHot(false), 2400)
  }

  function seek(value: number) {
    const node = video.current
    if (!node || !Number.isFinite(duration) || duration <= 0) return
    const next = Math.min(duration, Math.max(0, value))
    node.currentTime = next
    setTime(next)
  }

  return (
    <div
      className={hot ? 'reel-stage hot' : 'reel-stage'}
      ref={stage}
      onClick={toggle}
      onPointerEnter={(event) => { if (event.pointerType === 'mouse') setHot(true) }}
      onPointerLeave={(event) => { if (event.pointerType === 'mouse') setHot(false) }}
      onPointerDown={holdChrome}
    >
      <video
        ref={video}
        className="reel"
        src={src}
        poster={poster || undefined}
        playsInline
        preload="auto"
        onTimeUpdate={() => setTime(video.current?.currentTime || 0)}
        onDurationChange={() => setDuration(Number.isFinite(video.current?.duration) ? video.current!.duration : 0)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
      />
      <AnimatePresence>
        {mark && (
          <motion.span
            key={mark}
            className="reel-mark"
            initial={{ opacity: 0, scale: 0.7 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 1.08 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
          >
            <PlayerIcon name={mark} />
          </motion.span>
        )}
      </AnimatePresence>
      <div className="reel-bar" onClick={(event) => event.stopPropagation()}>
        <input
          className="reel-seek"
          type="range"
          min={0}
          max={duration || 0}
          step={0.05}
          value={Math.min(time, duration || 0)}
          aria-label="Move through the video"
          aria-valuetext={`${clock(time)} of ${clock(duration)}`}
          disabled={!duration}
          style={{ ['--fill' as string]: `${duration ? (time / duration) * 100 : 0}%` }}
          onInput={(event) => seek(Number(event.currentTarget.value))}
        />
        <div className="reel-controls">
        <div className="reel-meta">
          <button type="button" aria-label={playing ? 'Pause' : 'Play'} onClick={toggle}>
            <PlayerIcon name={playing ? 'pause' : 'play'} />
          </button>
          <span className="reel-time">{clock(time)} / {clock(duration)}</span>
        </div>
        <div className="reel-actions">
          <button type="button" aria-label={muted ? 'Unmute' : 'Mute'} onClick={toggleMute}>
            <PlayerIcon name={muted ? 'mute' : 'sound'} />
          </button>
          <button type="button" aria-label={full ? 'Exit full screen' : 'Full screen'} onClick={toggleFull}>
            <PlayerIcon name={full ? 'exit' : 'full'} />
          </button>
        </div>
        </div>
      </div>
    </div>
  )
}

function splitPath(text: string) {
  const slash = Math.max(text.lastIndexOf('/'), text.lastIndexOf('\\'))
  if (slash < 0) return { dir: '', name: text }
  return { dir: text.slice(0, slash + 1), name: text.slice(slash + 1) }
}

function CopyCard({ kind, label, text }: { kind: string; label: string; text: string }) {
  const [copied, setCopied] = useState(false)
  const file = kind === 'file' ? splitPath(text) : null
  return (
    <article className="panel copy-card" data-kind={kind}>
      <header>
        <span className="label">{label}</span>
        <button
          className="icon-btn"
          type="button"
          aria-label={copied ? `${label} copied` : `Copy ${label}`}
          onClick={() => {
            void navigator.clipboard.writeText(text).then(() => {
              setCopied(true)
              window.setTimeout(() => setCopied(false), 1200)
            })
          }}
        >
          <PlayerIcon name={copied ? 'check' : 'copy'} />
        </button>
      </header>
      {file ? (
        <p className="path"><span>{file.dir}</span>{file.name}</p>
      ) : (
        <p>{text}</p>
      )}
    </article>
  )
}

function Field({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <motion.div className={wide ? 'field span' : 'field'} variants={rise}>
      <span className="label">{label}</span>
      {children}
    </motion.div>
  )
}

function Aurora() {
  return (
    <div className="aurora" aria-hidden="true">
      <span className="orb a" />
      <span className="orb b" />
      <span className="orb c" />
    </div>
  )
}

function Menu({ label, value, options, empty, onChange }: { label: string; value: string; options: Choice[]; empty: string; onChange: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const listId = useId()
  const current = options.find((item) => item.id === value)

  useEffect(() => {
    if (!open) return
    function close(event: MouseEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="menu" ref={root}>
      <button className={open ? 'menu-btn open' : 'menu-btn'} type="button" aria-haspopup="listbox" aria-expanded={open} aria-controls={listId} onClick={() => setOpen((value) => !value)}>
        <span>{current?.label || empty}</span>
        <motion.span className="chevron" animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.25 }}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 6.2 8 10.5l4.5-4.3" /></svg>
        </motion.span>
      </button>
      <AnimatePresence>
        {open && (
          <motion.ul
            id={listId}
            className="menu-list"
            role="listbox"
            aria-label={label}
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
          >
            {options.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={item.id === value}
                  className={item.id === value ? 'on' : ''}
                  onClick={() => { onChange(item.id); setOpen(false) }}
                >
                  {item.label}
                </button>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </div>
  )
}

function Work({ job }: { job: Job }) {
  const bad = job.status === 'error'
  const live = job.status === 'running'
  return (
    <motion.section className="work" aria-live="polite" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }} transition={{ duration: 0.35 }}>
      <div className="track">
        <motion.div className="fill" initial={false} animate={{ width: `${Math.max(6, job.progress * 100)}%` }} transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}>
          {live && <span className="sheen" />}
        </motion.div>
      </div>
      <div className="work-top">
        <p className="step">
          {live && <motion.span className="pulse" animate={{ opacity: [0.35, 1, 0.35] }} transition={{ duration: 1.1, repeat: Infinity, ease: 'easeInOut' }} />}
          {job.step}
        </p>
        <p className="pct">{Math.round(job.progress * 100)}%</p>
      </div>
      <p className={bad ? 'detail bad' : 'detail'}>{job.detail}</p>
      <dl className="tokens">
        <div><dt>Prompt</dt><dd>{job.tokens?.prompt ?? 0}</dd></div>
        <div><dt>Reply</dt><dd>{job.tokens?.output ?? 0}</dd></div>
        <div><dt>Thinking</dt><dd>{job.tokens?.thinking ?? 0}</dd></div>
        <div><dt>Total</dt><dd>{job.tokens?.total ?? 0}</dd></div>
        <div><dt>About</dt><dd>{money(job.cost || 0)}</dd></div>
      </dl>
    </motion.section>
  )
}

export function App() {
  const [provider, setProvider] = useState('google')
  const [apiKey, setApiKey] = useState('')
  const [keySaved, setKeySaved] = useState(false)
  const [models, setModels] = useState<Model[]>([])
  const [model, setModel] = useState('')
  const [query, setQuery] = useState('')
  const [scoreCut, setScoreCut] = useState(80)
  const [momentFilter, setMomentFilter] = useState<'all' | 'kept'>('all')
  const [momentSort, setMomentSort] = useState<'score' | 'time'>('score')
  const [playMark, setPlayMark] = useState<PlayMark | null>(null)
  const [captionOn, setCaptionOn] = useState<Record<string, boolean>>({})
  const [titleOn, setTitleOn] = useState<Record<string, boolean>>({})
  const [layout, setLayout] = useState<LayoutName>('blur')
  const [regions, setRegions] = useState<[Region, Region]>([
    lockSplit({ x: 0.04, y: 0.04, w: 0.4, h: 0.62 }),
    lockSplit({ x: 0.56, y: 0.34, w: 0.4, h: 0.62 }),
  ])
  useEffect(() => {
    if (layout !== 'split') return
    setRegions((current) => {
      const next = current.map((box) => lockSplit(box)) as [Region, Region]
      const same = next.every((box, index) => box.x === current[index].x && box.y === current[index].y && box.w === current[index].w && box.h === current[index].h)
      return same ? current : next
    })
  }, [layout])
  const [url, setUrl] = useState('')
  const [note, setNote] = useState(NOTES.google)
  const [modelWarn, setModelWarn] = useState(false)
  const [modelNote, setModelNote] = useState('Paste the key. Models load on their own.')
  const keyWait = useRef(0)
  const [loadingModels, setLoadingModels] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const furthest = clips.reduce((end, clip) => Math.max(end, clip.end), 0)
  const frame = useLiveFrame(url.trim(), layout === 'split' && Boolean(youtubeId(url)), furthest)
  const [film, setFilm] = useState('')
  const [source, setSource] = useState<Source>({})
  const [thumb, setThumb] = useState('')
  const [chosen, setChosen] = useState<number[]>([])
  const [videos, setVideos] = useState<ReelFile[]>([])
  const [folder, setFolder] = useState('')
  const [picking, setPicking] = useState(false)
  const [exported, setExported] = useState<ReelFile[]>([])
  const [openExport, setOpenExport] = useState<ReelFile | null>(null)
  const [busy, setBusy] = useState(false)
  const [spend, setSpend] = useState(0)

  useEffect(() => {
    const saved = sessionStorage.getItem('clipper-provider')
    const name = saved === 'openai' ? 'openai' : 'google'
    const key = storedKey(name)
    setProvider(name)
    setApiKey(key)
    setKeySaved(keyIsSaved(name, key))
    setNote(NOTES[name])
    if (key.trim().length >= 10) void loadModels(key, name)
    const savedFolder = sessionStorage.getItem('anuclipz-export-dir')
    if (savedFolder) setFolder(savedFolder)
    else {
      void fetch('/api/folder').then(async (response) => {
        const data = await response.json()
        if (data.path) setFolder(data.path)
      }).catch(() => {})
    }
    sessionStorage.removeItem('anuclipz-exported')
    void fetch('/api/spend').then(async (response) => {
      const data = await response.json()
      if (typeof data.usd === 'number') setSpend(data.usd)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!folder) return
    let stop = false
    void fetch('/api/saved?dir=' + encodeURIComponent(folder)).then(async (response) => {
      if (!response.ok || stop) return
      const data = await response.json()
      if (stop) return
      if (Array.isArray(data.files)) setExported(data.files)
    }).catch(() => {})
    return () => { stop = true }
  }, [folder])

  async function watch(id: string, onDone: (next: Job) => void) {
    const timer = window.setInterval(async () => {
      const response = await fetch('/api/jobs/' + id)
      const next = await response.json() as Job
      setJob(next)
      if (typeof next.spend === 'number') setSpend(next.spend)
      if (next.status !== 'running') {
        window.clearInterval(timer)
        setBusy(false)
        onDone(next)
      }
    }, 400)
  }

  async function loadModels(key = apiKey, name = provider) {
    const trimmed = key.trim()
    sessionStorage.setItem('clipper-key-' + name, trimmed)
    if (trimmed.length < 10) {
      setModels([])
      setModel('')
      setModelNote('Paste the key. Models load on their own.')
      setModelWarn(false)
      setKeySaved(false)
      return
    }
    setLoadingModels(true)
    setModelNote('Asking which models this key can use.')
    setModelWarn(false)
    try {
      const data = await postJson<{ models: Model[]; recommended: string }>('/api/models', { provider: name, api_key: trimmed })
      setModels(data.models)
      const stored = sessionStorage.getItem('clipper-model-' + name)
      const pick = data.models.some((item) => item.id === stored) ? stored! : data.recommended
      setModel(pick)
      sessionStorage.setItem('clipper-model-' + name, pick)
      rememberKey(name, trimmed, true)
      setKeySaved(true)
      setModelNote(`${data.models.length} models. The suggested one is the light model, so the call stays small.`)
    } catch (err) {
      setModels([])
      rememberKey(name, trimmed, false)
      setKeySaved(false)
      setModelNote(err instanceof Error ? err.message : 'Could not load models.')
      setModelWarn(true)
    } finally {
      setLoadingModels(false)
    }
  }

  function chooseProvider(name: string) {
    setProvider(name)
    sessionStorage.setItem('clipper-provider', name)
    const key = storedKey(name)
    setApiKey(key)
    setKeySaved(keyIsSaved(name, key))
    setNote(NOTES[name] || '')
    setModels([])
    setModel('')
    if (key.trim().length >= 10) void loadModels(key, name)
  }

  function onKeyChange(value: string) {
    setApiKey(value)
    window.clearTimeout(keyWait.current)
    const trimmed = value.trim()
    sessionStorage.setItem('clipper-key-' + provider, trimmed)
    setKeySaved(keyIsSaved(provider, trimmed))
    if (trimmed.length < 10) {
      setModels([])
      setModel('')
      setModelNote('Paste the key. Models load on their own.')
      setModelWarn(false)
      return
    }
    setModelNote('Asking which models this key can use.')
    setModelWarn(false)
    keyWait.current = window.setTimeout(() => { void loadModels(value, provider) }, 400)
  }

  async function findMoments(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    if (apiKey.trim().length < 10 || !model) {
      setModelNote('Paste a key and wait for the models.')
      setModelWarn(true)
      return
    }
    setBusy(true)
    setVideos([])
    setClips([])
    setChosen([])
    setJob({
      id: '', status: 'running', progress: 0.04, step: 'Starting', tokens: emptyTokens,
      detail: 'AnuClipz is getting ready.', clips: [], title: '', thumbnail: '', error: '', file: '', files: [], quality: '',
      words_read: 0, words_sent: 0,
    })
    try {
      const { id } = await postJson<{ id: string }>('/api/jobs', {
        kind: 'analyze', url: url.trim(), provider, api_key: apiKey.trim(), model,
        query: query.trim(),
        export_dir: folder,
      })
      await watch(id, (next) => {
        if (next.status === 'done') {
          const found = next.clips || []
          setClips(found)
          setChosen(passingIndexes(found, scoreCut))
          setFilm(next.title || '')
          setThumb(next.thumbnail || '')
          if (next.source) setSource(next.source)
          setVideos([])
          setCaptionOn({})
          setTitleOn({})
          setPlayMark(null)
        }
      })
    } catch (err) {
      setBusy(false)
      setJob((current) => current && ({
        ...current, status: 'error', step: 'Stopped',
        detail: err instanceof Error ? err.message : 'The read failed.',
        error: err instanceof Error ? err.message : 'The read failed.',
      }))
    }
  }

  function toggle(index: number) {
    setChosen((current) => current.includes(index) ? current.filter((item) => item !== index) : [...current, index].sort((a, b) => a - b))
  }

  async function chooseFolder() {
    if (picking || busy) return
    setPicking(true)
    try {
      const data = await postJson<{ path: string }>('/api/folder', {})
      if (data.path) {
        setFolder(data.path)
        sessionStorage.setItem('anuclipz-export-dir', data.path)
      }
    } catch (err) {
      setJob((current) => current && ({
        ...current,
        status: 'error',
        step: 'Stopped',
        detail: err instanceof Error ? err.message : 'The folder could not be changed.',
      }))
    } finally {
      setPicking(false)
    }
  }

  useEffect(() => {
    if (!openExport) return
    document.body.style.overflow = 'hidden'
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape' && !document.fullscreenElement) setOpenExport(null)
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = ''
      document.removeEventListener('keydown', onKey)
    }
  }, [openExport])

  async function cutChosen() {
    const items = chosen.map((index) => clips[index]).filter((clip) => clip && scoreOf(clip) >= scoreCut)
    if (busy || items.length === 0) return
    setBusy(true)
    setVideos([])
    setJob({
      id: '', status: 'running', progress: 0.08, step: 'Starting the cut', tokens: job?.tokens || emptyTokens,
      detail: items.length > 1 ? `Preparing ${items.length} reels from the highest picture.` : 'Preparing that moment from the highest picture.',
      clips: [], title: '', thumbnail: '', error: '', file: '', files: [], quality: '',
      words_read: 0, words_sent: 0,
    })
    try {
      const { id } = await postJson<{ id: string }>('/api/jobs', {
        kind: 'cut',
        url: url.trim(),
        clips: items.map((clip) => ({
          start: clip.start,
          end: clip.end,
          hook: clip.hook,
          lines: clip.lines,
          title: clip.title,
          why: clip.why,
          captions: captionOn[momentKey(clip)] !== false,
          show_title: titleOn[momentKey(clip)] !== false,
        })),
        layout,
        splits: regions,
        film,
        source,
        export_dir: folder,
        provider,
        model,
        api_key: apiKey.trim(),
      })
      await watch(id, (next) => {
        const ready = next.files?.length ? next.files : next.file ? [{ file: next.file, title: '', quality: next.quality || '' }] : []
        if (next.status === 'done' && ready.length) {
          setVideos(ready)
          void fetch('/api/saved?dir=' + encodeURIComponent(folder)).then(async (response) => {
            if (!response.ok) return
            const data = await response.json()
            if (Array.isArray(data.files)) setExported(data.files)
          }).catch(() => {})
        }
      })
    } catch (err) {
      setBusy(false)
      setJob((current) => current && ({
        ...current, status: 'error', step: 'Stopped',
        detail: err instanceof Error ? err.message : 'The cut failed.',
      }))
    }
  }

  return (
    <>
      <Aurora />
      <div className="app">
      <motion.header initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}>
        <div className="brand">
          <p className="eyebrow">Shorts from one long video</p>
          <h1>AnuClipz</h1>
          <p className="tag">Find the moment, then export a reel that matches the preview.</p>
        </div>
        <div className="logo-slot">
          <img className="mark" src={logo} alt="Grand Theft Auto VI" />
        </div>
      </motion.header>
      <motion.form
        className="panel"
        onSubmit={findMoments}
        initial="hidden"
        animate="show"
        variants={{ show: { transition: { staggerChildren: 0.06, delayChildren: 0.08 } } }}
      >
        <Field label="YouTube link">
          <input id="url" type="url" required value={url} placeholder="https://www.youtube.com/watch?v=…" onChange={(event) => setUrl(event.target.value)} />
        </Field>
        <Field label="AI">
          <div className="segments" role="tablist" aria-label="AI">
            {PROVIDERS.map((item) => (
              <button key={item.id} type="button" role="tab" aria-selected={provider === item.id} className={provider === item.id ? 'on' : ''} onClick={() => chooseProvider(item.id)}>
                <span className="seg-label">{item.label}</span>
              </button>
            ))}
          </div>
        </Field>
        <motion.div className="stack" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.06 } } }}>
        <Field label="API key">
          <div className="key-slot">
            <input id="key" type="password" autoComplete="off" spellCheck={false} value={apiKey} placeholder="Paste the key" onChange={(event) => onKeyChange(event.target.value)} />
            {keySaved && (
              <span className="key-tick" role="img" aria-label="API key saved">
                <PlayerIcon name="check" />
              </span>
            )}
          </div>
          <p className="hint">{note}</p>
        </Field>
        <Field label="Model">
          <Menu
            label="Model"
            value={model}
            empty={loadingModels ? 'Loading models' : 'Paste a key first'}
            options={models}
            onChange={(id) => { setModel(id); sessionStorage.setItem('clipper-model-' + provider, id) }}
          />
          <p className={modelWarn ? 'hint warn' : 'hint'}>{modelNote}</p>
        </Field>
        <div className="pair">
          <Field label="Find a specific moment">
            <input id="query" type="text" value={query} placeholder="The plane on the runway" onChange={(event) => setQuery(event.target.value)} />
          </Field>
          <Field label="Score">
            <input
              id="score"
              type="number"
              min={0}
              max={99}
              inputMode="numeric"
              value={scoreCut}
              onChange={(event) => {
                const next = Number(event.target.value)
                if (!Number.isFinite(next)) return
                setScoreCut(Math.max(0, Math.min(99, Math.round(next))))
              }}
            />
          </Field>
        </div>
        <p className="hint">Moments at or above this score stay on the board. Set it to 0 to see every moment.</p>
        <div className="layouts">
          <span className="label">Layout</span>
          <div className="layout-row" role="radiogroup" aria-label="Reel layout">
            {([
              ['blur', 'Blurred game'],
              ['full', 'Full screen'],
              ['split', 'Two splits'],
            ] as const).map(([id, name]) => (
              <button key={id} type="button" role="radio" aria-checked={layout === id} className={layout === id ? 'layout-pick on' : 'layout-pick'} onClick={() => setLayout(id)}>
                <span className={`layout-mini ${id}`} aria-hidden="true" />
                <span>{name}</span>
              </button>
            ))}
          </div>
          {layout === 'split' && (
            <div className="split-setup">
              <div className="split-pick">
                <RegionFrame image={frame.image} regions={regions} onChange={setRegions} />
                <label className="frame-point">
                  <span>Frame at {clock(frame.shown)}</span>
                  <input
                    className="reel-seek"
                    type="range"
                    min={0}
                    max={frame.duration || 0}
                    step={1}
                    value={Math.min(frame.shown, frame.duration || 0)}
                    aria-label="Choose the frame used to place the two sections"
                    aria-valuetext={clock(frame.shown)}
                    disabled={!frame.duration}
                    style={{ ['--fill' as string]: `${frame.duration ? (frame.shown / frame.duration) * 100 : 0}%` }}
                    onInput={(event) => frame.move(Number(event.currentTarget.value))}
                  />
                </label>
                {frame.note && <p className="hint">{frame.note}</p>}
              </div>
              <div className="phone layout-split source-sample" aria-hidden="true">
                <div className="split-band top" style={cropStyle(frame.image, regions[0])} />
                <div className="split-band bottom" style={cropStyle(frame.image, regions[1])} />
              </div>
            </div>
          )}
          <p className="hint">
            {layout === 'blur' && 'The sharp game sits in the middle. The rest of the reel is a soft copy of the same picture.'}
            {layout === 'full' && 'The game fills the whole reel.'}
            {layout === 'split' && 'Drag the two boxes. They stay the shape of one half of the reel. Move the slider when the person walks out of a box.'}
          </p>
        </div>
        </motion.div>
        <motion.button className="primary" type="submit" disabled={busy} variants={rise} whileHover={busy ? undefined : { y: -1 }} whileTap={{ scale: 0.985 }}>
          {busy ? 'Working' : 'Find the moments'}
        </motion.button>
        <p className="hint">AI so far about {money(spend)}. A rough list price, saved on this computer.</p>
      </motion.form>
      <motion.section
        className="moments panel"
        initial="hidden"
        animate="show"
        variants={{ hidden: {}, show: { transition: { delayChildren: 0.28, staggerChildren: 0.08 } } }}
      >
        <motion.div className="moment-head" variants={rise}>
          <div>
            <h2>Moments</h2>
            {film && <p className="film">{film}</p>}
          </div>
          {clips.length > 0 && (
            <button className="texty" type="button" onClick={() => {
              const passing = passingIndexes(clips, scoreCut)
              const allOn = passing.length > 0 && passing.every((index) => chosen.includes(index))
              setChosen(allOn ? chosen.filter((index) => !passing.includes(index)) : [...new Set([...chosen, ...passing])].sort((a, b) => a - b))
            }}>
              {passingIndexes(clips, scoreCut).length > 0 && passingIndexes(clips, scoreCut).every((index) => chosen.includes(index)) ? 'Clear' : 'Select all'}
            </button>
          )}
        </motion.div>
        {youtubeId(url) && (
          <SourcePlayer
            pageUrl={url.trim()}
            play={playMark}
            layout={layout}
            regions={regions}
            still={layout === 'split' ? frame.image : thumb}
          />
        )}
        {clips.length > 0 && (
          <div className="moment-filters" role="toolbar" aria-label="Filter moments">
            {([['all', 'All'], ['kept', 'Kept']] as const).map(([id, label]) => (
              <button key={id} type="button" className={momentFilter === id ? 'on' : ''} aria-pressed={momentFilter === id} onClick={() => setMomentFilter(id)}>
                {label}
              </button>
            ))}
            <button type="button" onClick={() => setMomentSort((value) => value === 'score' ? 'time' : 'score')}>
              {momentSort === 'score' ? 'By score' : 'By time'}
            </button>
          </div>
        )}
        {clips.length === 0 && !busy && <motion.p className="empty" variants={rise}>Paste a link and find the moments. They leave when you reload this page.</motion.p>}
        {clips.length > 0 && clips.every((item) => scoreOf(item) < scoreCut) && (
          <p className="empty">Nothing scored {scoreCut} or higher. Lower the score to see the rest.</p>
        )}
        {clips.length === 0 && busy && (
          <div className="moment-list" aria-hidden="true">
            {[0, 1, 2].map((index) => <article className="moment moment-ghost" key={index} />)}
          </div>
        )}
        <motion.div className="moment-list" initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.07 } } }}>
          <AnimatePresence>
            {clips.map((item, index) => ({ item, index }))
              .filter(({ item, index }) => {
                if (scoreOf(item) < scoreCut) return false
                if (momentFilter === 'kept' && !chosen.includes(index)) return false
                return true
              })
              .sort((a, b) => momentSort === 'time' ? a.item.start - b.item.start : scoreOf(b.item) - scoreOf(a.item) || a.item.start - b.item.start)
              .map(({ item, index }) => {
              const key = momentKey(item)
              return (
                <MomentCard
                  key={key}
                  item={item}
                  image={item.thumb || thumb}
                  on={chosen.includes(index)}
                  captions={captionOn[key] !== false}
                  showTitle={titleOn[key] !== false}
                  layout={layout}
                  regions={regions}
                  onToggle={() => toggle(index)}
                  onCaptions={() => setCaptionOn((current) => ({ ...current, [key]: current[key] === false }))}
                  onTitle={() => setTitleOn((current) => ({ ...current, [key]: current[key] === false }))}
                  onPlay={() => {
                    setPlayMark({
                      start: item.start,
                      end: item.end,
                      nonce: Date.now(),
                      hook: item.hook,
                      caption: item.lines[0]?.text || '',
                      showTitle: titleOn[key] !== false,
                      showCaptions: captionOn[key] !== false,
                    })
                    const motion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
                    document.querySelector('.source-reel')?.scrollIntoView({ behavior: motion ? 'auto' : 'smooth', block: 'nearest' })
                  }}
                />
              )
            })}
          </AnimatePresence>
        </motion.div>
        {videos[0]?.quality && <p className="quality">{videos[0].quality}</p>}
        <motion.button className="where" type="button" variants={rise} onClick={() => void chooseFolder()} disabled={picking || busy}>
          <span className="label">Export location</span>
          <span className="path">{picking ? 'Choose a folder in the window that just opened.' : (folder || 'Downloads / AnuClipz')}</span>
        </motion.button>
        <motion.button className="primary export" type="button" variants={rise} disabled={busy || chosen.length === 0} whileHover={busy || chosen.length === 0 ? undefined : { y: -1 }} whileTap={{ scale: 0.985 }} onClick={() => void cutChosen()}>
          {chosen.length > 1 ? `Export ${chosen.length} reels` : 'Export reel'}
        </motion.button>
      </motion.section>
      <AnimatePresence>{job && <Work key="work" job={job} />}</AnimatePresence>
      <section className="exported panel" aria-label="Exported reels">
        <div className="stage-head">
          <h2>Exported</h2>
        </div>
        {exported.length === 0 && <p className="empty">Reels you export show up here. Open one to play it and copy the captions.</p>}
        <div className="preview-row">
          {exported.map((item) => (
            <figure className="preview" key={item.path || item.file}>
              <div className="phone">
                {item.poster ? <img className="reel" src={item.poster} alt="" /> : item.file ? <ReelStill src={item.file} /> : <div className="sharp blank" />}
                <button className="phone-open" type="button" aria-label={`Open ${maskText(item.title || 'reel')}`} onClick={() => setOpenExport(item)} />
              </div>
              <figcaption>{maskText(item.title || 'Reel')}</figcaption>
            </figure>
          ))}
        </div>
      </section>
      <AnimatePresence>
        {openExport && (
          <motion.div className="scrim" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setOpenExport(null)}>
            <motion.div
              className="dialog dialog-wide"
              role="dialog"
              aria-modal="true"
              aria-labelledby="export-title"
              initial={{ opacity: 0, y: 18 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 12 }}
              transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="dialog-reel">
                {openExport.file ? <ReelPlayer src={openExport.file} poster={openExport.poster} /> : <div className="sharp blank" />}
              </div>
              <div className="dialog-side">
                <div className="side-head">
                  <h2 id="export-title">{maskText(openExport.title || 'Reel')}</h2>
                  <button className="icon-btn" type="button" aria-label="Close" onClick={() => setOpenExport(null)}>
                    <PlayerIcon name="close" />
                  </button>
                </div>
                <div className="copy-list">
                  {openExport.poster && <img className="poster" src={openExport.poster} alt="" />}
                  {openExport.folder && <CopyCard kind="file" label="Folder" text={openExport.folder} />}
                  {openExport.path && <CopyCard kind="file" label="Video" text={openExport.path} />}
                  {openExport.thumb_path && <CopyCard kind="file" label="Thumbnail" text={openExport.thumb_path} />}
                  {openExport.meta_path && <CopyCard kind="file" label="Meta file" text={openExport.meta_path} />}
                  {openExport.tags && <CopyCard kind="youtube" label="YouTube tags" text={maskText(openExport.tags)} />}
                  {openExport.instagram && <CopyCard kind="instagram" label="Instagram caption" text={maskText(openExport.instagram)} />}
                  {openExport.youtube_title && <CopyCard kind="youtube" label="YouTube title" text={maskText(openExport.youtube_title)} />}
                  {openExport.youtube && <CopyCard kind="youtube" label="YouTube description" text={maskText(openExport.youtube)} />}
                  {openExport.tiktok && <CopyCard kind="tiktok" label="TikTok caption" text={maskText(openExport.tiktok)} />}
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
      </div>
    </>
  )
}
