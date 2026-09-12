import { useEffect, useRef, useState } from 'react'

/** Interruptible interpolation; each new target starts at the currently drawn value. */
export function useAnimatedNumber(value: number, enabled = true, duration = 250): number {
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
    if (!enabled || reducedMotion || duration <= 0 || !Number.isFinite(value)) {
      current.current = value
      setDisplayed(value)
      return
    }

    const from = current.current
    if (from === value) return

    let frame: number
    let started: number | undefined
    const draw = (time: number) => {
      started ??= time
      const progress = Math.min((time - started) / duration, 1)
      const eased = 1 - (1 - progress) ** 3
      current.current = progress === 1 ? value : from + (value - from) * eased
      setDisplayed(current.current)
      if (progress < 1) frame = window.requestAnimationFrame(draw)
    }
    frame = window.requestAnimationFrame(draw)
    return () => window.cancelAnimationFrame(frame)
  }, [value, enabled, duration, reducedMotion])

  return !enabled || reducedMotion ? value : displayed
}
