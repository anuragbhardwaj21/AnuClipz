import { spawn, execSync } from 'node:child_process'
import { existsSync, mkdirSync, createWriteStream } from 'node:fs'
import { get } from 'node:https'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('.', import.meta.url))
process.chdir(root)

function run(command) {
  execSync(command, { stdio: 'inherit' })
}

if (!existsSync('node_modules/react')) run('npm install')
if (!existsSync('.venv/bin/python')) {
  run('python3 -m venv .venv')
  run('.venv/bin/pip install -r requirements.txt')
}

async function saveZip(url, file) {
  await new Promise((resolve, reject) => {
    get(url, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume()
        const next = new URL(res.headers.location, url).href
        saveZip(next, file).then(resolve, reject)
        return
      }
      if (res.statusCode !== 200) {
        res.resume()
        reject(new Error(`Download failed (${res.statusCode}) for ${url}`))
        return
      }
      const out = createWriteStream(file)
      res.pipe(out)
      out.on('finish', resolve)
      out.on('error', reject)
    }).on('error', reject)
  })
}

if (!existsSync('.tools/ffmpeg') && process.platform === 'darwin' && process.arch === 'arm64') {
  mkdirSync('.tools', { recursive: true })
  console.log('Fetching the caption ffmpeg once…')
  await saveZip('https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip', '.tools/ffmpeg.zip')
  await saveZip('https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffprobe.zip', '.tools/ffprobe.zip')
  run('unzip -o .tools/ffmpeg.zip -d .tools && unzip -o .tools/ffprobe.zip -d .tools && chmod +x .tools/ffmpeg .tools/ffprobe')
}

const env = { ...process.env }
for (const key of ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'all_proxy']) delete env[key]

const engine = spawn('.venv/bin/python', ['backend/server.py'], { env, stdio: 'inherit' })
const page = spawn('npx', ['vite', '--host', '127.0.0.1', '--port', '5174', '--strictPort'], { env, stdio: 'inherit' })

function stop() {
  engine.kill('SIGTERM')
  page.kill('SIGTERM')
  process.exit(0)
}
process.on('SIGINT', stop)
process.on('SIGTERM', stop)
engine.on('exit', (code) => { if (code) process.exit(code) })
page.on('exit', (code) => { if (code) process.exit(code) })
console.log('Open http://127.0.0.1:5174')
