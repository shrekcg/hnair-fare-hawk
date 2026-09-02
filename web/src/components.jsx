import React, { useEffect, useState } from 'react'

/* ---------- 状态徽章 ---------- */
export function Badge({ kind = 'gray', children }) {
  return (
    <span className={`badge ${kind}`}>
      <span className="dot" />
      {children}
    </span>
  )
}

export function fareBadge(fareType) {
  return fareType === 'plus' ? <Badge kind="blue">PLUS专享</Badge> : <Badge kind="gray">普通票价</Badge>
}

export function statusBadge(status) {
  if (status === 'running') return <Badge kind="green">运行中</Badge>
  return <Badge kind="gray">已停止</Badge>
}

/* ---------- 开关 ---------- */
export function Switch({ checked, onChange, label, desc }) {
  return (
    <div className="switch-row">
      <div>
        <div className="switch-label">{label}</div>
        {desc && <div className="switch-desc">{desc}</div>}
      </div>
      <label className="switch">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span className="slider" />
      </label>
    </div>
  )
}

/* ---------- 空状态 ---------- */
export function EmptyState({ icon = '📭', title, desc, action }) {
  return (
    <div className="empty">
      <div className="icon">{icon}</div>
      <div className="title">{title}</div>
      <div className="desc">{desc}</div>
      {action}
    </div>
  )
}

/* ---------- 确认弹窗 ---------- */
export function Confirm({ title, message, onCancel, onConfirm }) {
  return (
    <div className="confirm-overlay" onClick={onCancel}>
      <div className="confirm-box" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        <p>{message}</p>
        <div className="confirm-actions">
          <button className="btn btn-secondary" onClick={onCancel}>取消</button>
          <button className="btn btn-danger" onClick={onConfirm}>确认删除</button>
        </div>
      </div>
    </div>
  )
}

/* ---------- 迷你趋势图 ---------- */
export function Sparkline({ values = [], hit }) {
  if (!values || values.length < 2) return <div className="muted">暂无趋势数据</div>
  const w = 280
  const h = 40
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w
    const y = h - 4 - ((v - min) / range) * (h - 8)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const last = pts[pts.length - 1]
  const stroke = hit ? 'var(--color-low-price)' : 'var(--color-primary)'
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="spark-svg" preserveAspectRatio="none">
      <polyline
        points={pts.join(' ')}
        fill="none"
        stroke={stroke}
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={last.split(',')[0]} cy={last.split(',')[1]} r="3" fill={stroke} />
    </svg>
  )
}

/* ---------- Toast 系统 ---------- */
export function useToasts() {
  const [toasts, setToasts] = useState([])
  const push = (type, text) => {
    const id = Date.now() + Math.random()
    setToasts((prev) => [...prev, { id, type, text }])
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3200)
  }
  const toastNode = (
    <div className="toast-wrap">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.type}`}>{t.text}</div>
      ))}
    </div>
  )
  return { toastNode, push }
}

/* ---------- 15s 轮询 Hook ---------- */
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