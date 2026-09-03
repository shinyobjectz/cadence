import { create } from 'zustand'
import { loadDoc, openProjectFromPicker, saveDoc } from '../cadence/api'
import type {
  EditorTool,
  Keyframe,
  KeyframeKind,
  ProjectDoc,
} from './types'
import { launchSpotExample } from './exampleDoc'

function uid() {
  return crypto.randomUUID()
}

type EditorState = {
  doc: ProjectDoc
  projectPath: string | null
  projectName: string
  dirty: boolean
  playhead: number
  playing: boolean
  scrubbing: boolean
  zoom: number
  volume: number
  tool: EditorTool
  leftTab: 'transcript' | 'scenes'
  theme: 'light' | 'dark'
  selectedKeyframeId: string | null
  voResolving: boolean
  setPlayhead: (t: number) => void
  setPlaying: (v: boolean) => void
  setScrubbing: (v: boolean) => void
  togglePlay: () => void
  setVoPath: (rel: string) => void
  setZoom: (z: number) => void
  setVolume: (v: number) => void
  setTool: (t: EditorTool) => void
  setLeftTab: (tab: 'transcript' | 'scenes') => void
  toggleTheme: () => void
  selectKeyframe: (id: string | null) => void
  addLineKeyframe: (lineId: string, gapIndex: number, kind: KeyframeKind) => void
  moveLineKeyframe: (id: string, gapIndex: number) => void
  updateSceneParam: (id: string, value: string) => void
  setDoc: (doc: ProjectDoc) => void
  loadProject: (path: string) => Promise<void>
  openProjectPicker: () => Promise<void>
  saveProject: () => Promise<void>
}

export const useEditor = create<EditorState>((set, get) => ({
  doc: launchSpotExample(),
  projectPath: null,
  projectName: 'Demo',
  dirty: false,
  playhead: 0,
  playing: false,
  scrubbing: false,
  zoom: 1,
  volume: 1,
  tool: 'select',
  leftTab: 'scenes',
  theme: 'dark',
  selectedKeyframeId: null,
  voResolving: false,

  setPlayhead: (t) => set({ playhead: Math.max(0, Math.min(t, get().doc.duration)) }),
  setPlaying: (playing) => set({ playing }),
  setScrubbing: (scrubbing) => set({ scrubbing }),
  togglePlay: () => set((s) => ({ playing: !s.playing })),
  setVoPath: (voPath) =>
    set((s) => ({
      doc: { ...s.doc, voPath },
      dirty: true,
    })),
  setZoom: (zoom) => set({ zoom: Math.max(0.5, Math.min(zoom, 16)) }),
  setVolume: (volume) => set({ volume: Math.max(0, Math.min(volume, 1)) }),
  setTool: (tool) => set({ tool }),
  setLeftTab: (leftTab) => set({ leftTab }),
  toggleTheme: () =>
    set((s) => ({ theme: s.theme === 'dark' ? 'light' : 'dark' })),
  selectKeyframe: (selectedKeyframeId) => set({ selectedKeyframeId }),

  setDoc: (doc) => set({ doc, dirty: true }),

  loadProject: async (path) => {
    set({ voResolving: true })
    try {
      const doc = await loadDoc(path)
      const name = path.replace(/\\/g, '/').split('/').filter(Boolean).pop() ?? 'Project'
      set({
        projectPath: path,
        projectName: name,
        doc,
        dirty: false,
        playhead: 0,
        playing: false,
        selectedKeyframeId: null,
        voResolving: false,
      })
    } catch (err) {
      set({ voResolving: false })
      throw err
    }
  },

  openProjectPicker: async () => {
    const picked = await openProjectFromPicker()
    if (!picked) return
    await get().loadProject(picked.path)
  },

  saveProject: async () => {
    const { projectPath, doc } = get()
    if (!projectPath) {
      throw new Error('Open a project folder before saving')
    }
    await saveDoc(projectPath, doc)
    set({ dirty: false })
  },

  addLineKeyframe: (lineId, gapIndex, kind) => {
    const k: Keyframe = { id: uid(), kind, gapIndex, parentId: lineId, parentType: 'line' }
    set((s) => ({
      doc: { ...s.doc, lineKeyframes: [...s.doc.lineKeyframes, k] },
      selectedKeyframeId: k.id,
      dirty: true,
    }))
  },

  moveLineKeyframe: (id, gapIndex) => {
    set((s) => ({
      doc: {
        ...s.doc,
        lineKeyframes: s.doc.lineKeyframes.map((k) =>
          k.id === id ? { ...k, gapIndex } : k,
        ),
      },
      dirty: true,
    }))
  },

  updateSceneParam: (id, value) => {
    set((s) => {
      const param = s.doc.sceneParams[id]
      if (!param) return s
      return {
        doc: {
          ...s.doc,
          sceneParams: { ...s.doc.sceneParams, [id]: { ...param, value } },
        },
        dirty: true,
      }
    })
  },
}))

export function lineKeyframesFor(lineId: string, keyframes: Keyframe[]) {
  return keyframes.filter((k) => k.parentId === lineId)
}
