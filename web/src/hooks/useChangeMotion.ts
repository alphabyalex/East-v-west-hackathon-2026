import { useEffect, useRef, type RefObject } from 'react'

/** Brief feedback for discrete result/view changes. Never animates initial entry. */
export function useChangeMotion(ref: RefObject<HTMLElement | null>, value: string) {
  const previous = useRef(value)
  useEffect(() => {
    if (previous.current === value) return
    previous.current = value
    const element = ref.current
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (!element?.animate || preference.matches || document.visibilityState === 'hidden') return
    const animation = element.animate(
      [{ opacity: 0.55 }, { opacity: 1 }],
      { duration: 320, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' },
    )
    const cancel = () => animation.cancel()
    const onVisibility = () => { if (document.visibilityState === 'hidden') cancel() }
    preference.addEventListener('change', cancel)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      cancel()
      preference.removeEventListener('change', cancel)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [ref, value])
}
