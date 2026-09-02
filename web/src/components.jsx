import React, { useEffect, useState } from 'react'

/* ---------- 迷你趋势图（自绘 SVG，无第三方图标依赖） ---------- */
export function Sparkline({ values = [], hit, width = 200, height = 36 }) {
  if (!values || values.length < 2) return <span className="muted">—</span>
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * width
    const y = height - 4 - ((v - min) / range) * (height - 8)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const last = pts[pts.length - 1]
  const stroke = hit ? '#ff4d6a' : '#1677ff'
  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width, height, display: 'block' }} preserveAspectRatio="none">
      <polyline
        points={pts.join(' ')}
        fill="none"
        stroke={stroke}
        strokeWidth="1.8"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={last.split(',')[0]} cy={last.split(',')[1]} r="2.6" fill={stroke} />
    </svg>
  )
}

/* ---------- 轮询 Hook ---------- */
export function usePolling(fn, interval = 15000, deps = []) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const d = await fn()
        if (!cancelled) {
          setData(d)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    const timer = setInterval(load, interval)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps])

  return { data, loading, error, refresh: () => setTick((t) => t + 1) }
}