import type { Source } from '../model'

/** Mock status follows the supplied reference, independently for each result block. */
export const isMockSource = (source: Source) => /^mock:/i.test(source.ref)

export function MockLabel({ sources, children = 'Assumed' }: { sources: Source[]; children?: string }) {
  return sources.some(isMockSource) ? <span className="mock-label">{children}</span> : null
}
