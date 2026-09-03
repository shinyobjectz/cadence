#!/usr/bin/env node
/** Prerender launch-spot VO + word alignment before dev server. */
import { createHash } from 'node:crypto'
import { execSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const PROJECT = path.join(ROOT, 'evals/projects/launch-spot')
const PROVIDER = 'pocket-tts'
const ALIGN_VERSION = 'speech-regions-v1'
const DOC_PATH = path.join(PROJECT, 'doc.json')

function scriptFromDoc(doc) {
  return doc.lines
    .flatMap((line) => line.words.map((w) => w.text))
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function voKey(script) {
  return createHash('sha1').update(`${script}|${PROVIDER}`).digest('hex')
}

function alignKey(script) {
  return createHash('sha1').update(`${script}|${PROVIDER}|${ALIGN_VERSION}`).digest('hex')
}

function normToken(text) {
  return text.toLowerCase().replace(/[^a-z0-9]/g, '')
}

function applyAlignment(doc, aligned) {
  const refs = doc.lines.flatMap((line, lineIndex) =>
    line.words.map((_, wordIndex) => ({ lineIndex, wordIndex })),
  )
  let ai = 0
  let updated = 0
  for (const { lineIndex, wordIndex } of refs) {
    const want = normToken(doc.lines[lineIndex].words[wordIndex].text)
    while (ai < aligned.length && normToken(aligned[ai].text) !== want) ai += 1
    if (ai >= aligned.length) break
    const aw = aligned[ai]
    const w = doc.lines[lineIndex].words[wordIndex]
    w.start = Math.round((aw.start ?? aw.t0 ?? 0) * 10000) / 10000
    w.end = Math.round((aw.end ?? aw.t1 ?? w.start) * 10000) / 10000
    updated += 1
    ai += 1
  }
  return updated
}

function alignVo(audioPath, script) {
  const alignTmp = path.join('/tmp', `cadence-align-${Date.now()}.json`)
  const cadence = path.join(ROOT, 'bin/cadence')
  execSync(
    `"${cadence}" align --file ${JSON.stringify(audioPath)} --text ${JSON.stringify(script)} --out ${JSON.stringify(alignTmp)}`,
    { cwd: PROJECT, stdio: 'pipe' },
  )
  const aligned = JSON.parse(readFileSync(alignTmp, 'utf8')).words
  unlinkSync(alignTmp)
  return aligned
}

function main() {
  if (!existsSync(DOC_PATH)) {
    console.log('cadence: no doc.json — skip VO prerender')
    return
  }
  const doc = JSON.parse(readFileSync(DOC_PATH, 'utf8'))
  const script = scriptFromDoc(doc)
  if (!script) {
    console.log('cadence: empty transcript — skip VO prerender')
    return
  }
  const key = voKey(script)
  const akey = alignKey(script)
  const voReady =
    doc.voKey === key && doc.voPath && existsSync(path.join(PROJECT, doc.voPath))
  const alignReady = voReady && doc.voAlignKey === akey

  if (alignReady) {
    console.log(`cadence: VO + align cache hit (${doc.voPath})`)
    return
  }

  let rel = doc.voPath
  if (!voReady) {
    const tmp = path.join('/tmp', `cadence-prerender-${Date.now()}.mp3`)
    const cadence = path.join(ROOT, 'bin/cadence')
    execSync(
      `"${cadence}" tts --text ${JSON.stringify(script)} --out ${JSON.stringify(tmp)} --provider ${PROVIDER} --model ${PROVIDER}`,
      { cwd: PROJECT, stdio: 'inherit' },
    )
    const bytes = readFileSync(tmp)
    const hash = createHash('sha256').update(bytes).digest('hex').slice(0, 12)
    rel = `assets/in/vo-${hash}.mp3`
    mkdirSync(path.join(PROJECT, 'assets/in'), { recursive: true })
    writeFileSync(path.join(PROJECT, rel), bytes)
    doc.voPath = rel
    doc.voKey = key
    doc.voProvider = PROVIDER
    console.log(`cadence: prerendered VO → ${rel}`)
  }

  const aligned = alignVo(path.join(PROJECT, rel), script)
  const n = applyAlignment(doc, aligned)
  doc.voAlignKey = akey
  writeFileSync(DOC_PATH, `${JSON.stringify(doc, null, 2)}\n`)
  console.log(`cadence: aligned ${n} words to VO`)
}

main()
