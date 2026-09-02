import React, { useEffect, useRef, useState } from 'react'
import { api, formatTs } from './api.js'
import {
  Badge, Confirm, EmptyState, Sparkline, Switch, fareBadge, statusBadge, usePolling, useToasts,
} from './components.jsx'

const TABS = [
  { id: 'overview', label: '总览' },
  { id: 'tasks', label: '任务' },
  { id: 'tickets', label: '票据管理' },
  { id: 'settings', label: '设置' },
  { id: 'history', label: '历史日志' },
]

/* ================= 总览 ================= */
function Overview({ data, onGo, onToggleStatus, push }) {
  const cfg = data.config
  const stats = data.stats
  const hits = (data.history || []).filter(
    (r) => Number(r.price) <= 500
  ).slice(0, 5)

  return (
    <div>
      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-label">监控状态</div>
          <div className="stat-value" style={{ color: cfg.status === 'running' ? 'var(--color-success)' : 'var(--color-text-secondary)' }}>
            {cfg.status === 'running' ? '运行中' : '已停止'}
          </div>
          <div className="stat-hint">daemon 按监控时段轮询抓价</div>
          <div className="stat-action">
            <button
              className={`btn ${cfg.status === 'running' ? 'btn-secondary' : 'btn-primary'}`}
              onClick={() => onToggleStatus()}
            >
              {cfg.status === 'running' ? '停止监控' : '启动监控'}
            </button>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">监控任务</div>
          <div className="stat-value">{stats.task_count}</div>
          <div className="stat-hint">启用 {stats.enabled_count} 个</div>
          <div className="stat-action">
            <button className="btn btn-text btn-sm" onClick={() => onGo('tasks')}>管理任务 →</button>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">近 24h 命中</div>
          <div className="stat-value" style={{ color: 'var(--color-low-price)' }}>{stats.hit_count_24h}</div>
          <div className="stat-hint">价格 ≤ 目标价记录数</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">历史记录</div>
          <div className="stat-value">{stats.history_count}</div>
          <div className="stat-hint">最近 50 条可查</div>
          <div className="stat-action">
            <button className="btn btn-text btn-sm" onClick={() => onGo('history')}>查看历史 →</button>
          </div>
        </div>
      </div>

      {!cfg.plus_ticket.configured && (
        <div className="banner">
          <span>⚠️ PLUS 票据未配置，PLUS 任务将无法查询。</span>
          <a onClick={() => onGo('tickets')}>去配置 →</a>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <div>
            <div className="card-title">最近低价命中</div>
            <div className="card-sub">价格历史中 ≤ ¥500 的记录（最近 5 条）</div>
          </div>
          <button className="btn btn-text btn-sm" onClick={() => onGo('history')}>全部历史 →</button>
        </div>
        {hits.length === 0 ? (
          <EmptyState
            icon="🛬"
            title="暂无低价命中"
            desc="监控运行后，命中目标价的任务会出现在这里，并推送微信。"
          />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>时间</th><th>航线</th><th>日期</th><th>类型</th><th>航班</th><th className="num">价格(元)</th>
                </tr>
              </thead>
              <tbody>
                {hits.map((r, i) => (
                  <tr key={i} className="hit-row">
                    <td>{formatTs(r.ts)}</td>
                    <td>{r.from} → {r.to}</td>
                    <td>{r.date}</td>
                    <td>{fareBadge(r.fare_type)}</td>
                    <td><span className="mono">{r.flight}</span></td>
                    <td className="num" style={{ color: 'var(--color-low-price)', fontWeight: 700 }}>¥{r.price}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

/* ================= 任务 ================= */
function Tasks({ data, onChanged, push }) {
  const [date, setDate] = useState('')
  const [fromCity, setFromCity] = useState('')
  const [toCity, setToCity] = useState('')
  const [price, setPrice] = useState(199)
  const [fareType, setFareType] = useState('normal')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmDel, setConfirmDel] = useState(null)
  const [fromTips, setFromTips] = useState([])
  const [toTips, setToTips] = useState([])

  const tasks = data.tasks || []
  const cfg = data.config

  const updateTips = async (kind, value) => {
    const setter = kind === 'from' ? setFromTips : setToTips
    if (!value.trim()) { setter([]); return }
    try {
      const r = await api.resolveCity(value)
      setter(r.tips || [])
    } catch { setter([]) }
  }

  const submit = async () => {
    setError('')
    if (!date) { setError('请先选择日期。'); return }
    if (!fromCity.trim()) { setError('请先填写出发地。'); return }
    if (!toCity.trim()) { setError('请先填写到达地。'); return }
    if (fareType === 'plus' && !cfg.plus_ticket.configured) {
      setError('你选择了 PLUS专享，但尚未配置 PLUS 票据，请先到「票据管理」粘贴抓包 cURL。')
      return
    }
    setBusy(true)
    try {
      const r = await api.addTask({ date, from_city: fromCity, to_city: toCity, target_price: price, fare_type: fareType })
      push('success', r.message || '任务已添加')
      setFromCity(''); setToCity(''); setError('')
      onChanged()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const toggleEnabled = async (t) => {
    await api.setTaskEnabled(t.id, !t.enabled)
    onChanged()
  }

  const doDelete = async () => {
    if (!confirmDel) return
    await api.deleteTask(confirmDel.id)
    push('info', `任务已删除：${confirmDel.from_code} → ${confirmDel.to_code}`)
    setConfirmDel(null)
    onChanged()
  }

  // 根据任务聚合价格历史做迷你趋势
  const sparkValues = (task) => {
    const vals = (data.history || [])
      .filter((r) => r.date === task.date && r.from.includes(task.from_code) || (r.date + r.from + r.to).includes(task.from_code + task.to_code))
      .map((r) => Number(r.price))
    return vals.slice(-7)
  }
  const isHit = (task) => {
    const vals = (data.history || []).filter((r) => String(r.date) === String(task.date) && r.from.includes(task.from_code) && r.to.includes(task.to_code))
    return vals.some((r) => Number(r.price) <= Number(task.target_price))
  }

  return (
    <div>
      <div className="card">
        <div className="card-head">
          <div>
            <div className="card-title">添加监控任务</div>
            <div className="card-sub">出发/到达支持中文城市名，会自动匹配三字码</div>
          </div>
        </div>
        <div className="form-row">
          <div className="field">
            <label>日期</label>
            <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div className="field">
            <label>出发地</label>
            <input className="input" placeholder="例如：深圳 / SZX" value={fromCity} onChange={(e) => { setFromCity(e.target.value); updateTips('from', e.target.value) }} />
            {fromTips.length > 0 && (
              <div className="field-hint">候选：{fromTips.join(' | ')}</div>
            )}
          </div>
          <div className="field">
            <label>到达地</label>
            <input className="input" placeholder="例如：乌鲁木齐 / URC" value={toCity} onChange={(e) => { setToCity(e.target.value); updateTips('to', e.target.value) }} />
            {toTips.length > 0 && (
              <div className="field-hint">候选：{toTips.join(' | ')}</div>
            )}
          </div>
          <div className="field">
            <label>提醒阈值(元)</label>
            <input className="input" type="number" min="1" value={price} onChange={(e) => setPrice(Number(e.target.value))} />
          </div>
          <div className="field">
            <label>票价类型</label>
            <div className="segmented">
              <button className={fareType === 'normal' ? 'active' : ''} onClick={() => setFareType('normal')}>普通票价</button>
              <button className={fareType === 'plus' ? 'active' : ''} onClick={() => setFareType('plus')}>PLUS专享</button>
            </div>
          </div>
          <button className="btn btn-primary" disabled={busy} onClick={submit}>添加任务</button>
        </div>
        {error && <div className="field-error" style={{ marginTop: 10 }}>{error}</div>}
      </div>

      <div className="section-gap">
        {tasks.length === 0 ? (
          <div className="card">
            <EmptyState
              icon="🗒️"
              title="还没有监控任务"
              desc="添加后 daemon 会按你设定的阈值轮询，命中即推送微信。"
              action={<button className="btn btn-primary" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>添加第一个任务</button>}
            />
          </div>
        ) : (
          <div className="task-grid">
            {tasks.map((t) => {
              const hit = isHit(t)
              return (
                <div className="task-card" key={t.id}>
                  <div className="task-card-head">
                    <div>
                      <div className="task-route">
                        {t.from_code} <span style={{ color: 'var(--color-text-tertiary)' }}>→</span> {t.to_code}
                      </div>
                      <div className="task-meta">{t.from_city} → {t.to_city}</div>
                      <div className="task-meta">{t.date}</div>
                    </div>
                    <Switch checked={t.enabled} onChange={() => toggleEnabled(t)} label="" />
                  </div>
                  <div className="row" style={{ marginTop: 8 }}>
                    {fareBadge(t.fare_type)}
                    {hit ? <Badge kind="pink">低价命中</Badge> : <Badge kind="gray">监控中</Badge>}
                  </div>
                  <div className="task-body">
                    <span className="task-target">目标 ≤ ¥{t.target_price}</span>
                    {hit && <span className="task-price hit">≤ 目标价</span>}
                  </div>
                  <div className="task-spark">
                    <Sparkline values={sparkValues(t)} hit={hit} />
                  </div>
                  <div className="task-foot">
                    <span>最近 {Math.min(sparkValues(t).length, 7)} 次价格</span>
                    <button className="btn btn-icon danger" title="删除任务" onClick={() => setConfirmDel(t)}>🗑</button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {confirmDel && (
        <Confirm
          title={`删除任务 ${confirmDel.from_code} → ${confirmDel.to_code}`}
          message="删除后 daemon 将不再查询该任务，价格历史保留。此操作不可撤销。"
          onCancel={() => setConfirmDel(null)}
          onConfirm={doDelete}
        />
      )}
    </div>
  )
}

/* ================= 票据管理 ================= */
function TicketPanel({ title, fareType, ticket, push, onChanged }) {
  const [raw, setRaw] = useState('')
  const [revealed, setRevealed] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    setError('')
    if (!raw.trim()) { setError('内容为空，未保存。'); return }
    setBusy(true)
    try {
      const r = await api.saveTicket(fareType, raw)
      if (!r.ok) { setError(r.error || '保存失败'); return }
      push('success', `${title} 票据已保存，daemon 下一轮自动生效`)
      setRaw('')
      onChanged()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const reveal = async () => {
    if (revealed) { setRevealed(''); return }
    try {
      const r = await api.ticketRaw(fareType)
      setRevealed(r.raw || '')
      setTimeout(() => setRevealed(''), 30000)
    } catch (e) {
      push('error', e.message)
    }
  }

  const checks = [
    { label: '请求地址', ok: ticket.configured },
    { label: '请求体（--data-raw）', ok: ticket.configured && ticket.length > 0 },
  ]

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">{title}</div>
          <div className="card-sub">抓包方法：官网查询页 F12 → Network → 右键请求 → Copy as cURL (bash)</div>
        </div>
        {ticket.configured && <Badge kind="green">已配置</Badge>}
      </div>

      <div className="ticket-status">
        <span className="muted">当前：</span>
        {ticket.configured ? (
          <>
            <span className="ticket-url">{ticket.url || '(已配置)'}</span>
            <button className="btn btn-text btn-sm" onClick={reveal}>
              {revealed ? '收起明文' : '查看明文'}
            </button>
            {revealed && (
              <details className="ticket-url" style={{ whiteSpace: 'pre-wrap', fontFamily: 'var(--font-mono)' }}>
                <summary>展开 cURL（30 秒后自动收起）</summary>
                <pre style={{ maxHeight: 240, overflow: 'auto', fontSize: 11 }}>{revealed}</pre>
              </details>
            )}
          </>
        ) : (
          <Badge kind="yellow">未配置 · PLUS 任务将无法查询</Badge>
        )}
        <span className="muted">{(ticket.length / 1024).toFixed(1)} KB</span>
      </div>

      <div className="check-list">
        {checks.map((c, i) => (
          <div key={i} className={`check-item ${c.ok ? 'ok' : 'bad'}`}>
            {c.ok ? '✓' : '✗'} {c.label}
          </div>
        ))}
      </div>

      <textarea
        className="input"
        rows={7}
        placeholder="在此粘贴完整 cURL 命令（含 Cookie、token、hnairSign）…"
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
      />
      {error && <div className="field-error" style={{ marginTop: 8 }}>{error}</div>}
      <div className="row" style={{ marginTop: 12, justifyContent: 'flex-end' }}>
        <button className="btn btn-secondary" onClick={() => setRaw('')}>清空</button>
        <button className="btn btn-primary" disabled={busy} onClick={save}>保存票据</button>
      </div>
    </div>
  )
}

function Tickets({ data, onChanged, push }) {
  return (
    <div>
      <TicketPanel
        title="PLUS 专享票据"
        fareType="plus"
        ticket={data.config.plus_ticket}
        push={push}
        onChanged={onChanged}
      />
      <TicketPanel
        title="普通票价票据"
        fareType="normal"
        ticket={data.config.normal_ticket}
        push={push}
        onChanged={onChanged}
      />
      <div className="card">
        <div className="card-sub">PLUS 通道抓 <span className="mono">ffl/airLowFareSearch</span>，普通票价抓 <span className="mono">airLowFareSearch</span>，两份要各自抓取。票据保存后 daemon 下一轮自动生效，无需重启。</div>
      </div>
    </div>
  )
}

/* ================= 设置 ================= */
function Settings({ data, onChanged, push }) {
  const cfg = data.config
  const [keysRaw, setKeysRaw] = useState('')
  const [testMsg, setTestMsg] = useState('')
  const [start, setStart] = useState(cfg.monitor_window?.start || '07:00')
  const [end, setEnd] = useState(cfg.monitor_window?.end || '23:00')
  const [proxy, setProxy] = useState(cfg.proxy || '')
  const [signRefresh, setSignRefresh] = useState(cfg.sign_refresh || false)
  const [busy, setBusy] = useState('')

  useEffect(() => {
    setStart(cfg.monitor_window?.start || '07:00')
    setEnd(cfg.monitor_window?.end || '23:00')
    setProxy(cfg.proxy || '')
    setSignRefresh(cfg.sign_refresh || false)
  }, [cfg])

  const saveKeys = async () => {
    setBusy('keys')
    try {
      await api.saveSendKeys(keysRaw)
      push('success', 'SendKey 已保存，daemon 下一轮生效')
      setKeysRaw('')
      onChanged()
    } catch (e) { push('error', e.message) } finally { setBusy('') }
  }

  const testAlert = async () => {
    const keys = keysRaw.split('\n').map((s) => s.trim()).filter(Boolean)
    if (keys.length === 0 && cfg.send_keys_count === 0) {
      push('error', '请先填写至少 1 个 SendKey')
      return
    }
    setBusy('test')
    try {
      const r = await api.testAlert(keys.length ? keys : undefined)
      if (r.success > 0) {
        setTestMsg(`测试消息发送完成：成功 ${r.success} / 总计 ${r.total}`)
        push('success', `测试消息：成功 ${r.success} / ${r.total}`)
      } else {
        setTestMsg('测试消息发送失败，请检查 SendKey 是否正确')
        push('error', '测试消息发送失败')
      }
    } catch (e) { push('error', e.message) } finally { setBusy('') }
  }

  const saveWindow = async () => {
    setBusy('window')
    try {
      await api.saveMonitorWindow(start, end)
      push('success', `监控时段已保存：${start} - ${end}`)
    } catch (e) { push('error', e.message) } finally { setBusy('') }
  }

  const saveProxy = async () => {
    setBusy('proxy')
    try { await api.saveProxy(proxy); push('success', '代理设置已保存') }
    catch (e) { push('error', e.message) } finally { setBusy('') }
  }

  const toggleSignRefresh = async (v) => {
    setSignRefresh(v)
    try {
      await api.saveSignRefresh(v)
      push('success', v ? '已开启签名刷新：每轮刷新 stime 并重签，失败自动回退' : '已关闭签名刷新')
    } catch (e) { push('error', e.message) }
  }

  return (
    <div>
      <div className="card">
        <div className="card-head">
          <div>
            <div className="card-title">微信推送配置</div>
            <div className="card-sub">Server酱 SendKey，每行一个（多个微信号可各配一个）</div>
          </div>
          <div className="row">
            {cfg.send_keys.map((k, i) => <Badge key={i} kind={k.includes('****') ? 'blue' : 'gray'}>{k}</Badge>)}
            <span className="muted">共 {cfg.send_keys_count} 个</span>
          </div>
        </div>
        <textarea className="input" rows={4} placeholder="每行粘贴一个 SendKey，例如 SCT..." value={keysRaw} onChange={(e) => setKeysRaw(e.target.value)} />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn btn-primary" disabled={busy === 'keys'} onClick={saveKeys}>保存并验证</button>
          <button className="btn btn-secondary" disabled={busy === 'test'} onClick={testAlert}>发送测试消息</button>
        </div>
        {testMsg && <div className="field-hint" style={{ marginTop: 8 }}>{testMsg}</div>}
      </div>

      <div className="card">
        <div className="card-head">
          <div>
            <div className="card-title">每日监控时段</div>
            <div className="card-sub">仅在该时段内轮询抓价，时段外 daemon 待机</div>
          </div>
        </div>
        <div className="form-row">
          <div className="field">
            <label>开始时间</label>
            <input className="input" type="time" value={start} onChange={(e) => setStart(e.target.value)} />
          </div>
          <div className="field">
            <label>结束时间</label>
            <input className="input" type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
          </div>
          <button className="btn btn-primary" disabled={busy === 'window'} onClick={saveWindow}>保存时段</button>
        </div>
        <div className="field-hint" style={{ marginTop: 8 }}>跨天请设置例如 22:00 到 07:00。</div>
      </div>

      <div className="card">
        <div className="card-head">
          <div>
            <div className="card-title">高级设置</div>
            <div className="card-sub">代理与签名刷新</div>
          </div>
        </div>
        <Switch
          checked={signRefresh}
          onChange={toggleSignRefresh}
          label="签名刷新（stime + 重签）"
          desc="开启后每轮刷新 common.stime 并重签；遇验签错误自动回退原签名抓包。建议稳定运行几天后再开。"
        />
        <div style={{ height: 12 }} />
        <div className="field">
          <label>HTTP(S) 代理（可选）</label>
          <input className="input" placeholder="例如 http://127.0.0.1:7890，留空表示直连" value={proxy} onChange={(e) => setProxy(e.target.value)} />
        </div>
        <div className="row" style={{ marginTop: 12, justifyContent: 'flex-end' }}>
          <button className="btn btn-primary" disabled={busy === 'proxy'} onClick={saveProxy}>保存代理</button>
        </div>
      </div>
    </div>
  )
}

/* ================= 历史日志 ================= */
function History({ data }) {
  const [autoRefresh, setAutoRefresh] = useState(false)
  return (
    <div>
      <div className="card">
        <div className="card-head">
          <div>
            <div className="card-title">价格历史（最近 50 条）</div>
            <div className="card-sub">命中行高亮显示</div>
          </div>
        </div>
        {(data.history || []).length === 0 ? (
          <EmptyState icon="📊" title="暂无价格历史" desc="daemon 抓到价格后才会写入。" />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>时间</th><th>日期</th><th>航线</th><th>类型</th><th>航班</th><th className="num">价格(元)</th>
                </tr>
              </thead>
              <tbody>
                {(data.history || []).map((r, i) => {
                  const isLow = Number(r.price) <= 500
                  return (
                    <tr key={i} className={isLow ? 'hit-row' : ''}>
                      <td>{formatTs(r.ts)}</td>
                      <td>{r.date}</td>
                      <td>{r.from} → {r.to}</td>
                      <td>{fareBadge(r.fare_type)}</td>
                      <td><span className="mono">{r.flight}</span></td>
                      <td className="num" style={{ fontWeight: isLow ? 700 : 400, color: isLow ? 'var(--color-low-price)' : undefined }}>{r.price}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <div>
            <div className="card-title">运行日志（最近 200 行）</div>
            <div className="card-sub">daemon 抓价循环输出</div>
          </div>
          <label className="row" style={{ fontSize: 13, cursor: 'pointer' }}>
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
            <span>自动刷新</span>
          </label>
        </div>
        <div className="log-view">
          {(data.logs || []).map((line, i) => {
            const cls = line.includes('错误') || line.includes('[阻断]') || line.includes('E00001') ? 'err'
              : line.includes('警告') || line.includes('退避') ? 'warn' : ''
            return <div key={i} className={cls}>{line}</div>
          })}
          {(data.logs || []).length === 0 && <div className="muted">暂无日志输出。</div>}
        </div>
      </div>
    </div>
  )
}

/* ================= App ================= */
export default function App() {
  const [tab, setTab] = useState('overview')
  const { toastNode, push } = useToasts()
  const { data, loading, error, refresh } = usePolling(api.state, tab === 'history' ? 30000 : 15000)

  const toggleStatus = async () => {
    if (!data) return
    const next = data.config.status === 'running' ? 'stopped' : 'running'
    try {
      await api.setStatus(next)
      push('success', next === 'running' ? '监控已启动' : '监控已停止')
      refresh()
    } catch (e) { push('error', e.message) }
  }

  if (loading && !data) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
        <span className="muted">加载中…</span>
      </div>
    )
  }
  if (error && !data) {
    return (
      <div style={{ maxWidth: 560, margin: '80px auto', textAlign: 'center' }}>
        <h2 style={{ marginBottom: 8 }}>无法连接控制台 API</h2>
        <p className="muted">{error}</p>
        <button className="btn btn-primary" style={{ marginTop: 16 }} onClick={refresh}>重试</button>
      </div>
    )
  }

  return (
    <>
      <header className="topnav">
        <div className="topnav-left">
          <div className="topnav-logo"><span className="plane">✈️</span> 海航监控</div>
        </div>
        <div className="topnav-right">
          <button
            className={`status-pill ${data.config.status === 'running' ? 'running' : 'stopped'}`}
            onClick={toggleStatus}
            title={data.config.status === 'running' ? '点击停止监控' : '点击启动监控'}
          >
            <span className="dot" />
            {data.config.status === 'running' ? '监控运行中' : '监控已停止'}
          </button>
          <button className="btn btn-primary" onClick={() => setTab('tasks')}>+ 添加任务</button>
        </div>
      </header>

      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={`tab ${tab === t.id ? 'active' : ''}`} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </nav>

      <main className="page">
        {tab === 'overview' && <Overview data={data} onGo={setTab} onToggleStatus={toggleStatus} push={push} />}
        {tab === 'tasks' && <Tasks data={data} onChanged={refresh} push={push} />}
        {tab === 'tickets' && <Tickets data={data} onChanged={refresh} push={push} />}
        {tab === 'settings' && <Settings data={data} onChanged={refresh} push={push} />}
        {tab === 'history' && <History data={data} />}
      </main>

      {toastNode}
    </>
  )
}