import { useEffect, useId, useRef, useState, type FormEvent, type PointerEvent, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import logo from '../gta6.png'

type Line = { start: number; end: number; text: string }
type Clip = {
  start: number
  end: number
  hook: string
  title: string
  why: string
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
  meta_path?: string
  instagram?: string
  youtube_title?: string
  youtube?: string
  tiktok?: string
  thumb?: string
}
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
  words_read: number
  words_sent: number
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
      preload="auto"
      onLoadedData={() => {
        const node = video.current
        if (node && node.currentTime < 0.05) node.currentTime = 0.4
      }}
    />
  )
}

function ReelPlayer({ src }: { src: string }) {
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

function Check() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M3.2 8.2 6.4 11.3 12.8 4.7" />
    </svg>
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

function LiveReel({ clip, image, video, index }: { clip: Clip; image: string; video: string; index: number }) {
  const [line, setLine] = useState(-1)
  useEffect(() => {
    if (video || clip.lines.length === 0) return
    const span = Math.max(1, clip.end - clip.start)
    const started = performance.now()
    let frame = 0
    const tick = (now: number) => {
      const t = ((now - started) / 1000) % span
      const next = clip.lines.findIndex((item) => t >= item.start && t < item.end + 0.04)
      setLine((current) => (current === next ? current : next))
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [clip, video])
  const caption = line >= 0 && clip.lines[line] ? clip.lines[line].text : ''
  return (
    <motion.figure
      className="preview"
      initial={{ opacity: 0, y: 22, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', bounce: 0.22, duration: 0.55, delay: index * 0.08 }}
    >
      <Phone image={image} hook={maskText(clip.hook)} caption={maskText(caption)} video={video} />
      <figcaption>{maskText(clip.title)}</figcaption>
    </motion.figure>
  )
}

function Phone({ image, hook, caption, video }: { image: string; hook: string; caption: string; video: string }) {
  if (video) {
    return (
      <motion.div className="phone" initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ type: 'spring', bounce: 0.18, duration: 0.6 }}>
        <video className="reel" src={video} controls playsInline autoPlay />
      </motion.div>
    )
  }
  const safe = image.replace(/"/g, '')
  return (
    <motion.div className="phone" initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ type: 'spring', bounce: 0.18, duration: 0.6 }}>
      <div className="wash" style={safe ? { backgroundImage: `url("${safe}")` } : undefined} />
      {safe ? <img className="sharp" src={safe} alt="" /> : <div className="sharp blank" />}
      <AnimatePresence mode="wait">
        <motion.p key={hook} className="hook" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.25 }}>
          {hook}
        </motion.p>
      </AnimatePresence>
      <AnimatePresence mode="wait">
        {caption && (
          <motion.p key={caption} className="cap" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.18 }}>
            {caption}
          </motion.p>
        )}
      </AnimatePresence>
    </motion.div>
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
  const [count, setCount] = useState(4)
  const [url, setUrl] = useState('')
  const [note, setNote] = useState(NOTES.google)
  const [modelWarn, setModelWarn] = useState(false)
  const [modelNote, setModelNote] = useState('Paste the key. Models load on their own.')
  const keyWait = useRef(0)
  const [loadingModels, setLoadingModels] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const [film, setFilm] = useState('')
  const [thumb, setThumb] = useState('')
  const [picked, setPicked] = useState(0)
  const [chosen, setChosen] = useState<number[]>([])
  const [videos, setVideos] = useState<ReelFile[]>([])
  const [folder, setFolder] = useState('')
  const [picking, setPicking] = useState(false)
  const [exported, setExported] = useState<ReelFile[]>([])
  const [openExport, setOpenExport] = useState<ReelFile | null>(null)
  const [busy, setBusy] = useState(false)

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
    try {
      const savedExports = JSON.parse(sessionStorage.getItem('anuclipz-exported') || '[]')
      if (Array.isArray(savedExports)) setExported(savedExports)
    } catch {
      setExported([])
    }
  }, [])

  async function watch(id: string, onDone: (next: Job) => void) {
    const timer = window.setInterval(async () => {
      const response = await fetch('/api/jobs/' + id)
      const next = await response.json() as Job
      setJob(next)
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
        kind: 'analyze', url: url.trim(), provider, api_key: apiKey.trim(), model, count,
      })
      await watch(id, (next) => {
        if (next.status === 'done') {
          const found = next.clips || []
          setClips(found)
          setChosen(found.map((_, index) => index))
          setFilm(next.title || '')
          setThumb(next.thumbnail || '')
          setPicked(0)
          setVideos([])
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
    setPicked(index)
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
    const items = chosen.map((index) => clips[index]).filter(Boolean)
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
        })),
        film,
        export_dir: folder,
      })
      await watch(id, (next) => {
        const ready = next.files?.length ? next.files : next.file ? [{ file: next.file, title: '', quality: next.quality || '' }] : []
        if (next.status === 'done' && ready.length) {
          setVideos(ready)
          const saved = ready.filter((item) => item.path).map((item) => ({
            ...item,
            thumb: items.find((clip) => clip.start === item.start)?.thumb || '',
          }))
          if (saved.length) {
            setExported((current) => {
              const merged = [...current, ...saved.filter((item) => !current.some((old) => old.path === item.path))]
              sessionStorage.setItem('anuclipz-exported', JSON.stringify(merged))
              return merged
            })
          }
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
        <Field label="How many">
          <div className="stepper">
            <motion.button type="button" whileTap={{ scale: 0.94 }} onClick={() => setCount((value) => Math.max(1, value - 1))} aria-label="Fewer moments">−</motion.button>
            <span>{count}</span>
            <motion.button type="button" whileTap={{ scale: 0.94 }} onClick={() => setCount((value) => Math.min(6, value + 1))} aria-label="More moments">+</motion.button>
          </div>
        </Field>
        </motion.div>
        <motion.button className="primary" type="submit" disabled={busy} variants={rise} whileHover={busy ? undefined : { y: -1 }} whileTap={{ scale: 0.985 }}>
          {busy ? 'Working' : 'Find the moments'}
        </motion.button>
      </motion.form>
      <motion.section
        className="moments panel"
        initial="hidden"
        animate="show"
        variants={{ hidden: {}, show: { transition: { delayChildren: 0.28, staggerChildren: 0.08 } } }}
      >
        <motion.div className="moment-head" variants={rise}>
          <h2>Moments</h2>
          {clips.length > 0 && (
            <button className="texty" type="button" onClick={() => setChosen(chosen.length === clips.length ? [] : clips.map((_, index) => index))}>
              {chosen.length === clips.length ? 'Clear' : 'Select all'}
            </button>
          )}
        </motion.div>
        {clips.length === 0 && <motion.p className="empty" variants={rise}>Moments show up here after the model picks them. Each one says what the model found.</motion.p>}
        <motion.div className="moment-list" initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.07 } } }}>
          <AnimatePresence>
            {clips.map((item, index) => {
              const on = chosen.includes(index)
              return (
                <motion.button
                  key={`${item.start}-${item.end}`}
                  className={on ? (index === picked ? 'pick on focus' : 'pick on') : 'pick'}
                  type="button"
                  aria-pressed={on}
                  initial={{ opacity: 0, y: 20, scale: 0.96 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 10 }}
                  transition={{ type: 'spring', bounce: 0.28, duration: 0.55, delay: index * 0.07 }}
                  whileTap={{ scale: 0.98 }}
                  onClick={() => toggle(index)}
                >
                  <motion.span className="tick" aria-hidden="true" animate={{ scale: on ? 1 : 0.92 }} transition={{ type: 'spring', bounce: 0.3, duration: 0.35 }}>{on ? <Check /> : null}</motion.span>
                  <div>
                    <div className="title">{maskText(item.title)}</div>
                    <div className="when">{clock(item.start)} – {clock(item.end)} · {maskText(item.hook)}</div>
                    <p className="why">{maskText(item.why)}</p>
                  </div>
                </motion.button>
              )
            })}
          </AnimatePresence>
        </motion.div>
        <motion.button className="where" type="button" variants={rise} onClick={() => void chooseFolder()} disabled={picking || busy}>
          <span className="label">Export location</span>
          <span className="path">{picking ? 'Choose a folder in the window that just opened.' : (folder || 'Downloads / AnuClipz')}</span>
        </motion.button>
        <motion.button className="primary export" type="button" variants={rise} disabled={busy || chosen.length === 0} whileHover={busy || chosen.length === 0 ? undefined : { y: -1 }} whileTap={{ scale: 0.985 }} onClick={() => void cutChosen()}>
          {chosen.length > 1 ? `Export ${chosen.length} reels` : 'Export reel'}
        </motion.button>
      </motion.section>
      <AnimatePresence>{job && <Work key="work" job={job} />}</AnimatePresence>
      <section className="stage panel" aria-label="Reel preview">
        <div className="stage-head">
          <h2>Previews</h2>
          {film && <p className="film">{film}</p>}
        </div>
        {clips.length === 0 && <Phone image={thumb} hook="The plan breaks" caption="" video="" />}
        <motion.div className="preview-row" initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.08 } } }}>
          {clips.map((item, index) => (
            <LiveReel
              key={`${item.start}-${item.end}`}
              clip={item}
              index={index}
              image={item.thumb || thumb}
              video={videos.find((file) => file.start === item.start)?.file || videos.find((file) => file.title === item.title)?.file || ''}
            />
          ))}
        </motion.div>
        {videos[0]?.quality && <p className="quality">{videos[0].quality}</p>}
      </section>
      <section className="exported panel" aria-label="Exported reels">
        <div className="stage-head">
          <h2>Exported</h2>
        </div>
        {exported.length === 0 && <p className="empty">Reels you export show up here. Open one to play it and copy the captions.</p>}
        <div className="preview-row">
          {exported.map((item) => (
            <figure className="preview" key={item.path || item.file}>
              <div className="phone">
                {item.file ? <ReelStill src={item.file} /> : <div className="sharp blank" />}
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
                {openExport.file ? <ReelPlayer src={openExport.file} /> : <div className="sharp blank" />}
              </div>
              <div className="dialog-side">
                <div className="side-head">
                  <h2 id="export-title">{maskText(openExport.title || 'Reel')}</h2>
                  <button className="icon-btn" type="button" aria-label="Close" onClick={() => setOpenExport(null)}>
                    <PlayerIcon name="close" />
                  </button>
                </div>
                <div className="copy-list">
                  {openExport.path && <CopyCard kind="file" label="Saved file" text={openExport.path} />}
                  {openExport.meta_path && <CopyCard kind="file" label="Meta file" text={openExport.meta_path} />}
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
