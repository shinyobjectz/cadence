const NATIVE_CALL =
  /\bs:(?:html|page|vector|fx|world|displace|draw)\b/
const NATIVE_KIND =
  /\bkind\s*=\s*['"](?:html|page|vector|fx|world|displace|draw)['"]/

export function isNativeOnly(source: string): boolean {
  return NATIVE_CALL.test(source) || NATIVE_KIND.test(source)
}

/** CompNode mounts WasmoonFrame only when this is true. */
export function shouldPreview(source: string): boolean {
  return !isNativeOnly(source)
}
