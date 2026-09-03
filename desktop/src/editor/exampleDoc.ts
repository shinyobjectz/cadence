import type { ProjectDoc, TranscriptLine } from './types'

/** ~12s product launch spot — VO sync + editorial tail (montage, end card). */
export function launchSpotExample(): ProjectDoc {
  const hook: TranscriptLine = {
    id: 'vo-hook',
    words: [
      { id: 'w01', text: 'Meet', start: 0.52, end: 0.78 },
      { id: 'w02', text: 'Cadence.', start: 0.78, end: 1.34 },
    ],
  }

  const pitch: TranscriptLine = {
    id: 'vo-pitch',
    words: [
      { id: 'w03', text: 'The', start: 1.72, end: 1.86 },
      { id: 'w04', text: 'fastest', start: 1.86, end: 2.28 },
      { id: 'w05', text: 'way', start: 2.28, end: 2.48 },
      { id: 'w06', text: 'to', start: 2.48, end: 2.58 },
      { id: 'w07', text: 'turn', start: 2.58, end: 2.82 },
      { id: 'w08', text: 'scripts', start: 2.82, end: 3.24 },
      { id: 'w09', text: 'into', start: 3.24, end: 3.42 },
      { id: 'w10', text: 'polished', start: 3.42, end: 3.88 },
      { id: 'w11', text: 'video.', start: 3.88, end: 4.36 },
    ],
  }

  const close: TranscriptLine = {
    id: 'vo-close',
    words: [
      { id: 'w12', text: 'Just', start: 4.84, end: 5.08 },
      { id: 'w13', text: 'write,', start: 5.08, end: 5.46 },
      { id: 'w14', text: 'sync,', start: 5.62, end: 6.02 },
      { id: 'w15', text: 'and', start: 6.18, end: 6.32 },
      { id: 'w16', text: 'ship.', start: 6.32, end: 6.78 },
    ],
  }

  return {
    duration: 13,
    fps: 30,
    aspect: '16:9',
    compPath: 'comps/launch.lua',
    lines: [hook, pitch, close],
    lineKeyframes: [
      { id: 'kf-logo-hit', kind: 'open', gapIndex: 1, parentId: 'vo-hook', parentType: 'line' },
      { id: 'kf-fastest', kind: 'mid', gapIndex: 2, parentId: 'vo-pitch', parentType: 'line' },
      { id: 'kf-video', kind: 'close', gapIndex: 10, parentId: 'vo-pitch', parentType: 'line' },
      { id: 'kf-ship', kind: 'open', gapIndex: 4, parentId: 'vo-close', parentType: 'line' },
    ],
    sceneParams: {
      logoScale: {
        id: 'logoScale',
        name: 'logoScale',
        type: 'float',
        value: '1.08',
        suffix: '×',
      },
      logoDuration: {
        id: 'logoDuration',
        name: 'logoDuration',
        type: 'float',
        value: '0.65',
        suffix: 's',
      },
      logoEasing: {
        id: 'logoEasing',
        name: 'logoEasing',
        type: 'string',
        value: 'cubic-bezier(0.16,1,0.3,1)',
      },
      uiBlur: {
        id: 'uiBlur',
        name: 'uiBlur',
        type: 'integer',
        value: '24',
        suffix: 'px',
      },
      uiStagger: {
        id: 'uiStagger',
        name: 'uiStagger',
        type: 'float',
        value: '0.08',
        suffix: 's',
      },
      captionMode: {
        id: 'captionMode',
        name: 'captionMode',
        type: 'string',
        value: 'phrase',
      },
      captionWindow: {
        id: 'captionWindow',
        name: 'captionWindow',
        type: 'integer',
        value: '8',
        suffix: ' words',
      },
      captionWrap: {
        id: 'captionWrap',
        name: 'captionWrap',
        type: 'integer',
        value: '920',
        suffix: 'px',
      },
      captionTail: {
        id: 'captionTail',
        name: 'captionTail',
        type: 'float',
        value: '0.35',
        suffix: 's',
      },
      montageCuts: {
        id: 'montageCuts',
        name: 'montageCuts',
        type: 'list',
        value: '["timeline","transcript","preview"]',
      },
      montageBpm: {
        id: 'montageBpm',
        name: 'montageBpm',
        type: 'integer',
        value: '128',
      },
      ctaLabel: {
        id: 'ctaLabel',
        name: 'ctaLabel',
        type: 'string',
        value: 'Start free',
      },
      ctaUrl: {
        id: 'ctaUrl',
        name: 'ctaUrl',
        type: 'string',
        value: 'cadence.video/start',
      },
      brandColor: {
        id: 'brandColor',
        name: 'brandColor',
        type: 'map',
        value: '{"primary":"#7c3aed","bg":"#0f0f12"}',
      },
    },
    scenes: [
      {
        id: 'sc-open',
        at: 0.0,
        duration: 1.8,
        segments: [
          { type: 'text', content: 'Cold open on black. Logo rises from ' },
          { type: 'param', paramId: 'logoScale' },
          { type: 'text', content: ' scale over ' },
          { type: 'param', paramId: 'logoDuration' },
          { type: 'text', content: ' using ' },
          { type: 'param', paramId: 'logoEasing' },
          { type: 'text', content: '. Soft vignette, no UI yet.' },
        ],
      },
      {
        id: 'sc-ui',
        at: 2.82,
        duration: 2.4,
        segments: [
          {
            type: 'text',
            content: 'Hard cut to editor UI on the word “scripts”. Window blur peaks at ',
          },
          { type: 'param', paramId: 'uiBlur' },
          { type: 'text', content: '. Panels stagger in every ' },
          { type: 'param', paramId: 'uiStagger' },
          { type: 'text', content: '. Keep playhead locked to VO.' },
        ],
      },
      {
        id: 'sc-montage',
        at: 7.2,
        duration: 3.0,
        segments: [
          { type: 'text', content: 'Montage on beat at ' },
          { type: 'param', paramId: 'montageBpm' },
          { type: 'text', content: ' BPM. Feature cards cycle ' },
          { type: 'param', paramId: 'montageCuts' },
          { type: 'text', content: '. Each card gets a single mid keyframe for the snap.' },
        ],
      },
      {
        id: 'sc-cta',
        at: 10.5,
        duration: 2.2,
        segments: [
          { type: 'text', content: 'End card: button copy “' },
          { type: 'param', paramId: 'ctaLabel' },
          { type: 'text', content: '” links to ' },
          { type: 'param', paramId: 'ctaUrl' },
          { type: 'text', content: '. Palette from ' },
          { type: 'param', paramId: 'brandColor' },
          { type: 'text', content: '. Hold through end card.' },
        ],
      },
    ],
  }
}
