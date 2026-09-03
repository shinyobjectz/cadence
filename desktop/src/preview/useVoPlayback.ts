import { useCallback, useEffect, useRef, useState } from 'react'
import { resolveAssetUrl } from '../cadence/api'
import { transcriptEndTime } from '../editor/transcriptPlayhead'
import type { ProjectDoc } from '../editor/types'

type VoPlaybackOpts = {
  projectPath: string | null
  doc: ProjectDoc
  playhead: number
  playing: boolean
  scrubbing: boolean
  volume: number
  setPlayhead: (t: number) => void
}

/**
 * Editorial VO player — one clock during playback (HTML Audio), seek only on scrub.
 * Avoids syncing currentTime every React frame (that causes stutter / "pieces").
 */
export function useVoPlayback({
  projectPath,
  doc,
  playhead,
  playing,
  scrubbing,
  volume,
  setPlayhead,
}: VoPlaybackOpts) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [ready, setReady] = useState(false)
  const playheadRef = useRef(playhead)
  const playingRef = useRef(playing)
  const scrubbingRef = useRef(scrubbing)
  const rafRef = useRef<number | null>(null)

  const voEnd = transcriptEndTime(doc)
  const hasVo = Boolean(doc.voPath)

  playheadRef.current = playhead
  playingRef.current = playing
  scrubbingRef.current = scrubbing

  useEffect(() => {
    const rel = doc.voPath
    if (!rel || !projectPath) {
      setReady(false)
      audioRef.current?.pause()
      audioRef.current = null
      return
    }

    let cancelled = false
    setReady(false)
    void (async () => {
      try {
        const url = await resolveAssetUrl(projectPath, rel)
        if (cancelled) return
        const audio = new Audio()
        audio.preload = 'auto'
        const markReady = () => {
          if (cancelled) return
          audioRef.current = audio
          setReady(true)
        }
        audio.addEventListener('canplaythrough', markReady, { once: true })
        audio.addEventListener('loadeddata', markReady, { once: true })
        audio.addEventListener(
          'error',
          () => console.warn('cadence vo: decode failed', rel),
          { once: true },
        )
        audio.src = url
        audio.load()
      } catch (err) {
        console.warn('cadence vo: load failed', err)
      }
    })()

    return () => {
      cancelled = true
      setReady(false)
      audioRef.current?.pause()
      audioRef.current = null
    }
  }, [doc.voPath, projectPath])

  const seekAudio = useCallback(
    (t: number) => {
      const audio = audioRef.current
      if (!audio) return
      const clamped = Math.max(0, Math.min(t, voEnd))
      if (Math.abs(audio.currentTime - clamped) > 0.02) {
        audio.currentTime = clamped
      }
    },
    [voEnd],
  )

  // Scrub / pause: mirror timeline playhead into the audio element.
  useEffect(() => {
    const audio = audioRef.current
    if (!ready || !audio) return
    audio.volume = volume
    if (playing) return
    seekAudio(playhead)
  }, [ready, playhead, playing, volume, seekAudio])

  // Scrub while holding timeline: follow playhead even if "playing" flag is set.
  useEffect(() => {
    if (!ready || !scrubbing) return
    seekAudio(playheadRef.current)
    audioRef.current?.pause()
  }, [ready, scrubbing, playhead, seekAudio])

  // Play / pause transitions.
  useEffect(() => {
    const audio = audioRef.current
    if (!ready || !audio) return

    if (!playing) {
      audio.pause()
      return
    }

    if (playheadRef.current > voEnd + 0.02) {
      audio.pause()
      return
    }

    audio.volume = volume
    seekAudio(playheadRef.current)

    void audio.play().catch((err) => console.warn('cadence vo: play blocked', err))
  }, [ready, playing, volume, voEnd, seekAudio])

  // During playback, audio is the master clock — drive the editorial playhead from it.
  useEffect(() => {
    if (!ready || !hasVo) return

    const tick = () => {
      const audio = audioRef.current
      if (!audio || !playingRef.current) {
        rafRef.current = null
        return
      }

      const t = audio.currentTime
      if (t < voEnd) {
        playheadRef.current = t
        setPlayhead(t)
      } else if (playheadRef.current < voEnd) {
        playheadRef.current = voEnd
        setPlayhead(voEnd)
      }

      if (t >= voEnd) {
        // VO finished — timeline keeps going for editorial tail.
        audio.pause()
        if (playingRef.current) {
          // Don't stop the whole timeline; only stop re-seeking VO.
        }
      }

      rafRef.current = requestAnimationFrame(tick)
    }

    if (playing) {
      rafRef.current = requestAnimationFrame(tick)
    }

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
  }, [ready, hasVo, playing, voEnd, setPlayhead])

  const pause = useCallback(() => {
    audioRef.current?.pause()
  }, [])

  return { pause, voEnd, ready, hasVo }
}
