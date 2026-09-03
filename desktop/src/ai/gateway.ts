import { invoke } from '@tauri-apps/api/core'

/** Relative cost chip: $ / $$ / $$$. HTTP lives in the Rust sidecar — no fetch() here. */
export type Model = { id: string; label: string; cost: 1 | 2 | 3 }

export const TEXT_MODELS: Model[] = [
  { id: 'gpt-4o-mini', label: 'gpt-4o-mini', cost: 1 },
  { id: 'gpt-4o', label: 'gpt-4o', cost: 2 },
]

export const IMAGE_MODELS: Model[] = [
  { id: 'gpt-image-1', label: 'gpt-image-1', cost: 2 },
]

export const VIDEO_MODELS: Model[] = [
  { id: 'placeholder-video', label: 'placeholder-video', cost: 3 },
]

export function costChip(cost: 1 | 2 | 3): string {
  return '$'.repeat(cost)
}

export function generateText(prompt: string, model: string): Promise<string> {
  return invoke<string>('generate_text', { prompt, model })
}

export function generateImage(
  project: string,
  prompt: string,
  model: string,
): Promise<string> {
  return invoke<string>('generate_image', { project, prompt, model })
}

export function generateVideo(
  project: string,
  prompt: string,
  model: string,
): Promise<string> {
  return invoke<string>('generate_video', { project, prompt, model })
}

export function setSecret(name: string, value: string): Promise<void> {
  return invoke('set_secret', { name, value })
}

export function secretIsSet(name: string): Promise<boolean> {
  return invoke<boolean>('secret_is_set', { name })
}

export function saveAssetBytes(
  project: string,
  stem: string,
  ext: string,
  bytes: number[],
): Promise<string> {
  return invoke<string>('save_asset_bytes', { project, stem, ext, bytes })
}
