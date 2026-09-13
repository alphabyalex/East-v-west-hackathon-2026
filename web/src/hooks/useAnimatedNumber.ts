import { useEffect, useRef, useState } from 'react'

/** Presentation only: interruptible, monotonic easing with no work left at rest. */
export function useAnimatedNumber(value: number, enabled = true, duration = 320): number {
  const [displayed, setDisplayed] = useState(value)
  const current = useRef(value)
  const [reducedMotion, setReducedMotion] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )

  useEffect(() => {
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReducedMotion(preference.matches)
    preference.addEventListener('change', update)
    return () => preference.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    const settle = () => {
      current.current = value
      setDisplayed(value)
    }
    if (!enabled || reducedMotion || document.hidden || duration <= 0
      || !Number.isFinite(duration) || !Number.isFinite(value) || !Number.isFinite(current.current)) {
      settle()
      return
    }

    const from = current.current
    if (from === value) return

    // RAF timestamps share performance.now()'s clock. Starting here matters: a
    // slider may supply another target every frame, before a first-frame clock
    // would ever advance. Always retarget from the number already on screen.
    const started = performance.now()
    let frame: number | undefined
    let cancelled = false
    const draw = (time: number) => {
      if (cancelled) return
      const progress = Math.max(0, Math.min((time - started) / duration, 1))
      const eased = 1 - (1 - progress) ** 3
      current.current = progress === 1 ? value : from + (value - from) * eased
      setDisplayed(current.current)
      if (progress < 1) frame = window.requestAnimationFrame(draw)
      else {
        frame = undefined
        document.removeEventListener('visibilitychange', onVisibilityChange)
      }
    }
    const onVisibilityChange = () => {
      if (!document.hidden) return
      cancelled = true
      if (frame !== undefined) window.cancelAnimationFrame(frame)
      frame = undefined
      settle()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    frame = window.requestAnimationFrame(draw)
    return () => {
      cancelled = true
      if (frame !== undefined) window.cancelAnimationFrame(frame)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [value, enabled, duration, reducedMotion])

  return !enabled || reducedMotion ? value : displayed
}
