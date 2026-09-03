import { MediaBag } from '../../../web/media.js'
import { collectAssetPaths, type AssetResolver } from './elluaAssets'

function loadImage(url: string) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const img = new Image()
    img.decoding = 'async'
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error(`image ${url}`))
    img.src = url
  })
}

function attachVideo(video: HTMLVideoElement) {
  video.muted = true
  video.defaultMuted = true
  video.playsInline = true
  video.preload = 'auto'
  video.setAttribute('playsinline', '')
  video.setAttribute('muted', '')
  if (!video.isConnected) {
    video.style.cssText =
      'position:fixed;left:-4096px;top:0;width:480px;height:270px;pointer-events:none;border:0'
    document.body.appendChild(video)
  }
  return video
}

function loadVideoSrc(url: string) {
  return new Promise<HTMLVideoElement>((resolve, reject) => {
    const video = attachVideo(document.createElement('video'))
    video.addEventListener('loadeddata', () => resolve(video), { once: true })
    video.addEventListener('error', () => reject(new Error(`video ${url}`)), { once: true })
    video.src = url
    video.load()
  })
}

/** Load comp assets with Tauri-resolved URLs (web MediaBag uses Vite-relative paths). */
export async function loadMediaFromComp(
  bag: MediaBag,
  luaSrc: string,
  resolveAsset: AssetResolver,
) {
  const paths = collectAssetPaths(luaSrc)
  const jobs = paths.map(async (src) => {
    const url = await resolveAsset(src)
    const ext = src.split('.').pop()?.toLowerCase() ?? ''
    if (ext === 'ttf' || ext === 'otf' || ext === 'woff' || ext === 'woff2') {
      const face = new FontFace(
        `"ellua_${src.replace(/[^A-Za-z0-9]/g, '_')}"`,
        `url(${url})`,
      )
      await face.load()
      document.fonts.add(face)
    } else if (['jpg', 'jpeg', 'png', 'svg', 'webp'].includes(ext)) {
      const img = await loadImage(url)
      bag.images.set(src, img)
    } else if (ext === 'webm' || ext === 'mp4') {
      const video = await loadVideoSrc(url)
      bag.images.set(`video:${src}`, video)
    } else if (ext === 'ogg' || ext === 'mp3' || ext === 'wav') {
      const audio = new Audio(url)
      audio.preload = 'auto'
      bag.audios.set(src, { el: audio })
    } else if (ext === 'json') {
      const data = await fetch(url).then((res) => {
        if (!res.ok) throw new Error(`json ${url}`)
        return res.json()
      })
      if (data.v && data.fr && data.layers) {
        bag.lottieData.set(src, data)
      }
    }
  })
  const results = await Promise.allSettled(jobs)
  const failed = results.filter((r) => r.status === 'rejected')
  if (failed.length) {
    console.warn('cadence preview: some assets failed', failed.map((r) => r.reason))
  }
}
