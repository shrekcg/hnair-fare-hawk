// 与 web_api.py 通信的轻量客户端
const BASE = ''

async function request(path, options = {}) {
  const resp = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok && !data.ok) {
    const err = new Error(data.error || `请求失败 (${resp.status})`)
    err.status = resp.status
    err.retry_after = data.retry_after
    throw err
  }
  return data
}

export const api = {
  state: () => request('/api/state'),
  cityOptions: () => request('/api/city/options'),
  resolveCity: (q) => request(`/api/city/resolve?q=${encodeURIComponent(q)}`),
  ticketRaw: (fareType) => request(`/api/ticket/raw?fare_type=${fareType}`),

  setStatus: (status) => request('/api/status', { method: 'POST', body: JSON.stringify({ status }) }),
  addTask: (payload) => request('/api/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  addTasksBatch: (items) => request('/api/tasks/batch', { method: 'POST', body: JSON.stringify({ items }) }),
  setTaskEnabled: (ids, enabled) =>
    request('/api/tasks/enabled', { method: 'POST', body: JSON.stringify({ ids: Array.isArray(ids) ? ids : [ids], enabled }) }),
  deleteTask: (ids) => request('/api/tasks/delete', { method: 'POST', body: JSON.stringify({ ids: Array.isArray(ids) ? ids : [ids] }) }),
  updateTasks: (ids, payload) =>
    request('/api/tasks/update', { method: 'POST', body: JSON.stringify({ ids: Array.isArray(ids) ? ids : [ids], ...payload }) }),
  saveSendKeys: (raw) => request('/api/send_keys', { method: 'POST', body: JSON.stringify({ raw }) }),
  testAlert: (keys) => request('/api/test_alert', { method: 'POST', body: JSON.stringify({ keys }) }),
  saveMonitorWindow: (start, end) =>
    request('/api/monitor_window', { method: 'POST', body: JSON.stringify({ start, end }) }),
  saveProxy: (proxy) => request('/api/proxy', { method: 'POST', body: JSON.stringify({ proxy }) }),
  saveSignRefresh: (enabled) =>
    request('/api/sign_refresh', { method: 'POST', body: JSON.stringify({ enabled }) }),
  savePriceQuery: (enabled, minInterval) =>
    request('/api/price_query', { method: 'POST', body: JSON.stringify({ enabled, min_interval: minInterval }) }),
  savePolling: (polling) =>
    request('/api/polling', { method: 'POST', body: JSON.stringify(polling) }),
  saveTicket: (fareType, raw) =>
    request('/api/ticket', { method: 'POST', body: JSON.stringify({ fare_type: fareType, raw }) }),
  saveFeishu: (payload) => request('/api/feishu', { method: 'POST', body: JSON.stringify(payload) }),
  testFeishu: (payload) => request('/api/feishu/test', { method: 'POST', body: JSON.stringify(payload) }),
  saveNotifyChannels: (payload) => request('/api/notify/save', { method: 'POST', body: JSON.stringify(payload) }),
  testNotifyChannel: (channel) => request('/api/notify/test', { method: 'POST', body: JSON.stringify({ channel }) }),
  clearLog: () => request('/api/log/clear', { method: 'POST', body: JSON.stringify({}) }),
  clearNotifyHistory: () => request('/api/notify_history/clear', { method: 'POST', body: JSON.stringify({}) }),
  clearPriceHistory: () => request('/api/price_history/clear', { method: 'POST', body: JSON.stringify({}) }),
  flightMeta: () => request('/api/flights/meta'),
  flightOptions: (params) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''),
    ).toString()
    return request(`/api/flights/options?${qs}`)
  },
  flightQuery: (params) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''),
    ).toString()
    return request(`/api/flights/query?${qs}`)
  },
  flightPrices: (params) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''),
    ).toString()
    return request(`/api/flights/prices?${qs}`)
  },
}

export function formatTs(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) {
    // 已是字符串时间（后台写的是 ISO 字符串）
    return String(ts).replace('T', ' ').slice(0, 16)
  }
  return d.toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}