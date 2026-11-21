import { spawn, execFileSync, execSync } from 'node:child_process'
import { chmodSync, copyFileSync, existsSync, mkdirSync, createWriteStream, readdirSync, statSync } from 'node:fs'
import { get } from 'node:https'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('.', import.meta.url))
const win = process.platform === 'win32'
process.chdir(root)

function run(command) {
  execSync(command, { stdio: 'inherit', shell: true })
}

function commandWorks(bin, args) {
  try {
    execFileSync(bin, args, { stdio: 'ignore' })
    return true
  } catch {
    return false
  }
}

function pythonLaunch() {
  const tries = win ? [['py', '-3'], ['python'], ['python3']] : [['python3'], ['python']]
  for (const args of tries) {
    if (commandWorks(args[0], [...args.slice(1), '--version'])) return args
  }
  console.error('Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ and run npm start again.')
  process.exit(1)
}

function pythonVersion(launch) {
  const text = execFileSync(launch[0], [...launch.slice(1), '-c', 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")'], { encoding: 'utf8' })
  const [major, minor] = text.trim().split('.').map(Number)
  return [major, minor]
}

const python = pythonLaunch()
const [pyMajor, pyMinor] = pythonVersion(python)
if (pyMajor < 3 || (pyMajor === 3 && pyMinor < 10)) {
  console.error(`Python ${pyMajor}.${pyMinor} is too old. Install Python 3.10 or newer, then run npm start again.`)
  process.exit(1)
}

const venvPython = win ? join('.venv', 'Scripts', 'python.exe') : join('.venv', 'bin', 'python')

if (!existsSync('node_modules/react')) run('npm install')
if (!existsSync(venvPython)) {
  execFileSync(python[0], [...python.slice(1), '-m', 'venv', '.venv'], { stdio: 'inherit' })
  execFileSync(venvPython, ['-m', 'pip', 'install', '-r', 'requirements.txt'], { stdio: 'inherit' })
}

function saveZip(url, file) {
  return new Promise((resolve, reject) => {
    get(url, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume()
        saveZip(new URL(res.headers.location, url).href, file).then(resolve, reject)
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

function unpack(zip, dest) {
  execFileSync(venvPython, ['-c', 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])', zip, dest], { stdio: 'inherit' })
}

function walk(dir, found = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) walk(path, found)
    else found.push(path)
  }
  return found
}

function placeTool(extracted, name) {
  const wanted = win ? `${name}.exe` : name
  const match = walk(extracted).find((path) => path.split(/[\\/]/).pop() === wanted)
  if (!match) throw new Error(`The download did not contain ${wanted}.`)
  const dest = join('.tools', wanted)
  copyFileSync(match, dest)
  if (!win) chmodSync(dest, 0o755)
}

async function ensureFfmpeg() {
  const ffmpegName = win ? 'ffmpeg.exe' : 'ffmpeg'
  const probeName = win ? 'ffprobe.exe' : 'ffprobe'
  if (existsSync(join('.tools', ffmpegName)) && existsSync(join('.tools', probeName))) return
  mkdirSync('.tools', { recursive: true })
  const arch = process.arch === 'arm64' ? 'arm64' : process.arch === 'x64' ? 'amd64' : ''
  console.log('Fetching the caption ffmpeg once…')
  const staging = join('.tools', 'unpack')
  mkdirSync(staging, { recursive: true })
  if (process.platform === 'darwin' || process.platform === 'linux') {
    if (!arch) throw new Error('This processor is not supported. Use a 64-bit Mac, Windows, or Linux machine.')
    const osName = process.platform === 'darwin' ? 'macos' : 'linux'
    const base = `https://ffmpeg.martin-riedl.de/redirect/latest/${osName}/${arch}/release`
    await saveZip(`${base}/ffmpeg.zip`, join('.tools', 'ffmpeg.zip'))
    await saveZip(`${base}/ffprobe.zip`, join('.tools', 'ffprobe.zip'))
    unpack(join('.tools', 'ffmpeg.zip'), staging)
    unpack(join('.tools', 'ffprobe.zip'), staging)
  } else if (win) {
    const zip = join('.tools', 'ffmpeg-essentials.zip')
    await saveZip('https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip', zip)
    unpack(zip, staging)
  } else {
    throw new Error('This operating system is not supported. Use Windows, macOS, or Linux.')
  }
  placeTool(staging, 'ffmpeg')
  placeTool(staging, 'ffprobe')
}

await ensureFfmpeg()

const env = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }
for (const key of ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'all_proxy']) delete env[key]

const engine = spawn(venvPython, ['backend/server.py'], { env, stdio: 'inherit', cwd: root })
const page = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5174', '--strictPort'], { env, stdio: 'inherit', cwd: root })

function stop() {
  engine.kill('SIGTERM')
  page.kill('SIGTERM')
  process.exit(0)
}
process.on('SIGINT', stop)
process.on('SIGTERM', stop)
engine.on('error', (err) => { console.error(err.message); process.exit(1) })
page.on('error', (err) => { console.error(err.message); process.exit(1) })
engine.on('exit', (code) => { if (code) process.exit(code) })
page.on('exit', (code) => { if (code) process.exit(code) })
console.log('Open http://127.0.0.1:5174')
