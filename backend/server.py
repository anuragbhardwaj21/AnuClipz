"""Clipper engine. Local tools do the heavy work. The model only picks the cut."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import select
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import captions
import safe

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
SPEND_NAME = 'anuclipz-spend.json'


def scratch_dir() -> Path:
    root = Path(tempfile.gettempdir())
    for old in root.glob('anuclipz-*'):
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
    path = Path(tempfile.mkdtemp(prefix='anuclipz-'))
    (path / 'thumbs').mkdir()
    return path


SCRATCH = scratch_dir()
TOOLS = ROOT / '.tools'
FONTS = ROOT / 'fonts'
EXE = '.exe' if os.name == 'nt' else ''
RENDER = TOOLS / f'ffmpeg{EXE}'
PROBE = TOOLS / f'ffprobe{EXE}'
HOT = {
    'no', 'never', 'wait', 'look', 'run', 'dead', 'kill', 'what', 'why', 'how', 'stop',
    'oh', 'wow', 'money', 'police', 'shot', 'missed', 'won', 'lost', 'crash', 'final',
}
SKIP_MODEL = (
    'tts', 'image', 'lyria', 'robot', 'computer-use', 'nano-banana', 'antigravity',
    'deep-research', 'transcribe', 'embed', 'imagen', 'audio', 'realtime', 'whisper',
    'moderation', 'dall', 'search',
)
CHEAP_FIRST = (
    'gemini-flash-lite-latest', 'gemini-2.5-flash-lite', 'gemini-3.5-flash-lite',
    'gemini-3.1-flash-lite', 'gemini-flash-latest', 'gemini-3.5-flash', 'gemini-3.8-flash',
    'gpt-4o-mini', 'gpt-4.1-mini', 'gpt-4o',
)
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://127.0.0.1:5174', 'http://localhost:5174'],
    allow_methods=['GET', 'POST'],
    allow_headers=['*'],
)


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else 'Request failed.'
    return JSONResponse({'error': detail}, status_code=exc.status_code)


def fail(message: str, code: int = 400):
    raise HTTPException(status_code=code, detail=message[:500])


def clean(message: str, key: str = '') -> str:
    if key:
        message = message.replace(key, '…')
    message = re.sub(r'AIza[0-9A-Za-z_\-]{10,}', '…', message)
    message = re.sub(r'sk-[A-Za-z0-9_\-]{10,}', '…', message)
    message = re.sub(r'crsr_[A-Za-z0-9_\-]{6,}', '…', message)
    message = re.sub(r'AQ\.[0-9A-Za-z_\-]{10,}', '…', message)
    text = re.sub(r'\s+', ' ', message).strip()
    if 'timed out' in text.lower():
        return 'The model took too long. Pick a lighter model from the list and try again.'
    return text[:500]


def blank_tokens() -> dict:
    return {'prompt': 0, 'output': 0, 'thinking': 0, 'total': 0}


def pack_tokens(meta: dict | None) -> dict:
    meta = meta or {}
    prompt = int(meta.get('promptTokenCount') or meta.get('prompt_tokens') or 0)
    output = int(meta.get('candidatesTokenCount') or meta.get('completion_tokens') or 0)
    thinking = int(meta.get('thoughtsTokenCount') or 0)
    total = int(meta.get('totalTokenCount') or meta.get('total_tokens') or (prompt + output + thinking))
    return {'prompt': prompt, 'output': output, 'thinking': thinking, 'total': total}


def spend_file() -> Path:
    folder = prepare_folder('')
    path = folder / SPEND_NAME
    legacy = DATA / 'spend.json'
    if not path.is_file() and legacy.is_file():
        path.write_bytes(legacy.read_bytes())
        legacy.unlink(missing_ok=True)
    return path


def price_for(provider: str, model: str) -> tuple[float, float, str]:
    name = (model or '').lower()
    if provider == 'openai':
        if 'mini' in name:
            return 0.15, 0.60, 'GPT mini list price'
        return 2.50, 10.0, 'larger GPT list price'
    if '3.5' in name and 'lite' in name:
        return 0.30, 2.50, 'Gemini 3.5 Flash-Lite list price'
    if 'lite' in name:
        return 0.10, 0.40, 'Gemini Flash-Lite list price'
    if 'flash' in name:
        return 0.30, 2.50, 'Gemini Flash list price'
    return 1.25, 10.0, 'rough larger-model list price'


def estimate_usd(provider: str, model: str, tokens: dict) -> tuple[float, str]:
    incoming, outgoing, label = price_for(provider, model)
    prompt = int(tokens.get('prompt') or 0)
    output = int(tokens.get('output') or 0) + int(tokens.get('thinking') or 0)
    usd = prompt / 1_000_000 * incoming + output / 1_000_000 * outgoing
    return round(usd, 6), label


def read_spend() -> dict:
    path = spend_file()
    if not path.is_file():
        return {'usd': 0, 'prompt': 0, 'output': 0, 'thinking': 0, 'runs': []}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {'usd': 0, 'prompt': 0, 'output': 0, 'thinking': 0, 'runs': []}
    return data if isinstance(data, dict) else {'usd': 0, 'prompt': 0, 'output': 0, 'thinking': 0, 'runs': []}


def add_spend(provider: str, model: str, tokens: dict, note: str) -> dict:
    usd, label = estimate_usd(provider, model, tokens)
    if usd <= 0 and int(tokens.get('total') or 0) <= 0:
        return read_spend()
    with LOCK:
        current = read_spend()
        runs = [item for item in (current.get('runs') or []) if isinstance(item, dict)]
        runs.append({
            'at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'provider': provider,
            'model': model,
            'prompt': int(tokens.get('prompt') or 0),
            'output': int(tokens.get('output') or 0),
            'thinking': int(tokens.get('thinking') or 0),
            'usd': usd,
            'rate': label,
            'note': note,
        })
        payload = {
            'usd': round(float(current.get('usd') or 0) + usd, 6),
            'prompt': int(current.get('prompt') or 0) + int(tokens.get('prompt') or 0),
            'output': int(current.get('output') or 0) + int(tokens.get('output') or 0),
            'thinking': int(current.get('thinking') or 0) + int(tokens.get('thinking') or 0),
            'rate': label,
            'runs': runs[-200:],
        }
        spend_file().write_text(json.dumps(payload, indent=2), encoding='utf-8')
        return payload


def charge(job_id: str, provider: str, model: str, tokens: dict, note: str):
    payload = add_spend(provider, model, tokens, note)
    current = snapshot(job_id).get('tokens') or blank_tokens()
    merged = {name: int(current.get(name) or 0) + int(tokens.get(name) or 0) for name in ('prompt', 'output', 'thinking', 'total')}
    usd, _label = estimate_usd(provider, model, tokens)
    cost = round(float(snapshot(job_id).get('cost') or 0) + usd, 6)
    update(job_id, tokens=merged, cost=cost, spend=float(payload.get('usd') or 0))


def new_job() -> str:
    job_id = uuid.uuid4().hex
    with LOCK:
        JOBS[job_id] = {
            'id': job_id,
            'status': 'running',
            'progress': 0.02,
            'step': 'Starting',
            'detail': 'Getting ready.',
            'tokens': blank_tokens(),
            'clips': [],
            'title': '',
            'thumbnail': '',
            'error': '',
            'file': '',
            'files': [],
            'quality': '',
            'words_read': 0,
            'words_sent': 0,
        }
    return job_id


def update(job_id: str, **fields):
    with LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(fields)


def snapshot(job_id: str) -> dict:
    with LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else {}


def youtube_url(url: str) -> str:
    url = (url or '').strip()
    if not re.match(r'^https?://((www|m)\.)?(youtube\.com|youtu\.be)/', url):
        fail('Paste a YouTube link.')
    return url.split('&')[0]


STREAMS: dict[str, dict] = {}
RESOLVE = threading.Lock()


def watch_id(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or '').lower()
    if host.endswith('youtu.be'):
        return parsed.path.strip('/').split('/')[0]
    return urllib.parse.parse_qs(parsed.query).get('v', [''])[0]


def direct_url(raw: str) -> str:
    parts = urllib.parse.urlsplit(raw)
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if key.lower() != 'range']
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), ''))


def ffmpeg_bin() -> str:
    brew = Path('/opt/homebrew/bin/ffmpeg')
    if sys.platform == 'darwin' and brew.is_file():
        return str(brew)
    local = TOOLS / 'ffmpeg'
    return str(local) if local.is_file() else 'ffmpeg'


def media_pair(page: str) -> dict:
    vid = watch_id(page)
    if not re.fullmatch(r'[\w-]{6,}', vid):
        raise RuntimeError('That link is not a video.')
    now = time.time()
    with RESOLVE:
        cached = STREAMS.get(vid)
        if cached and cached['until'] > now:
            return cached
    import yt_dlp
    with yt_dlp.YoutubeDL(ydl_opts(skip_download=True)) as ydl:
        info = ydl.extract_info(page, download=False)
    formats = info.get('formats') or []

    def usable(fmt: dict) -> str:
        raw = fmt.get('url')
        return direct_url(str(raw)) if raw else ''

    videos = []
    for fmt in formats:
        if fmt.get('vcodec') in (None, 'none') or fmt.get('acodec') not in (None, 'none'):
            continue
        height = int(fmt.get('height') or 0)
        if not usable(fmt) or not 0 < height <= 720:
            continue
        videos.append(fmt)
    videos.sort(key=lambda fmt: (1 if fmt.get('protocol') == 'https' else 0, int(fmt.get('height') or 0), float(fmt.get('tbr') or 0)))

    def audio_rank(fmt: dict) -> tuple:
        note = str(fmt.get('format_note') or '').lower()
        if 'original' in note:
            rank = 3
        elif 'dub' in note:
            rank = 0
        else:
            rank = 2
        return (rank, float(fmt.get('abr') or 0))

    audios = [fmt for fmt in formats if fmt.get('acodec') not in (None, 'none') and fmt.get('vcodec') in (None, 'none') and fmt.get('ext') in ('m4a', 'mp4') and usable(fmt)]
    audios.sort(key=audio_rank)
    if not videos or not audios:
        raise RuntimeError('This video has no stream our player can open.')
    headers = info.get('http_headers') or {}
    item = {
        'video': usable(videos[-1]),
        'audio': usable(audios[-1]),
        'agent': str(headers.get('User-Agent') or 'Mozilla/5.0'),
        'duration': float(info.get('duration') or 0),
        'until': time.time() + 30 * 60,
    }
    with RESOLVE:
        STREAMS[vid] = item
    return item


def start_preview(page: str, start: float, end: float):
    if end <= start:
        raise RuntimeError('That part is not one scene.')
    pair = media_pair(page)
    length = min(float(SCENE_MAX), max(1.0, end - start))
    begin = f'{max(0.0, start):.3f}'
    span = f'{length:.3f}'
    cmd = [
        ffmpeg_bin(), '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-ss', begin, '-t', span, '-user_agent', pair['agent'], '-i', pair['video'],
        '-ss', begin, '-t', span, '-user_agent', pair['agent'], '-i', pair['audio'],
        '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy',
        '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
        '-f', 'mp4', 'pipe:1',
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    ready, _, _ = select.select([proc.stdout], [], [], 20)
    first = proc.stdout.read(64 * 1024) if ready and proc.stdout else b''
    if not first:
        proc.kill()
        raise RuntimeError('That part could not be opened.')
    return proc, first


FRAME_LOCK = threading.Lock()


def grab_frame(page: str, at: float | None) -> tuple[Path, float, float]:
    pair = media_pair(page)
    duration = float(pair.get('duration') or 0)
    point = duration / 2 if at is None and duration > 1 else float(at or 0)
    if duration > 1:
        point = min(max(0.0, point), max(0.0, duration - 0.4))
    else:
        point = max(0.0, point)
    second = int(round(point))
    folder = SCRATCH / 'stills'
    folder.mkdir(exist_ok=True)
    path = folder / f'{watch_id(page)}-{second}.jpg'
    if path.is_file() and path.stat().st_size > 800:
        return path, float(second), duration
    cmd = [
        ffmpeg_bin(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
        '-ss', f'{point:.3f}', '-user_agent', pair['agent'], '-i', pair['video'],
        '-frames:v', '1', '-q:v', '3', str(path),
    ]
    with FRAME_LOCK:
        if path.is_file() and path.stat().st_size > 800:
            return path, float(second), duration
        proc = subprocess.run(cmd, capture_output=True, timeout=45)
    if proc.returncode != 0 or not path.is_file() or path.stat().st_size < 800:
        path.unlink(missing_ok=True)
        raise RuntimeError('That frame could not be opened.')
    return path, float(second), duration


def ydl_opts(**extra):
    opts = {
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 60,
        'retries': 2,
        'noprogress': True,
    }
    if sys.platform == 'darwin' and os.path.isdir('/opt/homebrew/bin'):
        opts['ffmpeg_location'] = '/opt/homebrew/bin'
    elif RENDER.is_file():
        opts['ffmpeg_location'] = str(TOOLS)
    opts.update(extra)
    return opts


def read_error(exc: urllib.error.HTTPError) -> str:
    body = exc.read().decode('utf-8', 'replace')
    try:
        message = (json.loads(body).get('error') or {}).get('message') or body
    except json.JSONDecodeError:
        message = body or f'HTTP {exc.code}'
    return str(message)[:400]


def request_json(req: urllib.request.Request, timeout: int) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(read_error(exc)) from None
    except (TimeoutError, urllib.error.URLError) as exc:
        reason = getattr(exc, 'reason', exc)
        if 'timed out' in str(reason).lower():
            raise RuntimeError('The model took too long. Pick a lighter model from the list and try again.') from None
        raise RuntimeError(f'Could not reach the AI. {reason}') from None


def get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers={'User-Agent': 'clipper', **headers})
    return request_json(req, 40)


def post_json(url: str, payload: dict, headers: dict, timeout: int = 150) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'User-Agent': 'clipper', **headers},
        method='POST',
    )
    return request_json(req, timeout)


def text_model(name: str) -> bool:
    low = name.lower()
    return bool(name) and not any(part in low for part in SKIP_MODEL)


def ranked(ids: list[str]) -> list[dict]:
    order = {name: index for index, name in enumerate(CHEAP_FIRST)}
    unique = sorted(set(ids), key=lambda name: (order.get(name, 1000), name))
    return [{'id': name, 'label': name} for name in unique]


def google_models(key: str) -> list[dict]:
    url = 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=100'
    ids: list[str] = []
    for _ in range(5):
        data = get_json(url, {'x-goog-api-key': key})
        for model in data.get('models') or []:
            name = str(model.get('name') or '').removeprefix('models/')
            methods = model.get('supportedGenerationMethods') or []
            if 'generateContent' in methods and text_model(name):
                ids.append(name)
        token = data.get('nextPageToken')
        if not token:
            break
        url = 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=100&pageToken=' + urllib.parse.quote(token)
    return ranked(ids)


def openai_models(key: str) -> list[dict]:
    data = get_json('https://api.openai.com/v1/models', {'Authorization': f'Bearer {key}'})
    ids = []
    for item in data.get('data') or []:
        name = str(item.get('id') or '')
        low = name.lower()
        if (low.startswith('gpt-') or low.startswith('chatgpt') or re.match(r'o\d', low)) and text_model(name):
            ids.append(name)
    return ranked(ids)


def model_name(raw: str, default: str) -> str:
    name = (raw or '').strip().removeprefix('models/') or default
    if not re.fullmatch(r'[A-Za-z0-9._\-]{1,80}', name):
        fail('That model name looks wrong.')
    return name


def sentences(words: list[dict]) -> list[dict]:
    groups, current = [], []
    for index, word in enumerate(words):
        current.append(word)
        nxt = words[index + 1]['start'] if index + 1 < len(words) else word['end'] + 1
        gap = nxt - word['end'] > 0.8
        if word['text'].endswith(('.', '?', '!', '…')) or len(current) >= 16 or gap:
            groups.append({
                'start': current[0]['start'],
                'end': current[-1]['end'],
                'text': ' '.join(item['text'] for item in current),
            })
            current = []
    if current:
        groups.append({
            'start': current[0]['start'],
            'end': current[-1]['end'],
            'text': ' '.join(item['text'] for item in current),
        })
    return groups


def local_score(text: str) -> int:
    tokens = re.findall(r"[a-z']+", text.lower())
    score = text.count('?') * 4 + text.count('!') * 4
    score += sum(2 for token in tokens if token in HOT)
    if text.rstrip().endswith(('.', '?', '!')):
        score += 2
    return score


def windows(rows: list[dict], min_len: float = 18, max_len: float = 42) -> list[dict]:
    found, count = [], len(rows)
    for start_index in range(count):
        end_index = start_index
        start = rows[start_index]['start']
        while end_index < count and rows[end_index]['end'] - start < min_len:
            end_index += 1
        if end_index >= count or rows[end_index]['end'] - start > max_len:
            continue
        while end_index + 1 < count and rows[end_index + 1]['end'] - start <= max_len:
            end_index += 1
        text = re.sub(r'\s+', ' ', ' '.join(rows[i]['text'] for i in range(start_index, end_index + 1))).strip()
        if len(text.split()) < 12:
            continue
        found.append({
            'start': round(start, 2),
            'end': round(rows[end_index]['end'], 2),
            'text': text[:160],
            'score': local_score(text),
        })
    return found


def overlaps(left: dict, right: dict) -> bool:
    return not (left['end'] <= right['start'] + 3 or left['start'] >= right['end'] - 3)


def shortlist(words: list[dict], limit: int) -> list[dict]:
    rows = sentences(words)
    cands = windows(rows) or windows(rows, 12, 50)
    ranked_rows = sorted(cands, key=lambda item: item['score'], reverse=True)
    buckets: dict[int, dict] = {}
    for cand in ranked_rows:
        buckets.setdefault(int(cand['start'] // 600), cand)
    chosen: list[dict] = []
    for cand in sorted(buckets.values(), key=lambda item: item['score'], reverse=True):
        if any(overlaps(cand, old) for old in chosen):
            continue
        chosen.append(cand)
        if len(chosen) >= limit:
            break
    if len(chosen) < limit:
        for cand in ranked_rows:
            if any(overlaps(cand, old) for old in chosen):
                continue
            chosen.append(cand)
            if len(chosen) >= limit:
                break
    chosen.sort(key=lambda item: item['start'])
    return chosen


def caption_lines(words: list[dict], start: float, end: float) -> list[dict]:
    inside = [word for word in words if start <= word['start'] < end]
    lines, buf = [], []
    for word in inside:
        buf.append(word)
        if len(buf) >= 3 or word['text'].endswith(('.', '?', '!')):
            lines.append({
                'start': round(max(0, buf[0]['start'] - start), 2),
                'end': round(max(0.18, buf[-1]['end'] - start), 2),
                'text': ' '.join(item['text'] for item in buf),
            })
            buf = []
    if buf:
        lines.append({
            'start': round(max(0, buf[0]['start'] - start), 2),
            'end': round(max(0.18, buf[-1]['end'] - start), 2),
            'text': ' '.join(item['text'] for item in buf),
        })
    return lines[:90]


def prompt_for(title: str, cands: list[dict], count: int) -> str:
    rows = '\n'.join(f"{n}. {cand['start']:.1f}-{cand['end']:.1f} | {cand['text']}" for n, cand in enumerate(cands, 1))
    return (
        'Pick short clips for Instagram, YouTube Shorts, and TikTok.\n'
        'Return JSON only: {"clips":[{"start":0,"end":0,"hook":"","title":"","why":"","instagram":0,"youtube":0,"tiktok":0}]}\n'
        f'Pick {count}. Copy start and end from the list. Do not invent a time.\n'
        'hook is 3 to 6 words burned on the video.\n'
        'If a spoken word is sexual or a slur, write the masked form with the second letter as *, like p*rn. Never write the raw word.\n'
        'title is one line a stranger would tap. No hashtags.\n'
        'why is two sentences: what is happening, then why a stranger would stop scrolling.\n'
        'Scores are a guess from 0 to 99, not a promise of views.\n'
        f'Video: {title[:100]}\n{rows}'
    )


def parse_json(text: str) -> dict:
    text = re.sub(r'^```(?:json)?', '', text.strip()).strip()
    text = re.sub(r'```$', '', text).strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        raise RuntimeError('The model did not return a clip list.')
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError('The model did not return a clip list.') from exc


def ask(provider: str, key: str, model: str, prompt: str, on_tokens, tokens: int = 1600, temperature: float = 0.3) -> str:
    if provider == 'google':
        data = post_json(
            f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
            {
                'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
                'generationConfig': {'temperature': temperature, 'maxOutputTokens': tokens},
            },
            {'x-goog-api-key': key},
        )
        on_tokens(pack_tokens(data.get('usageMetadata')))
        parts = ((data.get('candidates') or [{}])[0].get('content') or {}).get('parts') or []
        text = '\n'.join(part.get('text') or '' for part in parts).strip()
        if not text:
            raise RuntimeError('The model returned no clip list. Pick another model and try again.')
        return text
    data = post_json(
        'https://api.openai.com/v1/chat/completions',
        {
            'model': model,
            'temperature': temperature,
            'max_tokens': tokens,
            'response_format': {'type': 'json_object'},
            'messages': [{'role': 'user', 'content': prompt}],
        },
        {'Authorization': f'Bearer {key}'},
    )
    on_tokens(pack_tokens(data.get('usage')))
    try:
        return data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        message = (data.get('error') or {}).get('message') or 'ChatGPT returned nothing.'
        raise RuntimeError(message) from exc


def ask_vision(provider: str, key: str, model: str, prompt: str, shots: list[tuple[int, bytes]], on_tokens, tokens: int = 1600) -> str:
    if not shots:
        return ask(provider, key, model, prompt, on_tokens)
    if provider == 'google':
        parts: list[dict] = [{'text': prompt}]
        for number, raw in shots:
            parts.append({'text': f'Picture for clip {number}.'})
            parts.append({'inlineData': {'mimeType': 'image/jpeg', 'data': base64.b64encode(raw).decode()}})
        data = post_json(
            f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
            {
                'contents': [{'role': 'user', 'parts': parts}],
                'generationConfig': {'temperature': 0.2, 'maxOutputTokens': tokens},
            },
            {'x-goog-api-key': key},
            timeout=180,
        )
        on_tokens(pack_tokens(data.get('usageMetadata')))
        bits = ((data.get('candidates') or [{}])[0].get('content') or {}).get('parts') or []
        text = '\n'.join(part.get('text') or '' for part in bits).strip()
        if not text:
            raise RuntimeError('The model returned no clip list.')
        return text
    content: list[dict] = [{'type': 'text', 'text': prompt}]
    for number, raw in shots:
        content.append({'type': 'text', 'text': f'Picture for clip {number}.'})
        content.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(raw).decode()}})
    data = post_json(
        'https://api.openai.com/v1/chat/completions',
        {
            'model': model,
            'temperature': 0.2,
            'max_tokens': tokens,
            'response_format': {'type': 'json_object'},
            'messages': [{'role': 'user', 'content': content}],
        },
        {'Authorization': f'Bearer {key}'},
        timeout=180,
    )
    on_tokens(pack_tokens(data.get('usage')))
    try:
        return data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        message = (data.get('error') or {}).get('message') or 'ChatGPT returned nothing.'
        raise RuntimeError(message) from exc


def as_score(value) -> int | None:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(99, number))


def paint(pool: list[dict], raw: dict):
    for item in raw.get('clips') or []:
        if not isinstance(item, dict):
            continue
        try:
            start, end = float(item.get('start')), float(item.get('end'))
        except (TypeError, ValueError):
            continue
        cand = nearest(pool, start, end)
        if cand is None:
            continue
        union_start, union_end = min(start, float(cand['start'])), max(end, float(cand['end']))
        if SCENE_MIN <= union_end - union_start <= SCENE_MAX:
            cand['start'] = round(union_start, 2)
            cand['end'] = round(union_end, 2)
        for key in ('hook', 'title', 'why'):
            text = re.sub(r'\s+', ' ', str(item.get(key) or '')).strip().strip('"').strip("'")
            if text:
                cand[key] = text
        heat = as_score(item.get('heat'))
        if heat is not None:
            cand['heat'] = heat


def nearest(cands: list[dict], start: float, end: float) -> dict | None:
    best, best_overlap = None, 0.0
    for cand in cands:
        overlap = min(end, cand['end']) - max(start, cand['start'])
        if overlap > best_overlap:
            best, best_overlap = cand, overlap
    return best if best is not None and best_overlap >= 4 else None


def take_clips(raw: dict, cands: list[dict], words: list[dict], count: int) -> list[dict]:
    found, used = [], set()
    for item in raw.get('clips') or []:
        if not isinstance(item, dict):
            continue
        try:
            start, end = float(item.get('start')), float(item.get('end'))
        except (TypeError, ValueError):
            continue
        cand = nearest(cands, start, end)
        if cand is None or id(cand) in used:
            continue
        used.add(id(cand))
        hook = re.sub(r'\s+', ' ', str(item.get('hook') or '')).strip().strip('"').strip("'")
        title = re.sub(r'\s+', ' ', str(item.get('title') or '')).strip().strip('"').strip("'")
        if not hook:
            hook = ' '.join(cand['text'].split()[:6])
        if not title:
            title = hook
        why = re.sub(r'\s+', ' ', str(item.get('why') or '')).strip()[:320] or 'This moment can stand on its own.'
        start = min(start, float(cand['start']))
        end = max(end, float(cand['end']))
        if end - start > SCENE_MAX or end - start < SCENE_MIN:
            start, end = float(cand['start']), float(cand['end'])
        lines = caption_lines(words, start, end) or [{
            'start': 0, 'end': round(end - start, 2), 'text': cand['text'][:120],
        }]
        heat = as_score(item.get('heat'))
        if heat is None:
            heat = as_score(cand.get('heat')) or 0
        found.append({
            'start': round(start, 2),
            'end': round(end, 2),
            'hook': safe.censor(' '.join(hook.split()[:8])[:80]),
            'title': safe.censor(title[:90]),
            'why': safe.censor(why),
            'heat': heat,
            'instagram': as_score(item.get('instagram')),
            'youtube': as_score(item.get('youtube')),
            'tiktok': as_score(item.get('tiktok')),
            'lines': [{**line, 'text': safe.censor(line['text'])} for line in lines],
        })
        if len(found) >= count:
            break
    return found


def storyboard(info: dict) -> dict | None:
    boards = [item for item in (info.get('formats') or []) if str(item.get('format_id') or '').startswith('sb') and item.get('fragments')]
    if not boards:
        return None
    return max(boards, key=lambda item: int(item.get('width') or 0) * int(item.get('height') or 0))


def moment_frames(info: dict, clips: list[dict]):
    board = storyboard(info)
    if board is None:
        return
    columns = max(1, int(board.get('columns') or 1))
    rows = max(1, int(board.get('rows') or 1))
    tile_w, tile_h = int(board.get('width') or 0), int(board.get('height') or 0)
    fragments = board.get('fragments') or []
    if tile_w < 16 or tile_h < 16 or not fragments:
        return
    per_sheet = columns * rows
    step = 1 / float(board.get('fps') or 0.1)
    used: set[int] = set()
    sheets: dict[str, Path] = {}
    folder = SCRATCH / 'thumbs'
    folder.mkdir(parents=True, exist_ok=True)
    tool = str(RENDER if RENDER.is_file() else 'ffmpeg')
    for clip in clips:
        moment = (float(clip['start']) + float(clip['end'])) / 2
        index = max(0, int(moment / step))
        while index in used:
            index += 1
        index = min(index, len(fragments) * per_sheet - 1)
        used.add(index)
        sheet_url = fragments[index // per_sheet].get('url')
        if not isinstance(sheet_url, str):
            continue
        try:
            sheet = sheets.get(sheet_url)
            if sheet is None:
                sheet = folder / f'sheet-{uuid.uuid4().hex}.jpg'
                request = urllib.request.Request(sheet_url, headers={'User-Agent': 'clipper'})
                with urllib.request.urlopen(request, timeout=30) as response:
                    sheet.write_bytes(response.read())
                sheets[sheet_url] = sheet
            cell = index % per_sheet
            name = uuid.uuid4().hex
            out = folder / f'{name}.jpg'
            crop = f'crop={tile_w}:{tile_h}:{(cell % columns) * tile_w}:{(cell // columns) * tile_h}'
            proc = subprocess.run([tool, '-y', '-hide_banner', '-loglevel', 'error', '-i', str(sheet), '-vf', crop, '-frames:v', '1', str(out)], capture_output=True, text=True, timeout=30)
            if proc.returncode == 0 and out.is_file():
                clip['thumb'] = f'/api/thumb/{name}.jpg'
        except (OSError, urllib.error.URLError, TimeoutError, subprocess.TimeoutExpired):
            continue
    for sheet in sheets.values():
        sheet.unlink(missing_ok=True)


def caption_rows(words: list[dict]) -> list[dict]:
    rows = sentences(words)
    for index, row in enumerate(rows):
        row['id'] = index
    return rows


def row_chunks(rows: list[dict]) -> list[list[dict]]:
    chunks, index, count = [], 0, len(rows)
    while index < count and len(chunks) < 30:
        start, chars, end = rows[index]['start'], 0, index
        while end + 1 < count and rows[end + 1]['end'] - start <= 20 * 60 and chars < 12000:
            end += 1
            chars += len(rows[end]['text'])
        chunks.append(rows[index:end + 1])
        if end >= count - 1:
            break
        border = start + 17 * 60
        nxt = index
        while nxt + 1 <= end and rows[nxt + 1]['start'] < border:
            nxt += 1
        index = nxt + 1 if nxt >= index else end + 1
    return chunks


SCENE_MIN = 12
SCENE_MAX = 80


def fit_span(rows: list[dict], start_id: int, end_id: int):
    by_id = {row['id']: pos for pos, row in enumerate(rows)}
    if start_id not in by_id or end_id not in by_id:
        return None
    left, right = by_id[min(start_id, end_id)], by_id[max(start_id, end_id)]
    while right + 1 < len(rows) and rows[right]['end'] - rows[left]['start'] < SCENE_MIN and rows[right + 1]['end'] - rows[left]['start'] <= SCENE_MAX:
        right += 1
    while left > 0 and rows[right]['end'] - rows[left]['start'] < SCENE_MIN and rows[right]['end'] - rows[left - 1]['start'] <= SCENE_MAX:
        left -= 1
    while right > left and rows[right]['end'] - rows[left]['start'] > SCENE_MAX:
        right -= 1
    start, end = float(rows[left]['start']), float(rows[right]['end'])
    if end - start < SCENE_MIN or end - start > SCENE_MAX:
        return None
    text = re.sub(r'\s+', ' ', ' '.join(row['text'] for row in rows[left:right + 1])).strip()
    return round(start, 2), round(end, 2), text[:700]


def pool_from_model(raw: dict, rows: list[dict]) -> list[dict]:
    found = []
    for item in raw.get('clips') or []:
        if not isinstance(item, dict):
            continue
        try:
            span = fit_span(rows, int(item.get('start_id')), int(item.get('end_id')))
        except (TypeError, ValueError):
            continue
        if span is None:
            continue
        start, end, text = span
        if any(overlaps({'start': start, 'end': end}, old) for old in found):
            continue
        hook = re.sub(r'\s+', ' ', str(item.get('hook') or '')).strip()
        title = re.sub(r'\s+', ' ', str(item.get('title') or '')).strip()
        why = re.sub(r'\s+', ' ', str(item.get('why') or '')).strip()
        try:
            heat = int(round(float(item.get('heat'))))
        except (TypeError, ValueError):
            heat = local_score(text)
        found.append({
            'start': start,
            'end': end,
            'text': text,
            'hook': hook,
            'title': title or hook,
            'why': why,
            'heat': max(0, min(99, heat)),
            'score': local_score(text),
        })
    return found


def discover_prompt(title: str, chunk: list[dict], query: str = '') -> str:
    lines = '\n'.join(f"{row['id']}|{row['start']:.0f}|{row['text'][:220]}" for row in chunk)
    focus = ''
    if query.strip():
        focus = (
            f'The viewer asked for this and nothing else: {query.strip()[:200]}.\n'
            'Return only scenes that match that request. Skip everything else.\n'
        )
    return (
        'You hunt the crazy parts of one long video.\n'
        'A crazy part makes a stranger stop scrolling: a chase, a crash, a fail, panic, an insult, an explosion, or a payoff after a setup. Skip small talk.\n'
        'Return JSON only: {"clips":[{"start_id":0,"end_id":0,"hook":"","title":"","why":"","heat":0}]}\n'
        'Return every real moment in this stretch, at most 6. Return none if this stretch is dull.\n'
        'start_id is the sentence where the scene starts. end_id is the sentence after the payoff and the reaction. Do not start or end in the middle of a thought.\n'
        'The span is 12 to 80 seconds. Start where the scene starts and end when the payoff lands, on a sentence. Never go past 80 seconds.\n'
        + focus +
        'hook is 3 to 6 words. title is one line a stranger would tap. why is what happens, then why it stops a scroll.\n'
        'Write the hook, title, and why in English. The spoken lines below are already in English letters.\n'
        'If a spoken word is sexual or a slur, write it masked, like p*rn.\n'
        'heat is 0 to 99. Save 90 and above for a moment that is rare in the whole video.\n'
        f'Video: {title[:120]}\n{lines}'
    )


def judge_prompt(title: str, cands: list[dict], count: int, pictured: list[int]) -> str:
    rows = '\n'.join(
        f"{n}. {cand['start']:.1f}-{cand['end']:.1f} | heat {cand.get('heat', 0)} | {cand.get('text', '')[:280]}"
        for n, cand in enumerate(cands, 1)
    )
    pictures = ', '.join(str(n) for n in pictured) or 'none'
    return (
        'You are the last judge for these scenes.\n'
        'Return JSON only: {"clips":[{"start":0,"end":0,"hook":"","title":"","why":"","heat":0}]}\n'
        'A scene you do not mention stays as it is. Mention a scene to widen its times or change its heat.\n'
        'Copy start and end from the list. You may move them earlier or later so the scene includes the setup and the reaction. Do not invent a time that is not in the list.\n'
        'Do not cut the list down to a fixed count.\n'
        f'Pictures are attached for clips {pictures}. A dull picture lowers the heat. A wild picture raises it.\n'
        'hook is 3 to 6 words, in English. heat is 0 to 99. If a word is sexual or a slur, write it masked, like p*rn.\n'
        f'Video: {title[:120]}\n{rows}'
    )


def thumb_bytes(clip: dict) -> bytes | None:
    match = re.fullmatch(r'/api/thumb/([a-f0-9]{32}\.jpg)', str(clip.get('thumb') or ''))
    if not match:
        return None
    path = SCRATCH / 'thumbs' / match.group(1)
    if not path.is_file() or path.stat().st_size > 400_000:
        return None
    return path.read_bytes()


def keep_best(cands: list[dict], limit: int) -> list[dict]:
    ranked = sorted(cands, key=lambda item: (item.get('heat') or 0, item.get('score') or 0), reverse=True)
    kept: list[dict] = []
    for cand in ranked:
        if any(overlaps(cand, old) for old in kept):
            continue
        kept.append(cand)
        if len(kept) >= limit:
            break
    kept.sort(key=lambda item: item['start'])
    return kept


def save_board(folder: Path, payload: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'anuclipz-board.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')


def run_analyze(job_id: str, body: dict):
    key = str(body.get('api_key') or '').strip()
    provider = str(body.get('provider') or '').strip().lower()
    try:
        if provider == 'cursor' or key.startswith('crsr_'):
            raise RuntimeError('A Cursor key cannot pick a clip. Choose Google AI Studio or ChatGPT.')
        if provider not in ('google', 'openai'):
            raise RuntimeError('Choose Google AI Studio or ChatGPT.')
        if len(key) < 10:
            raise RuntimeError('Paste the API key first.')
        url = youtube_url(str(body.get('url') or ''))
        query = re.sub(r'\s+', ' ', str(body.get('query') or '')).strip()[:200]
        default = 'gemini-flash-lite-latest' if provider == 'google' else 'gpt-4o-mini'
        model = model_name(str(body.get('model') or ''), default)
        update(job_id, progress=0.12, step='Opening the video', detail='A local tool is reading the link. This spends no tokens.')
        import yt_dlp
        with yt_dlp.YoutubeDL(ydl_opts(skip_download=True)) as ydl:
            info = ydl.extract_info(url, download=False)
        update(job_id, progress=0.34, step='Reading the words', detail='Pulling the caption track. Still no model call.')
        words = captions.from_info(info) or []
        if len(words) < 20:
            raise RuntimeError('This video has no usable YouTube captions. Clipper reads the words first.')
        rows = caption_rows(words)
        title = str(info.get('title') or 'Untitled')
        chunks = row_chunks(rows)
        update(
            job_id, progress=0.4, step='Reading the story',
            words_read=len(words), words_sent=sum(len(row['text'].split()) for row in rows),
            detail=f'The model will read {len(rows)} sentences in {len(chunks)} sections'
            + (f', looking only for “{query}”.' if query else ', and keep every full scene.'),
        )

        def on_tokens(extra, note):
            charge(job_id, provider, model, extra, note)

        pool: list[dict] = []
        for index, chunk in enumerate(chunks):
            update(job_id, progress=round(0.42 + (0.28 * (index + 1) / max(1, len(chunks))), 3), step='Reading the story', detail=f'Section {index + 1} of {len(chunks)}. The model can return nothing if this part is dull.')
            try:
                text = ask(provider, key, model, discover_prompt(title, chunk, query), lambda extra, note=f'section {index + 1}': on_tokens(extra, note), tokens=2400)
                pool.extend(pool_from_model(parse_json(text), rows))
            except Exception:
                continue
        if not query:
            for cand in shortlist(words, 8):
                if any(overlaps(cand, old) for old in pool):
                    continue
                pool.append({
                    'start': cand['start'],
                    'end': cand['end'],
                    'text': cand['text'],
                    'hook': '',
                    'title': '',
                    'why': 'Kept by the local word score in case the model skipped a loud moment.',
                    'heat': min(70, int(cand.get('score') or 0)),
                    'score': cand.get('score') or 0,
                })
        pool = keep_best(pool, 40)
        if not pool:
            raise RuntimeError('No spoken stretch was strong enough to be a short.')
        update(job_id, progress=0.74, step='Looking at the picture', detail='Frames are cut for every moment. The model only sees the strongest pictures.')
        moment_frames(info, pool)
        ranked = sorted(range(len(pool)), key=lambda index: int(pool[index].get('heat') or 0), reverse=True)[:8]
        shots = [(index + 1, raw) for index in ranked if (raw := thumb_bytes(pool[index]))]
        pictured = [number for number, _raw in shots]
        update(job_id, progress=0.84, step='Judging the finalists', detail='One last call can widen a scene so the setup and the reaction stay in.')
        try:
            judged = ask_vision(
                provider, key, model, judge_prompt(title, pool, len(pool), pictured),
                shots,
                lambda extra: on_tokens(extra, 'final judge'),
                tokens=2400,
            )
            paint(pool, parse_json(judged))
        except Exception:
            pass
        clips = take_clips({'clips': [{
            'start': cand['start'], 'end': cand['end'], 'hook': cand.get('hook') or '',
            'title': cand.get('title') or '', 'why': cand.get('why') or '', 'heat': cand.get('heat'),
        } for cand in pool]}, pool, words, 40)
        if not clips:
            raise RuntimeError('The model did not pick usable times. Try again, or pick a lighter model.')
        moment_frames(info, clips)
        thumb = info.get('thumbnail') or ''
        if not isinstance(thumb, str) or not thumb.startswith('https://'):
            thumb = ''
        card = source_card(info)
        tokens = snapshot(job_id).get('tokens') or blank_tokens()
        cost = float(snapshot(job_id).get('cost') or 0)
        update(
            job_id, status='done', progress=1, step='Ready', title=title[:180], thumbnail=thumb,
            clips=clips, source=card, error='',
            detail=f'{len(clips)} moments ready. This pass was about ${cost:.4f}. The running total stays on this computer.',
        )
    except HTTPException as exc:
        update(job_id, status='error', step='Stopped', error=str(exc.detail), detail=str(exc.detail))
    except Exception as exc:
        message = clean(str(exc), key) or 'The read failed.'
        update(job_id, status='error', step='Stopped', error=message, detail=message)


def ass_time(value: float) -> str:
    value = max(0.0, value)
    hours, minutes = int(value // 3600), int((value % 3600) // 60)
    return f'{hours}:{minutes:02d}:{value % 60:05.2f}'


def ass_escape(text: str) -> str:
    return re.sub(r'\s+', ' ', text.replace('\\', '').replace('{', '').replace('}', '')).strip()


def video_size(path: Path) -> tuple[int, int]:
    if not PROBE.is_file():
        return 0, 0
    proc = subprocess.run(
        [str(PROBE), '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0:s=x', str(path)],
        capture_output=True, text=True,
    )
    try:
        width, height = (int(part) for part in proc.stdout.strip().split('x'))
    except ValueError:
        return 0, 0
    return width, height


def reel_canvas(src_w: int, src_h: int) -> tuple[int, int, str]:
    src_w, src_h = src_w - (src_w % 2), src_h - (src_h % 2)
    if src_w >= 3800 or src_h >= 2100:
        reel_w, reel_h = 2160, 3840
        note = f'Source {src_w}×{src_h}. Rendering a 2160×3840 reel, the 4K vertical size, from that file. The picture is not dropped to 1080.'
    elif src_w >= 640:
        reel_w = src_w
        reel_h = reel_w * 16 // 9
        reel_h += reel_h % 2
        note = f'Source {src_w}×{src_h}. Rendering a {reel_w}×{reel_h} reel. The gameplay stays at that width.'
    else:
        reel_w, reel_h = 1080, 1920
        note = 'Rendering 1080×1920. The download did not report a larger picture.'
    return reel_w, reel_h, note


def type_places(layout: str, reel_w: int, reel_h: int, src_w: int, src_h: int) -> tuple[int, int, int, int]:
    hook_size = max(48, round(reel_w * 0.128))
    cap_size = max(36, round(reel_w * 0.077))
    if layout == 'blur':
        picture_top, picture_bottom = sharp_span(src_w, src_h, reel_w, reel_h)
        gap = max(8, round(reel_h * 0.028))
        hook_top = round(reel_h * 0.09)
        room = picture_top - gap - hook_top
        if room < hook_size:
            hook_size = max(36, room)
        lower = reel_h - (picture_bottom + gap) - round(reel_h * 0.05)
        if lower < cap_size:
            cap_size = max(28, lower)
        return hook_top, hook_size, picture_bottom + gap, cap_size
    hook_top = round(reel_h * 0.055)
    cap_top = max(hook_top + hook_size, reel_h - cap_size - round(reel_h * 0.05))
    return hook_top, hook_size, cap_top, cap_size


def region_frac(box) -> tuple[float, float, float, float]:
    if not isinstance(box, dict):
        box = {}
    try:
        x, y, w, h = float(box.get('x') or 0), float(box.get('y') or 0), float(box.get('w') or 1), float(box.get('h') or 0.5)
    except (TypeError, ValueError):
        x, y, w, h = 0, 0, 1, 0.5
    w, h = min(1.0, max(0.04, w)), min(1.0, max(0.04, h))
    x, y = min(max(0.0, x), 1 - w), min(max(0.0, y), 1 - h)
    return x, y, w, h


def plate_graph(layout: str, reel_w: int, reel_h: int, start_at: str, length: str, splits: list) -> str:
    head = f'[0:v]trim=start={start_at}:duration={length},setpts=PTS-STARTPTS'
    if layout == 'full':
        return head + f',scale={reel_w}:{reel_h}:force_original_aspect_ratio=increase:flags=lanczos,crop={reel_w}:{reel_h}[base]'
    if layout == 'split':
        band = reel_h // 2
        band -= band % 2
        top = region_frac(splits[0] if splits else {'x': 0, 'y': 0, 'w': 1, 'h': 0.45})
        bot = region_frac(splits[1] if len(splits) > 1 else {'x': 0, 'y': 0.55, 'w': 1, 'h': 0.45})
        def crop(box):
            x, y, w, h = box
            return f'crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},scale={reel_w}:{band}:force_original_aspect_ratio=increase:flags=lanczos,crop={reel_w}:{band}'
        return (
            head + ',split[sa][sb];'
            f'[sa]{crop(top)}[top];[sb]{crop(bot)}[bot];'
            f'[top][bot]vstack=inputs=2,scale={reel_w}:{reel_h}[base]'
        )
    return (
        head + ',split[fg][bg];'
        f'[bg]scale=1080:1920:force_original_aspect_ratio=increase:flags=bilinear,crop=1080:1920,boxblur=20:2,scale={reel_w}:{reel_h}:flags=bilinear[blur];'
        f'[fg]scale={reel_w}:-2:flags=lanczos[sharp];'
        '[blur][sharp]overlay=(W-w)/2:(H-h)/2:shortest=1[base]'
    )


def sharp_span(src_w: int, src_h: int, reel_w: int, reel_h: int) -> tuple[int, int]:
    if src_w < 2 or src_h < 2:
        src_w, src_h = 16, 9
    picture_h = int(round((reel_w * src_h / src_w) / 2.0) * 2)
    picture_h = min(max(2, picture_h), reel_h)
    top = max(0, (reel_h - picture_h) // 2)
    return top, top + picture_h


def write_ass(path: Path, hook: str, lines: list[dict], duration: float, reel_w: int, reel_h: int, src_w: int = 16, src_h: int = 9, layout: str = 'blur', show_hook: bool = True):
    hook = ass_escape(hook).upper()[:80]
    rows = []
    for line in lines:
        text = ass_escape(str(line.get('text') or '')).upper()[:140]
        if not text:
            continue
        try:
            start, end = float(line.get('start') or 0), float(line.get('end') or 0)
        except (TypeError, ValueError):
            continue
        rows.append([max(0.0, start), max(0.3, end), text])
    rows.sort(key=lambda row: row[0])
    for row in rows:
        row[1] = min(duration, max(row[1], row[0] + 0.2))
    side = round(reel_w * 0.06)
    hook_top, hook_size, cap_y, cap_size = type_places(layout, reel_w, reel_h, src_w, src_h)
    hook_edge = max(2, round(reel_w * 0.0042))
    cap_edge = max(2, round(reel_w * 0.003))
    slide = max(6, round(reel_w * 8 / 380))
    center = reel_w // 2
    events = []
    if show_hook and hook:
        hook_tag = f'{{\\fad(250,0)\\move({center},{hook_top + slide},{center},{hook_top},0,250)}}'
        events.append(f'Dialogue: 0,{ass_time(0)},{ass_time(duration)},Hook,,0,0,0,,{hook_tag}{hook}')
    cap_tag = f'{{\\fad(250,180)\\move({center},{cap_y + slide},{center},{cap_y},0,180)}}'
    events += [f'Dialogue: 0,{ass_time(a)},{ass_time(b)},Cap,,0,0,0,,{cap_tag}{text}' for a, b, text in rows]
    path.write_text('\n'.join([
        '[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {reel_w}', f'PlayResY: {reel_h}', 'WrapStyle: 0', 'ScaledBorderAndShadow: yes', '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
        f'Style: Hook,Anton,{hook_size},&H004AE1FF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{hook_edge},0,8,{side},{side},{hook_top},1',
        f'Style: Cap,Montserrat ExtraBold,{cap_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{cap_edge},0,8,{side},{side},{cap_y},1',
        '', '[Events]',
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
        *events, '',
    ]), encoding='utf-8')


def media_length(path: Path) -> float:
    if not PROBE.is_file():
        return 0
    proc = subprocess.run([str(PROBE), '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)], capture_output=True, text=True)
    try:
        return float(proc.stdout.strip() or 0)
    except ValueError:
        return 0


def has_audio(path: Path) -> bool:
    if not PROBE.is_file():
        return False
    proc = subprocess.run([str(PROBE), '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index', '-of', 'csv=p=0', str(path)], capture_output=True, text=True)
    return bool(proc.stdout.strip())


def page_text(text: str) -> str:
    return (str(text or '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;'))


def draw_type(folder: Path, hook: str, lines: list[dict], duration: float, reel_w: int, reel_h: int, src_w: int, src_h: int, layout: str = 'blur', show_hook: bool = True) -> Path | None:
    if not (FONTS / 'Anton-Regular.ttf').is_file() or not shutil.which('npx'):
        return None
    folder.mkdir(parents=True, exist_ok=True)
    for font in ('Anton-Regular.ttf', 'Montserrat-ExtraBold.ttf'):
        src = FONTS / font
        if src.is_file():
            shutil.copy2(src, folder / font)
    hook_top, hook_size, cap_top, cap_size = type_places(layout, reel_w, reel_h, src_w, src_h)
    groups = []
    for line in lines:
        text = re.sub(r'\s+', ' ', str(line.get('text') or '')).strip()
        if not text:
            continue
        try:
            start, end = float(line.get('start') or 0), float(line.get('end') or 0)
        except (TypeError, ValueError):
            continue
        groups.append((max(0.0, start), min(duration, max(start + 0.2, end)), text))
    groups.sort()
    for index, group in enumerate(groups[:-1]):
        groups[index] = (group[0], min(group[1], max(group[0] + 0.16, groups[index + 1][0] - 0.04)), group[2])
    blocks = []
    if show_hook and hook.strip():
        blocks.append(
            f'<h1 class="clip hook" data-start="0" data-duration="{duration:.3f}" data-track-index="0">{page_text(hook.upper()[:80])}</h1>'
        )
    for start, end, text in groups:
        blocks.append(
            f'<p class="clip cap" data-start="{start:.3f}" data-duration="{max(0.16, end - start):.3f}" data-track-index="1">{page_text(text.upper()[:140])}</p>'
        )
    if not blocks:
        return None
    html = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<style>
@font-face {{ font-family: Anton; src: url("Anton-Regular.ttf"); }}
@font-face {{ font-family: "Montserrat ExtraBold"; src: url("Montserrat-ExtraBold.ttf"); font-weight: 800; }}
html, body, #root {{ margin: 0; width: {reel_w}px; height: {reel_h}px; overflow: hidden; background: transparent; }}
#root {{ position: relative; }}
.hook, .cap {{ position: absolute; left: 6%; right: 6%; margin: 0; text-align: center; text-transform: uppercase; }}
.hook {{ top: {hook_top}px; color: #FFE14A; font-family: Anton, sans-serif; font-size: {hook_size}px; line-height: 0.92; font-weight: 400; text-shadow: 0 4px 0 #000, 0 0 3px #000; }}
.cap {{ top: {cap_top}px; color: #fff; font-family: "Montserrat ExtraBold", sans-serif; font-size: {cap_size}px; line-height: 1.02; font-weight: 800; text-shadow: 0 3px 0 #000, 0 0 3px #000; }}
</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{duration:.3f}" data-width="{reel_w}" data-height="{reel_h}">
{chr(10).join(blocks)}
</div>
</body>
</html>
'''
    (folder / 'index.html').write_text(html, encoding='utf-8')
    (folder / 'hyperframes.json').write_text('{"name":"anuclipz"}\n', encoding='utf-8')
    out = folder / 'type.webm'
    proc = subprocess.run(
        ['npx', '-y', 'hyperframes', 'render', '--format', 'webm', '--fps', '30', '--output', str(out)],
        cwd=folder, capture_output=True, text=True, timeout=max(180, int(duration * 12) + 60),
    )
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 500:
        return None
    return out


def compose(src: Path, pad: float, duration: float, hook: str, lines: list[dict], out: Path, overlay: Path | None = None, layout: str = 'blur', splits: list | None = None, show_hook: bool = True) -> str:
    if not RENDER.is_file():
        raise RuntimeError('Caption burn needs the ffmpeg in .tools. Run npm start once so AnuClipz can fetch it.')
    out.parent.mkdir(parents=True, exist_ok=True)
    src_w, src_h = video_size(src)
    reel_w, reel_h, quality = reel_canvas(src_w, src_h)
    use_type = overlay is not None and overlay.is_file() and overlay.stat().st_size > 500
    ass = out.with_suffix('.ass')
    start_at = f'{max(0.0, pad):.3f}'
    length = f'{max(0.3, duration):.3f}'
    plate = plate_graph(layout, reel_w, reel_h, start_at, length, splits or [])
    if use_type:
        graph = plate + ';[base][1:v]overlay=0:0:eof_action=pass:format=auto[v]'
        inputs = ['-i', str(src), '-c:v', 'libvpx-vp9', '-i', str(overlay)]
    elif (show_hook and hook.strip()) or lines:
        write_ass(ass, safe.censor(hook), [{**line, 'text': safe.censor(str(line.get('text') or ''))} for line in lines], duration, reel_w, reel_h, src_w, src_h, layout, show_hook)
        ass_arg = str(ass).replace('\\', '\\\\').replace(':', '\\:')
        fonts_arg = str(FONTS).replace('\\', '\\\\').replace(':', '\\:')
        graph = plate + f';[base]ass={ass_arg}:fontsdir={fonts_arg}[v]'
        inputs = ['-i', str(src)]
    else:
        graph = plate.replace('[base]', '[v]')
        inputs = ['-i', str(src)]
    cmd = [str(RENDER), '-y', '-hide_banner', '-loglevel', 'error', *inputs, '-filter_complex', graph, '-map', '[v]']
    if has_audio(src):
        graph += f';[0:a]atrim=start={start_at}:duration={length},asetpts=PTS-STARTPTS[a]'
        cmd = [str(RENDER), '-y', '-hide_banner', '-loglevel', 'error', *inputs, '-filter_complex', graph, '-map', '[v]', '-map', '[a]', '-c:a', 'aac', '-b:a', '192k']
    cmd += ['-c:v', 'libx264', '-preset', 'medium', '-crf', '15', '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(out)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        if use_type and (proc.returncode != 0 or not out.is_file()):
            return compose(src, pad, duration, hook, lines, out, None, layout, splits, show_hook)
    finally:
        ass.unlink(missing_ok=True)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(clean((proc.stderr or proc.stdout or 'ffmpeg could not cut that reel.')[-400:]) or 'ffmpeg could not cut that reel.')
    return quality + (' Encoded x264 CRF 15. HyperFrames set the type.' if use_type else ' Encoded x264 CRF 15.')


def clip_slug(title: str) -> str:
    text = re.sub(r'[^\w]+', '-', title, flags=re.UNICODE).strip('-')
    text = re.sub(r'-{2,}', '-', text)
    return text[:48] or 'reel'


def fit_tags(raw: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for piece in re.split(r'[,|\n]', str(raw or '')):
        tag = re.sub(r'\s+', ' ', piece).strip().lstrip('#').strip()
        tag = safe.censor(tag).replace('<', '').replace('>', '')
        key = tag.lower()
        if len(tag) < 2 or key in seen:
            continue
        trial = ','.join(parts + [tag])
        if len(trial) > 500:
            continue
        parts.append(tag)
        seen.add(key)
    return ','.join(parts)


def fill_tags(primary: str, extras: list[str]) -> str:
    tags = fit_tags(primary)
    parts = [part for part in tags.split(',') if part]
    seen = {part.lower() for part in parts}
    for extra in extras:
        tag = re.sub(r'\s+', ' ', str(extra or '')).strip().lstrip('#').strip()
        tag = safe.censor(tag).replace('<', '').replace('>', '')
        key = tag.lower()
        if len(tag) < 2 or key in seen:
            continue
        trial = ','.join(parts + [tag])
        if len(trial) > 500:
            continue
        parts.append(tag)
        seen.add(key)
        if len(','.join(parts)) >= 480:
            break
    return ','.join(parts)


def source_card(info: dict) -> dict:
    raw_tags = info.get('tags') if isinstance(info.get('tags'), list) else []
    description = re.sub(r'\s+', ' ', str(info.get('description') or '')).strip()
    categories = info.get('categories') if isinstance(info.get('categories'), list) else []
    return {
        'title': safe.censor(str(info.get('title') or '')[:180]),
        'channel': safe.censor(str(info.get('channel') or info.get('uploader') or '')[:80]),
        'description': safe.censor(description[:800]),
        'tags': [safe.censor(str(tag).strip()[:48]) for tag in raw_tags[:40] if str(tag).strip()],
        'categories': [safe.censor(str(item)[:40]) for item in categories[:4] if str(item).strip()],
    }


def tag_extras(source: dict, title: str, hook: str) -> list[str]:
    skip = {'this', 'that', 'with', 'from', 'have', 'your', 'just', 'what', 'when', 'where', 'here', 'there', 'they', 'them', 'then', 'than', 'into', 'about', 'some', 'long', 'right', 'now'}
    texts = [title, hook, str(source.get('channel') or ''), str(source.get('title') or ''), str(source.get('description') or '')]
    raw_tags = source.get('tags') if isinstance(source.get('tags'), list) else []
    texts.extend(str(tag) for tag in raw_tags)
    raw_categories = source.get('categories') if isinstance(source.get('categories'), list) else []
    texts.extend(str(item) for item in raw_categories)
    extras: list[str] = []
    for text in texts:
        words = [word for word in re.findall(r"[A-Za-z0-9']+", text) if len(word) > 2 and word.lower() not in skip]
        if 2 <= len(text) <= 55:
            extras.append(text)
        for size in (3, 2):
            for index in range(0, max(0, len(words) - size + 1)):
                extras.append(' '.join(words[index:index + size]))
        for word in words:
            if len(word) >= 5:
                extras.append(word)
    channel = str(source.get('channel') or '').strip()
    if channel:
        extras.append(f'{channel} shorts')
    extras.extend(['youtube shorts', 'funny moments', 'viral clip'])
    return extras


def fallback_pack(title: str, hook: str, film: str, source: dict | None = None) -> dict:
    seeds = [title, hook, film, 'funny moment', 'shorts', 'youtube shorts']
    return {'tags': fill_tags(','.join(seeds), tag_extras(source or {}, title, hook))}


def pack_prompt(film: str, clips: list[dict], source: dict) -> str:
    rows = []
    for index, clip in enumerate(clips):
        spoken = ' | '.join(str(line.get('text') or '') for line in (clip.get('lines') or [])[:18])[:700]
        rows.append(f"{index}. clip title={clip.get('title')} | hook={clip.get('hook')} | why={str(clip.get('why') or '')[:240]} | spoken={spoken}")
    origin_tags = ', '.join(source.get('tags') or [])[:400]
    return (
        'You package one YouTube Short the way a large channel would, without lying.\n'
        'Return JSON only: {"packs":[{"index":0,"tags":"tag one,tag two","youtube_title":"","youtube":"","instagram":"","tiktok":""}]}\n'
        'tags is one string of search phrases, biggest and most exact first. No hash sign. Separate tags with commas and no spaces after the commas.\n'
        'The tags string must be at least 470 characters and at most 500, including the commas. Do not stop early.\n'
        'Order the tags: the exact phrase a stranger would type for this moment, close wordings, common misspellings, the channel name, phrases from the source title, source tags that still match this clip, then broader searches.\n'
        'youtube_title is at most 70 characters. The first words are the specific shock in this clip. No hashtags. No "you won\'t believe". A person scrolling stops because the title is exact, not because it shouts.\n'
        'youtube is the description, 700 to 1100 characters. The first line is under 120 characters and is the whole reason to tap, because a phone cuts the rest. Then a blank line. Then what is happening, who is involved, and the turn, and stop before the last second so the video still has a payoff. Then a blank line. Credit the channel name and the source title in one line. Then a blank line and 4 to 6 hashtags from this moment.\n'
        'instagram is the hook, a blank line, two short lines about the moment, a blank line, then the same hashtags.\n'
        'tiktok is one punch line, a blank line, one line of what happens, a blank line, then the hashtags.\n'
        'Write every field in English. Use only people, places, and events that are in the spoken lines or the source metadata. Do not invent a celebrity.\n'
        'If a word is sexual or a slur, write it already masked, like p*rn.\n'
        f"Channel: {source.get('channel') or 'unknown'}\n"
        f"Source title: {source.get('title') or film[:160]}\n"
        f"Source description: {source.get('description') or ''}\n"
        f"Source tags: {origin_tags}\n"
        + '\n'.join(rows)
    )


def read_packs(provider: str, key: str, model: str, film: str, clips: list[dict], source: dict, on_tokens) -> list[dict]:
    raw = parse_json(ask(provider, key, model, pack_prompt(film, clips, source), on_tokens, tokens=3200, temperature=0.5))
    found = raw.get('packs') if isinstance(raw, dict) else None
    return found if isinstance(found, list) else []


def unique_pack(folder: Path, stem: str) -> Path:
    candidate, number = stem, 2
    while (folder / candidate).exists():
        candidate = f'{stem}-{number}'
        number += 1
    dest = folder / candidate
    dest.mkdir(parents=True)
    return dest


def prepare_folder(raw: str) -> Path:
    folder = Path(raw).expanduser() if raw.strip() else Path.home() / 'Downloads' / 'AnuClipz'
    folder = folder.resolve()
    if folder == Path(folder.anchor):
        raise RuntimeError('Pick a folder, not the whole disk.')
    folder.mkdir(parents=True, exist_ok=True)
    if not os.access(folder, os.W_OK):
        raise RuntimeError('AnuClipz cannot write in that folder.')
    return folder


def hashtags(film: str, title: str) -> str:
    blob = f'{film} {title}'.lower()
    tags: list[str] = []
    if 'gta' in blob:
        tags.extend(['#GTA6', '#GTA', '#GTAVI'])
    stop = {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'right', 'now', 'full', 'video', 'beating'}
    for word in re.findall(r'[A-Za-z][A-Za-z0-9]{2,}', film):
        if word.lower() in stop:
            continue
        tag = '#' + re.sub(r'[^A-Za-z0-9]', '', word)
        if tag.lower() not in {item.lower() for item in tags}:
            tags.append(tag)
        if len(tags) >= 4:
            break
    for extra in ('#shorts', '#reels', '#fyp'):
        if extra.lower() not in {item.lower() for item in tags}:
            tags.append(extra)
    return ' '.join(tags[:6])


def clip_field(text: str, limit: int) -> str:
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(' ', 1)[0].strip()
    return cut or text[:limit].rstrip()


def attract(title: str, hook: str, why: str, film: str, spoken: str = '', channel: str = '', chosen: dict | None = None) -> dict:
    chosen = chosen or {}
    title = clip_field(title, 90) or 'Watch this'
    hook = clip_field(hook, 80) or title
    why = re.sub(r'\s+', ' ', why).strip()
    spoken = re.sub(r'\s+', ' ', spoken).strip()
    channel = channel.strip()
    credit = f'{channel} · {film}'.strip(' ·') if film.strip() or channel else ''
    tags = hashtags(film, title)
    youtube_title = clip_field(str(chosen.get('youtube_title') or ''), 70) or clip_field(title, 70)
    youtube = str(chosen.get('youtube') or '').strip()
    instagram = str(chosen.get('instagram') or '').strip()
    tiktok = str(chosen.get('tiktok') or '').strip()
    if len(youtube) < 180:
        lead = hook if hook.lower() != title.lower() else title
        body = why or spoken[:280] or 'The next second is the one people replay.'
        heard = f'\n\nHe says: {spoken[:180]}' if spoken else ''
        source = f'\n\n{credit}' if credit else ''
        youtube = f'{lead}\n\n{body}{heard}\n\nThe last second is the one worth staying for.{source}\n\n{tags}'
    if len(instagram) < 40:
        instagram = f'{hook}\n\n{why or spoken[:180] or "Watch it to the last second."}\n\n{tags}'
    if len(tiktok) < 20:
        tiktok = f'{hook}\n\n{clip_field(why or spoken, 140)}\n\n{tags}'
    return {
        'title': safe.censor(title),
        'instagram': safe.censor(instagram[:1800]),
        'youtube_title': safe.censor(youtube_title),
        'youtube': safe.censor(youtube[:1800]),
        'tiktok': safe.censor(tiktok[:900]),
    }


def write_meta(path: Path, copy: dict, video: Path):
    path.write_text(
        f"YOUTUBE TITLE\n{copy['youtube_title']}\n\n"
        f"YOUTUBE DESCRIPTION\n{copy['youtube']}\n\n"
        f"YOUTUBE TAGS\n{copy.get('tags') or ''}\n\n"
        f"INSTAGRAM\n{copy['instagram']}\n\n"
        f"TIKTOK\n{copy['tiktok']}\n\n"
        f"TITLE\n{copy['title']}\n\n"
        f"FILE\n{video}\n",
        encoding='utf-8',
    )


def clip_list(body: dict) -> list[dict]:
    raw = body.get('clips')
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    return [{
        'start': body.get('start'),
        'end': body.get('end'),
        'hook': body.get('hook'),
        'lines': body.get('lines') or [],
        'title': body.get('title') or '',
    }]


def run_cut(job_id: str, body: dict):
    scratches: list[Path] = []
    try:
        url = youtube_url(str(body.get('url') or ''))
        clips = clip_list(body)
        if not clips:
            raise RuntimeError('Pick at least one moment.')
        import yt_dlp
        from yt_dlp.utils import download_range_func
        folder = prepare_folder(str(body.get('export_dir') or ''))
        film = str(body.get('film') or '')
        provider = str(body.get('provider') or '').strip().lower()
        model = str(body.get('model') or '').strip()
        key = str(body.get('api_key') or '').strip()
        source = body.get('source') if isinstance(body.get('source'), dict) else {}
        layout = str(body.get('layout') or 'blur')
        if layout not in ('blur', 'full', 'split'):
            layout = 'blur'
        splits = body.get('splits') if isinstance(body.get('splits'), list) else []
        if not str(source.get('channel') or source.get('description') or '').strip():
            update(job_id, progress=0.04, step='Reading the source', detail='Title, description, channel, and the original tags.')
            try:
                with yt_dlp.YoutubeDL(ydl_opts(skip_download=True)) as ydl:
                    source = source_card(ydl.extract_info(url, download=False))
            except Exception:
                source = source or {'title': film}
        files = []
        total = len(clips)
        for index, clip in enumerate(clips):
            start, end = float(clip.get('start')), float(clip.get('end'))
            duration = end - start
            if duration < SCENE_MIN or duration > SCENE_MAX:
                raise RuntimeError('Keep each scene between 12 and 80 seconds.')
            hook = str(clip.get('hook') or 'Watch this')[:80]
            title = str(clip.get('title') or hook)[:120]
            lines = [line for line in (clip.get('lines') or [])[:90] if isinstance(line, dict)]
            hook = safe.censor(hook)
            title = safe.censor(title)
            lines = [{**line, 'text': safe.censor(str(line.get('text') or ''))} for line in lines]
            show_captions = clip.get('captions') is not False
            show_title = clip.get('show_title') is not False
            burned = lines if show_captions else []
            burn_hook = hook if show_title else ''
            lead = 2.0 if start >= 2 else max(0.0, start)
            scratch = Path(tempfile.mkdtemp(prefix='anuclipz-part-'))
            scratches.append(scratch)
            part_dir = scratch
            span = 1 / total
            base = index * span
            last_tick = {'at': 0.0}

            def on_progress(item, base=base, span=span, index=index):
                if item.get('status') != 'downloading':
                    return
                now = time.time()
                if now - last_tick['at'] < 0.35:
                    return
                last_tick['at'] = now
                got = item.get('downloaded_bytes') or 0
                whole = item.get('total_bytes') or item.get('total_bytes_estimate') or 0
                frac = min(0.85, got / whole) if whole else 0.15
                info = item.get('info_dict') or {}
                width, height = info.get('width') or 0, info.get('height') or 0
                source = f'{width}×{height}' if width and height else 'the highest stream'
                update(
                    job_id, progress=round(base + span * 0.7 * frac, 3),
                    step=f'Downloading {index + 1} of {total}',
                    detail=f'{source} · {got / 1e6:.0f} MB. The reel is cut from this, not a smaller copy.',
                )

            update(job_id, progress=round(base + 0.02, 3), step=f'Downloading {index + 1} of {total}', detail='Taking the highest picture YouTube has for this stretch.')
            with yt_dlp.YoutubeDL(ydl_opts(
                format='bv*+ba[format_note*=original]/bv*+ba/b',
                format_sort=['lang', 'res', 'br'],
                merge_output_format='mp4',
                outtmpl=str(part_dir / 'source.%(ext)s'),
                download_ranges=download_range_func(None, [(max(0.0, start - lead), end + 0.4)]),
                force_keyframes_at_cuts=True,
                progress_hooks=[on_progress],
                noprogress=False,
            )) as ydl:
                ydl.download([url])
            matches = sorted(part_dir.glob('source.*'))
            if not matches:
                raise RuntimeError('The piece did not download.')
            src = matches[0]
            got = media_length(src)
            pad = max(0.0, got - duration - 0.4)
            src_w, src_h = video_size(src)
            reel_w, reel_h, quality = reel_canvas(src_w, src_h)
            update(job_id, progress=round(base + span * 0.72, 3), step=f'Drawing the type {index + 1} of {total}', detail='HyperFrames is setting the hook and the captions.' if show_captions else 'HyperFrames is setting the hook. Captions stay off for this one.')
            overlay = None
            try:
                overlay = draw_type(scratch / 'frames', burn_hook, burned, duration, reel_w, reel_h, src_w, src_h, layout, show_title)
            except (OSError, subprocess.TimeoutExpired):
                overlay = None
            update(job_id, progress=round(base + span * 0.8, 3), step=f'Rendering {index + 1} of {total}', detail=quality, quality=quality)
            out = scratch / 'reel.mp4'
            quality = compose(src, pad, duration, burn_hook, burned, out, overlay, layout, splits, show_title)
            packs: list[dict] = []
            if provider in ('google', 'openai') and model and len(key) >= 10:
                update(job_id, progress=round(base + span * 0.9, 3), step=f'Packing {index + 1} of {total}', detail='The model is writing the title, the description, and the tags.')
                try:
                    def take_tokens(extra, job_id=job_id):
                        charge(job_id, provider, model, extra, 'title, description, and tags')
                    packs = read_packs(provider, key, model, film, [{
                        'title': title, 'hook': hook, 'why': str(clip.get('why') or ''), 'lines': lines,
                    }], source, take_tokens)
                except Exception:
                    packs = []
            chosen = packs[0] if packs and isinstance(packs[0], dict) else {}
            tags = fill_tags(str(chosen.get('tags') or ''), tag_extras(source, title, hook)) or fallback_pack(title, hook, film, source)['tags']
            spoken = ' '.join(str(line.get('text') or '') for line in lines)[:400]
            copy = attract(title, hook, str(clip.get('why') or ''), film, spoken, str(source.get('channel') or ''), chosen)
            copy['tags'] = tags
            pack = unique_pack(folder, clip_slug(copy['title']))
            dest = pack / 'video.mp4'
            meta = pack / 'meta.txt'
            shutil.move(out, dest)
            write_meta(meta, copy, dest)
            item = saved_reel(folder, pack.name, dest, meta, None, 'video')
            item['quality'] = quality
            item['start'] = start
            files.append(item)
            update(job_id, files=files, file=files[0]['file'], quality=quality, progress=round(base + span, 3))
        update(job_id, status='done', progress=1, step='Reels ready', files=files, file=files[0]['file'], quality=files[0]['quality'], detail=files[0]['quality'], error='')
    except Exception as exc:
        message = clean(str(exc)) or 'The cut failed.'
        update(job_id, status='error', step='Stopped', error=message, detail=message)
    finally:
        for scratch in scratches:
            shutil.rmtree(scratch, ignore_errors=True)


@app.get('/api/folder')
def folder_default():
    folder = prepare_folder('')
    return {'path': str(folder)}


def choose_directory() -> str:
    if sys.platform == 'darwin':
        proc = subprocess.run(
            ['osascript', '-e', 'POSIX path of (choose folder with prompt "Save AnuClipz reels here")'],
            capture_output=True, text=True, timeout=180,
        )
        return '' if proc.returncode != 0 else proc.stdout.strip()
    if os.name == 'nt':
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$dialog.Description = 'Save AnuClipz reels here'; "
            "$dialog.UseDescriptionForTitle = $true; "
            "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.SelectedPath }"
        )
        proc = subprocess.run(
            ['powershell', '-NoProfile', '-STA', '-Command', script],
            capture_output=True, text=True, timeout=180,
        )
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return '' if proc.returncode != 0 or not lines else lines[-1]
    if shutil.which('zenity'):
        proc = subprocess.run(
            ['zenity', '--file-selection', '--directory', '--title=Save AnuClipz reels here'],
            capture_output=True, text=True, timeout=180,
        )
        return '' if proc.returncode != 0 else proc.stdout.strip()
    if shutil.which('kdialog'):
        proc = subprocess.run(
            ['kdialog', '--getexistingdirectory', str(Path.home()), '--title', 'Save AnuClipz reels here'],
            capture_output=True, text=True, timeout=180,
        )
        return '' if proc.returncode != 0 else proc.stdout.strip()
    raise RuntimeError('This system has no folder window. Install zenity, or type the folder another way.')


@app.post('/api/folder')
def folder_pick():
    try:
        chosen = choose_directory()
    except subprocess.TimeoutExpired:
        fail('The folder window timed out.')
    except FileNotFoundError:
        fail('The folder window could not open on this system.')
    except RuntimeError as exc:
        fail(str(exc))
    if not chosen:
        return {'path': ''}
    try:
        folder = prepare_folder(chosen)
    except Exception as exc:
        fail(clean(str(exc)) or 'That folder cannot be used.')
    return {'path': str(folder)}


@app.get('/api/spend')
def spend():
    data = read_spend()
    return {
        'usd': float(data.get('usd') or 0),
        'prompt': int(data.get('prompt') or 0),
        'output': int(data.get('output') or 0),
        'thinking': int(data.get('thinking') or 0),
        'rate': str(data.get('rate') or 'Rough list price'),
    }


def parse_meta(text: str) -> dict:
    matches = list(re.finditer(
        r'^(TITLE|INSTAGRAM|YOUTUBE TITLE|YOUTUBE DESCRIPTION|TIKTOK|YOUTUBE TAGS|THUMBNAIL|FILE)\n',
        text,
        re.M,
    ))
    found = {}
    for index, match in enumerate(matches):
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        found[match.group(1)] = text[match.end():stop].strip()
    return found


def path_inside(folder: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def saved_reel(folder: Path, name: str, video: Path, meta_path: Path, thumb: Path | None, kind: str) -> dict:
    meta = parse_meta(meta_path.read_text(encoding='utf-8')) if meta_path.is_file() else {}
    quoted_dir = urllib.parse.quote(str(folder))
    quoted_name = urllib.parse.quote(name)
    poster = ''
    if thumb and thumb.is_file():
        poster = f'/api/saved-file?dir={quoted_dir}&name={quoted_name}&kind=thumb'
    return {
        'file': f'/api/saved-file?dir={quoted_dir}&name={quoted_name}&kind={kind}',
        'poster': poster,
        'title': meta.get('TITLE') or name,
        'path': str(video),
        'folder': str(video.parent),
        'meta_path': str(meta_path) if meta_path.is_file() else '',
        'thumb_path': str(thumb) if thumb and thumb.is_file() else '',
        'tags': meta.get('YOUTUBE TAGS') or '',
        'instagram': meta.get('INSTAGRAM') or '',
        'youtube_title': meta.get('YOUTUBE TITLE') or '',
        'youtube': meta.get('YOUTUBE DESCRIPTION') or '',
        'tiktok': meta.get('TIKTOK') or '',
    }


def saved_items(folder: Path) -> list[dict]:
    items = []
    if not folder.is_dir():
        return items
    packs = [path for path in folder.iterdir() if path.is_dir() and (path / 'video.mp4').is_file()]
    packs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for child in packs:
        thumb = child / 'thumbnail.png'
        items.append(saved_reel(folder, child.name, child / 'video.mp4', child / 'meta.txt', thumb, 'video'))
    loose = sorted((path for path in folder.glob('*.mp4') if path.is_file()), key=lambda path: path.stat().st_mtime, reverse=True)
    for video in loose:
        items.append(saved_reel(folder, video.stem, video, folder / f'{video.stem}-meta.txt', None, 'loose'))
    return items


def read_board(folder: Path) -> dict | None:
    path = folder / 'anuclipz-board.json'
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('clips'), list):
        return None
    return {
        'url': str(data.get('url') or ''),
        'title': str(data.get('title') or ''),
        'thumbnail': str(data.get('thumbnail') or ''),
        'clips': data['clips'][:8],
        'source': data.get('source') if isinstance(data.get('source'), dict) else {},
    }


@app.get('/api/saved')
def saved(dir: str = ''):
    try:
        folder = prepare_folder(dir)
    except Exception as exc:
        fail(clean(str(exc)) or 'That folder cannot be used.')
    (folder / 'anuclipz-board.json').unlink(missing_ok=True)
    return {'files': saved_items(folder)}


@app.get('/api/saved-file')
def saved_file(dir: str, name: str, kind: str):
    try:
        folder = prepare_folder(dir)
    except Exception as exc:
        fail(clean(str(exc)) or 'That folder cannot be used.')
    safe_name = Path(name).name
    if not safe_name or safe_name != name or safe_name in {'.', '..'}:
        fail('Not found.', 404)
    if kind == 'loose':
        path, media = folder / f'{safe_name}.mp4', 'video/mp4'
    elif kind == 'video':
        path, media = folder / safe_name / 'video.mp4', 'video/mp4'
    elif kind == 'thumb':
        path, media = folder / safe_name / 'thumbnail.png', 'image/png'
    else:
        fail('Not found.', 404)
    if not path.is_file() or not path_inside(folder, path):
        fail('Not found.', 404)
    return FileResponse(path, media_type=media)


@app.get('/api/preview')
def preview(url: str, start: float, end: float):
    page = youtube_url(url)
    try:
        proc, first = start_preview(page, start, end)
    except Exception as exc:
        message = re.sub(r'https?://\S+', '', clean(str(exc))).strip()[:240]
        fail(message or 'That part could not be opened.', 502)

    def chunks():
        try:
            yield first
            while True:
                block = proc.stdout.read(64 * 1024) if proc.stdout else b''
                if not block:
                    break
                yield block
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)

    return StreamingResponse(chunks(), media_type='video/mp4', headers={'Cache-Control': 'no-store', 'Accept-Ranges': 'none'})


@app.get('/api/frame')
def frame(url: str, at: float | None = None):
    page = youtube_url(url)
    try:
        path, point, duration = grab_frame(page, at)
    except Exception as exc:
        message = re.sub(r'https?://\S+', '', clean(str(exc))).strip()[:240]
        fail(message or 'That frame could not be opened.', 502)
    return FileResponse(path, media_type='image/jpeg', headers={
        'Cache-Control': 'private, max-age=3600',
        'X-Duration': f'{duration:.3f}',
        'X-At': f'{point:.3f}',
    })


@app.get('/api/health')
def health():
    return {'ok': True, 'name': 'clipper', 'can_cut': RENDER.is_file()}


@app.post('/api/models')
def models(body: dict):
    key = str(body.get('api_key') or '').strip()
    provider = str(body.get('provider') or '').strip().lower()
    try:
        if provider == 'cursor' or key.startswith('crsr_'):
            fail('A Cursor key cannot list chat models. Choose Google AI Studio or ChatGPT.')
        if provider not in ('google', 'openai'):
            fail('Choose Google AI Studio or ChatGPT.')
        if len(key) < 10:
            fail('Paste the API key first.')
        found = google_models(key) if provider == 'google' else openai_models(key)
        if not found:
            fail('This key returned no text models.')
        suggested = next((item['id'] for item in found if item['id'] in CHEAP_FIRST), found[0]['id'])
        return {'models': found, 'recommended': suggested}
    except HTTPException:
        raise
    except Exception as exc:
        fail(clean(str(exc), key) or 'Could not load models.')


@app.post('/api/jobs')
def start_job(body: dict):
    kind = str(body.get('kind') or 'analyze')
    job_id = new_job()
    target = run_cut if kind == 'cut' else run_analyze
    threading.Thread(target=target, args=(job_id, body), daemon=True).start()
    return {'id': job_id}


@app.get('/api/jobs/{job_id}')
def read_job(job_id: str):
    job = snapshot(job_id)
    if not job:
        fail('That job is gone.', 404)
    return job


@app.get('/api/thumb/{name}')
def thumb(name: str):
    if not re.fullmatch(r'[a-f0-9]{32}\.jpg', name):
        fail('Not found.', 404)
    path = SCRATCH / 'thumbs' / name
    if not path.is_file():
        fail('That frame is gone.', 404)
    return FileResponse(path, media_type='image/jpeg')


@app.get('/api/poster/{name}')
def poster(name: str):
    if not re.fullmatch(r'[a-f0-9]{32}\.png', name):
        fail('Not found.', 404)
    path = DATA / 'reels' / name
    if not path.is_file():
        fail('That thumbnail is gone.', 404)
    return FileResponse(path, media_type='image/png')


@app.get('/api/media/{name}')
def media(name: str):
    if not re.fullmatch(r'[a-f0-9]{32}\.mp4', name):
        fail('Not found.', 404)
    path = DATA / 'reels' / name
    if not path.is_file():
        fail('That reel is gone.', 404)
    return FileResponse(path, media_type='video/mp4', filename='clipper.mp4')


if __name__ == '__main__':
    import uvicorn
    print('Clipper engine is on http://127.0.0.1:8781', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=8781, log_level='info')
