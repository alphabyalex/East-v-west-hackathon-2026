import type { Source } from '../model'

/** Mock status follows the supplied reference, independently for each result block. */
export const isMockSource = (source: Source) => /^mock:/i.test(source.ref)
