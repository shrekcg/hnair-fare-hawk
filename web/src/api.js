// 与 web_api.py 通信的轻量客户端
const BASE = ''

async function request(path, options = {}) {
  const resp = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok && !data.ok) {
    throw new Error(data.error || `请求失败 (${resp.status})`)
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
  setTaskEnabled: (id, enabled) =>
    request('/api/tasks/enabled', { method: 'POST', body: JSON.stringify({ id, enabled }) }),
  deleteTask: (id) => request('/api/tasks/delete', { method: 'POST', body: JSON.stringify({ id }) }),
  saveSendKeys: (raw) => request('/api/send_keys', { method: 'POST', body: JSON.stringify({ raw }) }),
  testAlert: (keys) => request('/api/test_alert', { method: 'POST', body: JSON.stringify({ keys }) }),
  saveMonitorWindow: (start, end) =>
    request('/api/monitor_window', { method: 'POST', body: JSON.stringify({ start, end }) }),
  saveProxy: (proxy) => request('/api/proxy', { method: 'POST', body: JSON.stringify({ proxy }) }),
  saveSignRefresh: (enabled) =>
    request('/api/sign_refresh', { method: 'POST', body: JSON.stringify({ enabled }) }),
  saveTicket: (fareType, raw) =>
    request('/api/ticket', { method: 'POST', body: JSON.stringify({ fare_type: fareType, raw }) }),
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