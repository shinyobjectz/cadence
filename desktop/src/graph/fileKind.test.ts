import { describe, expect, test } from 'vitest'
import { kindFromPath } from './fileKind'

describe('kindFromPath', () => {
  test('maps image, video, and audio extensions, else file', () => {
    expect(kindFromPath('assets/in/logo-abc.png')).toBe('image')
    expect(kindFromPath('clip.MP4')).toBe('video')
    expect(kindFromPath('/tmp/voice.wav')).toBe('audio')
    expect(kindFromPath('notes.txt')).toBe('file')
    expect(kindFromPath('noext')).toBe('file')
  })
})
