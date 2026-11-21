"""Clipper engine. Local tools do the heavy work. The model only picks the cut."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import captions
import safe

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
TOOLS = ROOT / '.tools'
FONTS = ROOT / 'fonts'
RENDER = TOOLS / 'ffmpeg'
PROBE = TOOLS / 'ffprobe'
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


def ydl_opts(**extra):
    opts = {
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 60,
        'retries': 2,
        'noprogress': True,
    }
    if os.path.isdir('/opt/homebrew/bin'):
        opts['ffmpeg_location'] = '/opt/homebrew/bin'
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


def ask(provider: str, key: str, model: str, prompt: str, on_tokens) -> str:
    if provider == 'google':
        data = post_json(
            f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
            {
                'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
                'generationConfig': {'temperature': 0.3, 'maxOutputTokens': 1600},
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
            'temperature': 0.3,
            'max_tokens': 1600,
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


def as_score(value) -> int | None:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(99, number))


def nearest(cands: list[dict], start: float, end: float) -> dict | None:
    best, best_d = None, 1e9
    for cand in cands:
        dist = abs(cand['start'] - start) + abs(cand['end'] - end)
        if dist < best_d:
            best, best_d = cand, dist
    if best is None or best_d > 8:
        return None
    return best


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
        lines = caption_lines(words, cand['start'], cand['end']) or [{
            'start': 0, 'end': round(cand['end'] - cand['start'], 2), 'text': cand['text'][:120],
        }]
        found.append({
            'start': cand['start'],
            'end': cand['end'],
            'hook': safe.censor(' '.join(hook.split()[:8])[:80]),
            'title': safe.censor(title[:90]),
            'why': safe.censor(why),
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
    folder = DATA / 'thumbs'
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
        try:
            count = int(body.get('count') or 4)
        except (TypeError, ValueError):
            count = 4
        count = max(1, min(6, count))
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
        limit = min(8, max(count + 2, 6))
        cands = shortlist(words, limit)
        if not cands:
            raise RuntimeError('No spoken stretch was long enough to be a short.')
        sent = sum(len(item['text'].split()) for item in cands)
        update(
            job_id, progress=0.52, step='Keeping the strong moments',
            words_read=len(words), words_sent=sent,
            detail=f'Local tools read {len(words)} words and kept {len(cands)} moments ({sent} words) for the model.',
        )
        count = min(count, len(cands))
        title = str(info.get('title') or 'Untitled')
        prompt = prompt_for(title, cands, count)

        def on_tokens(tokens):
            update(job_id, progress=0.78, step='The model is choosing the cuts', tokens=tokens, detail='Tokens are arriving from the model.')

        update(job_id, progress=0.64, step='Asking the model', detail=f'{model} is reading {sent} words from the short list. The rest of the video stays on this computer.')
        text = ask(provider, key, model, prompt, on_tokens)
        clips = take_clips(parse_json(text), cands, words, count)
        if not clips:
            raise RuntimeError('The model did not pick usable times. Try again, or pick a lighter model.')
        update(job_id, progress=0.9, step='Cutting a frame for each moment', detail='Each preview gets the picture from its own moment, not the cover.')
        moment_frames(info, clips)
        thumb = info.get('thumbnail') or ''
        if not isinstance(thumb, str) or not thumb.startswith('https://'):
            thumb = ''
        tokens = snapshot(job_id).get('tokens') or blank_tokens()
        update(
            job_id, status='done', progress=1, step='Ready', title=title[:180], thumbnail=thumb,
            clips=clips, error='',
            detail=f'{len(clips)} moments ready. The model used {tokens.get("total", 0)} tokens.',
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


def sharp_span(src_w: int, src_h: int, reel_w: int, reel_h: int) -> tuple[int, int]:
    if src_w < 2 or src_h < 2:
        src_w, src_h = 16, 9
    picture_h = int(round((reel_w * src_h / src_w) / 2.0) * 2)
    picture_h = min(max(2, picture_h), reel_h)
    top = max(0, (reel_h - picture_h) // 2)
    return top, top + picture_h


def write_ass(path: Path, hook: str, lines: list[dict], duration: float, reel_w: int, reel_h: int, src_w: int = 16, src_h: int = 9):
    hook = ass_escape(hook).upper()[:80] or 'WATCH THIS'
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
    hook_size = max(48, round(reel_w * 0.128))
    cap_size = max(36, round(reel_w * 0.077))
    hook_edge = max(2, round(reel_w * 0.0042))
    cap_edge = max(2, round(reel_w * 0.003))
    side = round(reel_w * 0.06)
    picture_top, picture_bottom = sharp_span(src_w, src_h, reel_w, reel_h)
    gap = max(cap_edge * 2, round(reel_h * 0.028))
    hook_top = round(reel_h * 0.09)
    hook_room = picture_top - gap - hook_top
    if hook_room < hook_size:
        hook_size = max(36, hook_room)
    lower_room = reel_h - (picture_bottom + gap) - round(reel_h * 0.05)
    if lower_room < cap_size:
        cap_size = max(28, lower_room)
    slide = max(6, round(reel_w * 8 / 380))
    center = reel_w // 2
    hook_y = hook_top
    cap_y = picture_bottom + gap
    hook_tag = f'{{\\clip(0,0,{reel_w},{max(0, picture_top - gap)})\\fad(250,0)\\move({center},{hook_y + slide},{center},{hook_y},0,250)}}'
    cap_tag = f'{{\\clip(0,{picture_bottom},{reel_w},{reel_h})\\fad(250,180)\\move({center},{cap_y + slide},{center},{cap_y},0,180)}}'
    events = [f'Dialogue: 0,{ass_time(0)},{ass_time(duration)},Hook,,0,0,0,,{hook_tag}{hook}']
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


def compose(src: Path, pad: float, duration: float, hook: str, lines: list[dict], out: Path) -> str:
    if not RENDER.is_file():
        raise RuntimeError('Caption burn needs .tools/ffmpeg. Run npm start once so Clipper can fetch it.')
    out.parent.mkdir(parents=True, exist_ok=True)
    src_w, src_h = video_size(src)
    reel_w, reel_h, quality = reel_canvas(src_w, src_h)
    ass = out.with_suffix('.ass')
    write_ass(ass, safe.censor(hook), [{**line, 'text': safe.censor(str(line.get('text') or ''))} for line in lines], duration, reel_w, reel_h, src_w, src_h)
    ass_arg = str(ass).replace('\\', '\\\\').replace(':', '\\:')
    fonts_arg = str(FONTS).replace('\\', '\\\\').replace(':', '\\:')
    start_at = f'{max(0.0, pad):.3f}'
    length = f'{max(0.3, duration):.3f}'
    graph = (
        f'[0:v]trim=start={start_at}:duration={length},setpts=PTS-STARTPTS,split[fg][bg];'
        f'[bg]scale=1080:1920:force_original_aspect_ratio=increase:flags=bilinear,crop=1080:1920,boxblur=20:2,scale={reel_w}:{reel_h}:flags=bilinear[blur];'
        f'[fg]scale={reel_w}:-2:flags=lanczos[sharp];'
        '[blur][sharp]overlay=(W-w)/2:(H-h)/2:shortest=1[base];'
        f'[base]ass={ass_arg}:fontsdir={fonts_arg}[v]'
    )
    cmd = [str(RENDER), '-y', '-hide_banner', '-loglevel', 'error', '-i', str(src), '-filter_complex', graph, '-map', '[v]']
    if has_audio(src):
        graph += f';[0:a]atrim=start={start_at}:duration={length},asetpts=PTS-STARTPTS[a]'
        cmd = [str(RENDER), '-y', '-hide_banner', '-loglevel', 'error', '-i', str(src), '-filter_complex', graph, '-map', '[v]', '-map', '[a]', '-c:a', 'aac', '-b:a', '192k']
    cmd += ['-c:v', 'libx264', '-preset', 'medium', '-crf', '15', '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(out)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    finally:
        ass.unlink(missing_ok=True)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(clean((proc.stderr or proc.stdout or 'ffmpeg could not cut that reel.')[-400:]) or 'ffmpeg could not cut that reel.')
    return quality + ' Encoded x264 CRF 15.'


def clip_slug(title: str) -> str:
    text = re.sub(r'[^\w]+', '-', title, flags=re.UNICODE).strip('-')
    text = re.sub(r'-{2,}', '-', text)
    return text[:48] or 'reel'


def unique_stem(folder: Path, stem: str) -> str:
    candidate, number = stem, 2
    while (folder / f'{candidate}.mp4').exists() or (folder / f'{candidate}-meta.txt').exists():
        candidate = f'{stem}-{number}'
        number += 1
    return candidate


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


def attract(title: str, hook: str, why: str, film: str) -> dict:
    title = re.sub(r'\s+', ' ', title).strip() or 'Watch this'
    hook = re.sub(r'\s+', ' ', hook).strip() or title
    why = re.sub(r'\s+', ' ', why).strip()
    sentences = [part.strip() for part in re.split(r'(?<=[.!?])\s+', why) if part.strip()]
    first = sentences[0] if sentences else 'It turns before you can look away.'
    second = sentences[1] if len(sentences) > 1 else 'The next second is the one people replay.'
    tags = hashtags(film, title)
    youtube_title = title if len(title) <= 70 else title[:69].rstrip() + '…'
    source = f'\n\nFrom: {film.strip()}' if film.strip() else ''
    instagram = f'{hook}\n\n{first} {second}\n\nWatch it to the last second.\n\n{tags}'
    youtube = f'{hook}\n\n{first}\n{second}\n\nWatch to the last second.{source}'.strip()
    tiktok = f'{hook}\n\n{first}\n\n{tags}'
    return {
        'title': safe.censor(title),
        'instagram': safe.censor(instagram),
        'youtube_title': safe.censor(youtube_title),
        'youtube': safe.censor(youtube),
        'tiktok': safe.censor(tiktok),
    }


def write_meta(path: Path, copy: dict, video: Path):
    path.write_text(
        f"TITLE\n{copy['title']}\n\n"
        f"INSTAGRAM\n{copy['instagram']}\n\n"
        f"YOUTUBE TITLE\n{copy['youtube_title']}\n\n"
        f"YOUTUBE DESCRIPTION\n{copy['youtube']}\n\n"
        f"TIKTOK\n{copy['tiktok']}\n\n"
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
    names: list[str] = []
    try:
        url = youtube_url(str(body.get('url') or ''))
        clips = clip_list(body)
        if not clips:
            raise RuntimeError('Pick at least one moment.')
        import yt_dlp
        from yt_dlp.utils import download_range_func
        folder = prepare_folder(str(body.get('export_dir') or ''))
        film = str(body.get('film') or '')
        files = []
        total = len(clips)
        part_dir = DATA / 'parts'
        part_dir.mkdir(parents=True, exist_ok=True)
        for index, clip in enumerate(clips):
            start, end = float(clip.get('start')), float(clip.get('end'))
            duration = end - start
            if duration < 8 or duration > 90:
                raise RuntimeError('That clip is not one short.')
            hook = str(clip.get('hook') or 'Watch this')[:80]
            title = str(clip.get('title') or hook)[:120]
            lines = [line for line in (clip.get('lines') or [])[:90] if isinstance(line, dict)]
            hook = safe.censor(hook)
            title = safe.censor(title)
            lines = [{**line, 'text': safe.censor(str(line.get('text') or ''))} for line in lines]
            lead = 2.0 if start >= 2 else max(0.0, start)
            name = uuid.uuid4().hex
            names.append(name)
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
                format='bv*+ba/b',
                format_sort=['res', 'br'],
                merge_output_format='mp4',
                outtmpl=str(part_dir / (name + '.%(ext)s')),
                download_ranges=download_range_func(None, [(max(0.0, start - lead), end + 0.4)]),
                force_keyframes_at_cuts=True,
                progress_hooks=[on_progress],
                noprogress=False,
            )) as ydl:
                ydl.download([url])
            matches = sorted(part_dir.glob(name + '.*'))
            if not matches:
                raise RuntimeError('The piece did not download.')
            src = matches[0]
            got = media_length(src)
            pad = max(0.0, got - duration - 0.4)
            src_w, src_h = video_size(src)
            _, _, quality = reel_canvas(src_w, src_h)
            update(job_id, progress=round(base + span * 0.75, 3), step=f'Rendering {index + 1} of {total}', detail=quality, quality=quality)
            out = DATA / 'reels' / f'{name}.mp4'
            quality = compose(src, pad, duration, hook, lines, out)
            copy = attract(title, hook, str(clip.get('why') or ''), film)
            stem = unique_stem(folder, clip_slug(copy['title']))
            dest = folder / f'{stem}.mp4'
            meta = folder / f'{stem}-meta.txt'
            shutil.copy2(out, dest)
            write_meta(meta, copy, dest)
            files.append({
                'file': f'/api/media/{name}.mp4',
                'title': copy['title'],
                'quality': quality,
                'start': start,
                'path': str(dest),
                'meta_path': str(meta),
                'instagram': copy['instagram'],
                'youtube_title': copy['youtube_title'],
                'youtube': copy['youtube'],
                'tiktok': copy['tiktok'],
            })
            update(job_id, files=files, file=files[0]['file'], quality=quality, progress=round(base + span, 3))
        update(job_id, status='done', progress=1, step='Reels ready', files=files, file=files[0]['file'], quality=files[0]['quality'], detail=files[0]['quality'], error='')
    except Exception as exc:
        message = clean(str(exc)) or 'The cut failed.'
        update(job_id, status='error', step='Stopped', error=message, detail=message)
    finally:
        for name in names:
            for path in (DATA / 'parts').glob(name + '.*'):
                path.unlink(missing_ok=True)


@app.get('/api/folder')
def folder_default():
    folder = prepare_folder('')
    return {'path': str(folder)}


@app.post('/api/folder')
def folder_pick():
    try:
        proc = subprocess.run(
            ['osascript', '-e', 'POSIX path of (choose folder with prompt "Save AnuClipz reels here")'],
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        fail('The folder window timed out.')
    if proc.returncode != 0:
        return {'path': ''}
    chosen = proc.stdout.strip()
    if not chosen:
        return {'path': ''}
    try:
        folder = prepare_folder(chosen)
    except Exception as exc:
        fail(clean(str(exc)) or 'That folder cannot be used.')
    return {'path': str(folder)}


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
    path = DATA / 'thumbs' / name
    if not path.is_file():
        fail('That frame is gone.', 404)
    return FileResponse(path, media_type='image/jpeg')


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
    DATA.mkdir(parents=True, exist_ok=True)
    print('Clipper engine is on http://127.0.0.1:8781', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=8781, log_level='info')
