import React, { useEffect, useRef, useState } from 'react'
import {
  Alert, App as AntApp, Button, Card, Col, Collapse, ConfigProvider, DatePicker, Descriptions, Dropdown, Empty, Form, Input,
  InputNumber, Layout, Menu, Modal, Popconfirm, Popover, Radio, Result, Row, Segmented, Select, Space, Spin,
  Statistic, Steps, Switch, Table, Tag, TimePicker, Tooltip, Typography,
} from 'antd'
import {
  CalendarIcon, ChevronLeftIcon, ChevronRightIcon, CompassIcon, DeleteIcon, EditIcon, HistoryIcon, MonitorIcon,
  NotifyIcon, OverviewIcon, PlusIcon, PowerIcon, QuestionIcon, SendIcon, SettingsIcon, TasksIcon, BrandIcon,
} from './icons.jsx'
import dayjs from 'dayjs'
import { api, formatTs } from './api.js'
import { usePolling } from './components.jsx'
import { cityWithProvince } from './cityProvince.js'

const TABS = [
  { key: 'overview', icon: <OverviewIcon />, label: '总览' },
  { key: 'tasks', icon: <TasksIcon />, label: '监控任务' },
  { key: 'flights', icon: <CompassIcon />, label: '航线查询' },
  { key: 'notify', icon: <NotifyIcon />, label: '通知管理' },
  { key: 'monitor', icon: <MonitorIcon />, label: '监控管理' },
  { key: 'history', icon: <HistoryIcon />, label: '历史日志' },
]

/* ---------- 通用辅助 ---------- */
function shortCity(label) {
  return String(label || '').split('（')[0] || label
}

function fareTag(fareType) {
  return fareType === 'plus' ? <Tag color="blue">PLUS专享</Tag> : <Tag>普通票价</Tag>
}

function HitTag({ hit }) {
  return hit ? <Tag color="magenta">低价命中</Tag> : <Tag>监控中</Tag>
}

/* ================= 总览 ================= */
function Overview({ data, onGo, onToggleStatus }) {
  const cfg = data.config
  const stats = data.stats
  const running = cfg.status === 'running'
  const hits = (data.history || []).filter((r) => Number(r.price) <= 500).slice(0, 10)

  const columns = [
    { title: '时间', dataIndex: 'ts', width: 160, render: (v) => formatTs(v) },
    { title: '航线', key: 'route', render: (_, r) => `${shortCity(r.from)} → ${shortCity(r.to)}` },
    { title: '日期', dataIndex: 'date', width: 110 },
    { title: '类型', dataIndex: 'fare_type', width: 100, render: fareTag },
    { title: '航班', dataIndex: 'flight', width: 100, render: (v) => <span className="mono">{v}</span> },
    {
      title: '价格(元)', dataIndex: 'price', width: 100, align: 'right',
      render: (v) => <span style={{ color: 'var(--color-low-price)', fontWeight: 700 }}>¥{v}</span>,
    },
  ]

  return (
    <div>
      <Row gutter={[16, 16]} align="stretch">
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card">
            <Statistic
              title="监控状态"
              value={running ? '运行中' : '已停止'}
              valueStyle={{ color: running ? 'var(--color-success)' : '#8c8c8c' }}
            />
            <div className="muted" style={{ margin: '4px 0 12px' }}>daemon 按监控时段轮询抓价</div>
            <Button
              type={running ? 'default' : 'primary'}
              icon={running ? <PowerIcon /> : undefined}
              style={{ marginTop: 'auto' }}
              onClick={onToggleStatus}
            >
              {running ? '停止监控' : '启动监控'}
            </Button>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card">
            <Statistic title="监控任务" value={stats.task_count} />
            <div className="muted" style={{ margin: '4px 0 12px' }}>启用 {stats.enabled_count} 个</div>
            <Button type="default" style={{ marginTop: 'auto' }} onClick={() => onGo('tasks')}>管理任务</Button>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card">
            <Statistic title="近 24h 命中" value={stats.hit_count_24h} valueStyle={{ color: 'var(--color-low-price)' }} />
            <div className="muted" style={{ margin: '4px 0 12px' }}>价格 ≤ 目标价的记录数</div>
            <div className="stat-card-footer" />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card">
            <Statistic title="历史记录" value={stats.history_count} />
            <div className="muted" style={{ margin: '4px 0 12px' }}>最近 50 条可查</div>
            <Button type="default" style={{ marginTop: 'auto' }} onClick={() => onGo('history')}>查看历史</Button>
          </Card>
        </Col>
      </Row>

      {!cfg.plus_ticket.configured && !cfg.normal_ticket.configured && (
        <Alert
          style={{ marginTop: 16 }}
          type="warning"
          showIcon
          message="尚未配置任何抓包票据，监控任务将无法查询。"
          action={<Button size="small" type="link" onClick={() => onGo('monitor')}>去配置</Button>}
        />
      )}

      <Card
        style={{ marginTop: 16 }}
        title="最近低价命中（最近 10 条）"
        extra={<Button type="link" onClick={() => onGo('history')}>全部历史</Button>}
      >
        <Table
          rowKey={(_, i) => i}
          size="small"
          pagination={false}
          dataSource={hits}
          columns={columns}
          locale={{ emptyText: <Empty description="暂无低价命中，监控运行后会出现在这里" /> }}
        />
      </Card>
    </div>
  )
}

/* ================= 任务 ================= */
function Tasks({ data, onChanged, msg, onGo }) {
  const tasks = data.tasks || []

  const toggleEnabled = async (t, enabled) => {
    try {
      await api.setTaskEnabled(t.ids || [t.id], enabled)
      msg.success(enabled ? '监控已开启' : '监控已停止')
      onChanged()
    } catch (e) { msg.error(e.message) }
  }

  const doDelete = async (t) => {
    try {
      await api.deleteTask(t.ids || [t.id])
      msg.success(`已删除监控：${t.from_city} → ${t.to_city}`)
      onChanged()
    } catch (e) { msg.error(e.message) }
  }

  const stopTag = (t) => {
    const s = t.stop
    if (!s || typeof s !== 'object') return null
    if (s.kind === 'stopover') return <Tag color="orange">经停</Tag>
    if (s.kind === 'transfer') return <Tag color="purple">中转{s.legs || ''}段</Tag>
    if (s.kind === 'direct') return <Tag>直飞</Tag>
    return null
  }

  // 经停详情小字（参考航线查询经停列）：城市 · 机场 · 航站楼　到→离（停留）
  const stopDetail = (t) => {
    const s = t.stop
    if (!s || typeof s !== 'object' || s.kind !== 'stopover') return ''
    const details = s.stops_detail || []
    if (!details.length) {
      const via = (s.via || []).join('/')
      return via ? `经停 · ${via}` : ''
    }
    return details
      .map((d) => {
        const where = [d.city, d.airport ? `${d.airport}机场` : '', d.terminal].filter(Boolean).join(' · ')
        return `${where}${d.arrive ? `　${d.arrive}→${d.depart || '?'}${d.stay ? `（${d.stay}）` : ''}` : ''}`
      })
      .join('；')
  }

  // 监控日期行：最多展示 3 个日期 + 其余数量，悬停查看全部
  // （不把日期全部平铺——flex-wrap 容器会把整列 max-content 撑成「所有日期一字排开」的宽度，
  //   多日期任务会把航线列撑到极宽，后面列被推出屏幕外）
  const DateCell = ({ dates }) => {
    const list = dates || []
    if (!list.length) return <Typography.Text type="secondary" style={{ fontSize: 12 }}>—</Typography.Text>
    const shown = list.slice(0, 3)
    const rest = list.length - shown.length
    const tip = (
      <div style={{ maxWidth: 320, maxHeight: 260, overflow: 'auto', fontSize: 12, lineHeight: 1.9 }}>
        {list.map((d) => <div key={d}>{d}</div>)}
      </div>
    )
    return (
      <Tooltip title={tip} placement="top">
        <Space size={4} wrap style={{ cursor: 'default' }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>监控：</Typography.Text>
          {shown.map((d) => <Tag key={d} style={{ marginInlineEnd: 0 }}>{d}</Tag>)}
          {rest > 0 && <Tag color="blue" style={{ marginInlineEnd: 0 }}>+{rest}</Tag>}
        </Space>
      </Tooltip>
    )
  }

  const columns = [
    {
      title: '航线', key: 'route', width: 320,
      render: (_, t) => (
        <Space direction="vertical" size={3} style={{ padding: '4px 0' }}>
          {/* 标题行：航班号 + 城市（省份） → 城市（省份） + 经停/中转 tag */}
          <Space size={6} wrap>
            <span className="mono" style={{ fontWeight: 600 }}>{t.flight_no || '—'}</span>
            <span>
              {cityWithProvince(t.from_city)}
              <ChevronRightIcon size={11} style={{ color: '#bfbfbf', margin: '0 6px' }} />
              {cityWithProvince(t.to_city)}
            </span>
            {stopTag(t)}
          </Space>
          {/* 机场 / 航站楼行 */}
          <Typography.Text type="secondary" style={{ fontSize: 12, lineHeight: 1.6 }}>
            {t.from_code} · {(t.from_airport || '').replace('机场', '')}{t.from_terminal ? ` · ${t.from_terminal}` : ''}
            <ChevronRightIcon size={10} style={{ color: '#d9d9d9', margin: '0 4px' }} />
            {t.to_code} · {(t.to_airport || '').replace('机场', '')}{t.to_terminal ? ` · ${t.to_terminal}` : ''}
          </Typography.Text>
          {/* 起降时间行 + 经停详情小字 */}
          <Space size={8} wrap>
            <span className="mono" style={{ fontSize: 12 }}>{t.dep_time || '--:--'} → {t.arr_time || '--:--'}</span>
            {!(t.dep_time && t.arr_time) && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>时刻待查询</Typography.Text>
            )}
            {stopDetail(t) && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{stopDetail(t)}</Typography.Text>
            )}
          </Space>
          {/* 监控日期行：收敛为 3 个 + 其余数量，悬停查看全部 */}
          <DateCell dates={t.dates} />
        </Space>
      ),
    },
    {
      title: '档位', key: 'product', width: 150,
      render: (_, t) => {
        const parts = String(t.product || '').split('/').map((s) => s.trim()).filter(Boolean)
        if (!parts.length) return <Typography.Text type="secondary">—</Typography.Text>
        return parts.map((p) => (
          <Tag key={p} color={p === '666' ? 'gold' : 'purple'} style={{ marginInlineEnd: 4 }}>{p}</Tag>
        ))
      },
    },
    {
      title: '目标价', key: 'target', width: 100,
      render: () => <span style={{ color: 'var(--color-low-price)', fontWeight: 700 }}>¥199</span>,
    },
    {
      title: '余票条件', key: 'seat_cond', width: 140,
      render: (_, t) => {
        const parts = []
        if (Number(t.min_seats || 1) > 1) parts.push(`≥${t.min_seats}张`)
        if (t.cabins && t.cabins.length) parts.push(`舱位 ${t.cabins.join('/')}`)
        if (!parts.length) return <Typography.Text type="secondary">不限</Typography.Text>
        return (
          <Space size={4} wrap>
            {parts.map((p, i) => <Tag key={i} color={i === 0 ? 'blue' : 'purple'}>{p}</Tag>)}
          </Space>
        )
      },
    },
    {
      title: '状态', key: 'status', width: 100,
      render: (_, t) => (
        t.enabled
          ? <Tag color="green">监控中</Tag>
          : <Tag>已停止</Tag>
      ),
    },
    {
      title: '操作', key: 'op', width: 180,
      render: (_, t) => (
        <Space size={8}>
          <Switch
            checked={t.enabled}
            checkedChildren="监控中"
            unCheckedChildren="已停止"
            onChange={(v) => toggleEnabled(t, v)}
          />
          <Popconfirm
            title={`删除监控 ${t.from_city} → ${t.to_city}`}
            description="删除后 daemon 将不再查询，价格历史保留。不可撤销。"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => doDelete(t)}
          >
            <Button type="text" danger icon={<DeleteIcon />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Card
        title={`监控任务（${tasks.length}）`}
        extra={
          <Space>
            <Typography.Text type="secondary">15 秒自动刷新 · 同一航班多个监控日期已合并；不同航班分别成条</Typography.Text>
            <Button type="primary" ghost icon={<CompassIcon />} onClick={() => onGo('flights')}>去航线查询添加</Button>
          </Space>
        }
      >
        <Table
          rowKey={(_, i) => i}
          size="middle"
          pagination={false}
          dataSource={tasks}
          columns={columns}
          scroll={{ x: 'max-content' }}
          locale={{
            emptyText: (
              <Empty description="还没有监控任务">
                <Button type="primary" icon={<CompassIcon />} onClick={() => onGo('flights')}>
                  去航线查询添加
                </Button>
              </Empty>
            ),
          }}
        />
      </Card>
    </div>
  )
}

/* ================= 监控管理（票据 + 时段 + 高级设置） ================= */
function detectFareType(raw, fallback = 'plus') {
  // 从 cURL 的请求路径识别档位：ffl/airCtLowFareSearch → PLUS；airLowFareSearch → 普通
  const m = String(raw || '').match(/https?:\/\/[^\s'"]+/i)
  const path = m ? m[0] : ''
  if (path.includes('ffl') || path.includes('airCtLowFareSearch')) return 'plus'
  if (path.includes('airLowFareSearch')) return 'normal'
  return fallback
}

function TicketSetup({ cfg, onChanged, msg }) {
  const plus = cfg.plus_ticket || {}
  const normal = cfg.normal_ticket || {}
  const hasPlus = !!plus.configured
  const hasNormal = !!normal.configured
  const any = hasPlus || hasNormal
  // 主档：优先 PLUS，其次普通；另一档运行时会自动派生
  const primary = hasPlus ? 'plus' : hasNormal ? 'normal' : null
  const primaryTicket = primary === 'plus' ? plus : primary === 'normal' ? normal : null
  const derivedType = primary === 'plus' ? (hasNormal ? '' : 'normal') : primary === 'normal' ? (hasPlus ? '' : 'plus') : ''

  const [editing, setEditing] = useState(!any)
  const [raw, setRaw] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [revealed, setRevealed] = useState('')
  const [secondsLeft, setSecondsLeft] = useState(0)

  useEffect(() => {
    if (!revealed) return undefined
    const timer = setTimeout(() => { setRevealed(''); setSecondsLeft(0) }, 30000)
    return () => clearTimeout(timer)
  }, [revealed])

  useEffect(() => {
    if (!revealed) return undefined
    const iv = setInterval(() => setSecondsLeft((s) => (s > 0 ? s - 1 : 0)), 1000)
    return () => clearInterval(iv)
  }, [revealed])

  const detected = detectFareType(raw, primary || 'plus')
  const detectedLabel = detected === 'plus' ? 'PLUS 专享' : '普通票价'

  const save = async () => {
    setError('')
    if (!raw.trim()) { setError('内容为空，未保存。'); return }
    setBusy(true)
    try {
      const r = await api.saveTicket(detected, raw)
      if (!r.ok) { setError(r.error || '保存失败'); return }
      msg.success(`票据已识别为「${detectedLabel}」并保存，另一档自动派生，daemon 下一轮自动生效`)
      setRaw('')
      setEditing(false)
      onChanged()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const reveal = async () => {
    if (!primary) return
    if (revealed) { setRevealed(''); setSecondsLeft(0); return }
    try {
      const r = await api.ticketRaw(primary)
      setRevealed(r.raw || '')
      setSecondsLeft(30)
    } catch (e) {
      msg.error(e.message)
    }
  }

  const bodyOk = primaryTicket && primaryTicket.configured && primaryTicket.length > 0
  const statusTag = !any
    ? <Tag color="orange">未配置</Tag>
    : hasPlus && hasNormal
      ? <Tag color="green">已配置（两档均有）</Tag>
      : <Tag color="green">已配置</Tag>

  return (
    <Card title="抓包票据" extra={statusTag}>
      <Descriptions size="small" column={1}>
        <Descriptions.Item label="当前票据">
          {primary ? (primary === 'plus' ? 'PLUS 专享' : '普通票价') : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="请求地址">
          {primaryTicket?.configured ? (
            <Tooltip title={primaryTicket.url}>
              <Typography.Text ellipsis style={{ maxWidth: 480, display: 'block' }}>{primaryTicket.url}</Typography.Text>
            </Tooltip>
          ) : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="端点">
          {primaryTicket?.configured ? (primaryTicket.endpoint_note || '—') : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="体积">
          {primaryTicket ? `${(primaryTicket.length / 1024).toFixed(1)} KB` : '—'}
        </Descriptions.Item>
      </Descriptions>

      {derivedType && (
        <Alert
          style={{ marginBottom: 12 }}
          type="info"
          showIcon
          message={`未单独抓包${derivedType === 'plus' ? 'PLUS 专享' : '普通票价'}票据：运行时自动从当前票据派生（共用凭证，自动改写端点与档位标记）。`}
        />
      )}
      {!derivedType && hasPlus && hasNormal && (
        <Alert style={{ marginBottom: 12 }} type="success" showIcon message="PLUS 与普通档均已单独配置，均按原档使用。" />
      )}

      <Space style={{ margin: '8px 0 12px' }} size={[8, 8]} wrap>
        <Tag color={primaryTicket?.configured ? 'green' : 'red'}>{primaryTicket?.configured ? '✓' : '✗'} 请求地址已解析</Tag>
        <Tag color={bodyOk ? 'green' : 'red'}>{bodyOk ? '✓' : '✗'} 请求体完整</Tag>
        {primaryTicket?.configured && (
          <Button size="small" type="link" onClick={reveal}>{revealed ? '收起明文' : '查看明文'}</Button>
        )}
      </Space>

      {revealed && (
        <div className="raw-box">
          <pre>{revealed}</pre>
          <div className="muted" style={{ fontSize: 12 }}>{secondsLeft} 秒后自动收起</div>
        </div>
      )}

      {!editing ? (
        <div style={{ textAlign: 'right' }}>
          <Button type="primary" icon={<EditIcon />} onClick={() => setEditing(true)}>更新票据</Button>
        </div>
      ) : (
        <div>
          <div className="muted" style={{ marginBottom: 6 }}>
            粘贴后自动识别档位，当前识别为
            <Tag color={detected === 'plus' ? 'blue' : 'default'} style={{ marginInline: '0 4px' }}>{detectedLabel}</Tag>
            （另一档自动派生，抓一份即可）
          </div>
          <Input.TextArea
            rows={6}
            placeholder="粘贴完整 cURL（含 Cookie、token、hnairSign），PLUS 端点 ffl/airLowFareSearch 或普通端点 airLowFareSearch 均可…"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
          />
          {error && <Alert style={{ marginTop: 8 }} type="error" showIcon message={error} />}
          <div style={{ marginTop: 12, textAlign: 'right' }}>
            <Space>
              <Button onClick={() => setRaw('')}>清空</Button>
              {any && <Button onClick={() => setEditing(false)}>取消</Button>}
              <Button type="primary" loading={busy} onClick={save}>保存票据</Button>
            </Space>
          </div>
        </div>
      )}

      <Collapse
        ghost
        style={{ marginTop: 12 }}
        items={[{
          key: 'howto',
          label: '如何获取票据（抓一份即可，自动识别档位）',
          children: (
            <div>
              <Typography.Paragraph style={{ marginBottom: 8 }}>
                <Typography.Text strong>抓包方法：</Typography.Text>
                打开海航官网/App 查询页 → F12 开发者工具 → Network（网络）→ 找到对应查询请求 → 右键 →
                <Typography.Text code>Copy → Copy as cURL (bash)</Typography.Text>，粘贴到上方输入框并保存。
              </Typography.Paragraph>
              <Typography.Paragraph style={{ marginBottom: 8 }}>
                <Typography.Text strong>两种端点：</Typography.Text>
                PLUS 专享请求名
                <Typography.Text code>ffl/airLowFareSearch</Typography.Text>
                （抓到的 <Typography.Text code>airCtLowFareSearch</Typography.Text> 也会自动改写为 PLUS 端点）；
                普通票价请求名 <Typography.Text code>airLowFareSearch</Typography.Text>（不带 ffl 前缀）。
              </Typography.Paragraph>
              <Typography.Paragraph style={{ marginBottom: 0 }}>
                <Typography.Text type="secondary">
                  两档共用同一套 cookie/token/签名：只抓一份后，另一档运行时会自动借用并改写端点与档位标记（状态显示「自动派生」），无需重复抓包。
                  若提示无法识别，请改用 Copy as cURL (cmd) 格式重试；已配置后保存即生效（daemon 下一轮自动读取，无需重启）。
                </Typography.Text>
              </Typography.Paragraph>
            </div>
          ),
        }]}
      />
    </Card>
  )
}

function MonitorSettings({ data, onChanged, msg }) {
  const cfg = data.config
  const [busy, setBusy] = useState('')
  const [start, setStart] = useState(() => dayjs(cfg.monitor_window?.start || '07:00', 'HH:mm'))
  const [end, setEnd] = useState(() => dayjs(cfg.monitor_window?.end || '23:00', 'HH:mm'))
  const [proxy, setProxy] = useState(cfg.proxy || '')
  const [signRefresh, setSignRefresh] = useState(cfg.sign_refresh || false)
  const [priceQueryOn, setPriceQueryOn] = useState(cfg.price_query?.enabled !== false)
  const [priceQueryInterval, setPriceQueryInterval] = useState(cfg.price_query?.min_interval || 8)

  const saveWindow = async () => {
    setBusy('window')
    try {
      await api.saveMonitorWindow(start.format('HH:mm'), end.format('HH:mm'))
      msg.success(`监控时段已保存：${start.format('HH:mm')} - ${end.format('HH:mm')}`)
      onChanged()
    } catch (e) { msg.error(e.message) } finally { setBusy('') }
  }

  const saveProxy = async () => {
    setBusy('proxy')
    try {
      await api.saveProxy(proxy)
      msg.success('代理设置已保存')
    } catch (e) { msg.error(e.message) } finally { setBusy('') }
  }

  const toggleSignRefresh = async (v) => {
    setSignRefresh(v)
    try {
      await api.saveSignRefresh(v)
      msg.success(v ? '已开启签名刷新：每轮刷新 stime 并重签，失败自动回退' : '已关闭签名刷新')
    } catch (e) { msg.error(e.message) }
  }

  const togglePriceQuery = async (v) => {
    setPriceQueryOn(v)
    try {
      await api.savePriceQuery(v, priceQueryInterval)
      msg.success(v ? '已开启实时查价（原价/优惠价可查）' : '已关闭实时查价（防风控），不再请求海航接口')
      onChanged()
    } catch (e) {
      setPriceQueryOn(!v)
      msg.error(e.message)
    }
  }

  const savePriceInterval = async () => {
    try {
      await api.savePriceQuery(priceQueryOn, priceQueryInterval)
      msg.success(`实时查价最小间隔已保存：${priceQueryInterval} 秒`)
      onChanged()
    } catch (e) { msg.error(e.message) }
  }

  return (
    <div>
      <TicketSetup cfg={cfg} onChanged={onChanged} msg={msg} />

      {/* 实时查价（防风控） */}
      <Card style={{ marginTop: 16, marginBottom: 16 }} title="实时查价（防风控）">
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space wrap>
            <Switch checked={priceQueryOn} onChange={togglePriceQuery} checkedChildren="开" unCheckedChildren="关" />
            <Typography.Text strong>允许实时查价（原价/优惠价）</Typography.Text>
            <Typography.Text type="secondary">查询最小间隔</Typography.Text>
            <InputNumber
              min={2}
              max={120}
              value={priceQueryInterval}
              onChange={(v) => setPriceQueryInterval(v || 8)}
              addonAfter="秒"
              style={{ width: 130 }}
            />
            <Button onClick={savePriceInterval} loading={busy === 'pq'}>保存间隔</Button>
          </Space>
          <Alert
            type={priceQueryOn ? 'info' : 'warning'}
            showIcon
            message={
              priceQueryOn
                ? '实时查价会直接请求海航接口（存在风控风险）。已做限流：每次查询间隔不少于设定秒数，手动与监控共用此开关。'
                : '实时查价已关闭：手动查询与监控任务都不再请求海航价格接口，只保留底表航班信息查询。'
            }
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            航班信息（起降时刻/经停/航站楼）的更新走「第三方校正」机制（data/sediment/third_party/），
            不依赖实时查价；如担心被风控可随时关掉本开关，不影响航班信息查询。
          </Typography.Text>
        </Space>
      </Card>

      {/* 监控时段 */}
      <Card style={{ marginTop: 16, marginBottom: 16 }} title="每日监控时段">
        <Space size="large" wrap>
          <span>
            <Typography.Text strong style={{ marginRight: 8 }}>开始</Typography.Text>
            <TimePicker format="HH:mm" minuteStep={5} value={start} onChange={(v) => v && setStart(v)} />
          </span>
          <span>
            <Typography.Text strong style={{ marginRight: 8 }}>结束</Typography.Text>
            <TimePicker format="HH:mm" minuteStep={5} value={end} onChange={(v) => v && setEnd(v)} />
          </span>
          <Button type="primary" loading={busy === 'window'} onClick={saveWindow}>保存时段</Button>
        </Space>
        <div className="muted" style={{ marginTop: 8 }}>仅在该时段内轮询抓价；跨天请设置例如 22:00 到 07:00（22:00 ~ 次日 07:00）。</div>
      </Card>

      {/* 高级设置 */}
      <Card title="高级设置">
        <Row gutter={[16, 16]}>
          <Col xs={24} md={12}>
            <Space direction="vertical" size={8}>
              <Space>
                <Switch checked={signRefresh} onChange={toggleSignRefresh} checkedChildren="开" unCheckedChildren="关" />
                <Typography.Text strong>签名刷新（stime + 重签）</Typography.Text>
              </Space>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                开启后每轮刷新 common.stime 并重签；遇验签错误自动回退原签名抓包。建议稳定运行几天后再开。
              </Typography.Text>
            </Space>
          </Col>
          <Col xs={24} md={12}>
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Typography.Text strong>HTTP(S) 代理（可选）</Typography.Text>
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  placeholder="例如 http://127.0.0.1:7890，留空表示直连"
                  value={proxy}
                  onChange={(e) => setProxy(e.target.value)}
                />
                <Button type="primary" loading={busy === 'proxy'} onClick={saveProxy}>保存代理</Button>
              </Space.Compact>
            </Space>
          </Col>
        </Row>
      </Card>
    </div>
  )
}

/* ================= 通知管理 ================= */
function wsStateTag(ws) {
  if (!ws) return <Tag color="orange">未知</Tag>
  const map = {
    connected: { color: 'green', text: '已连接' },
    connecting: { color: 'blue', text: '连接中' },
    reconnecting: { color: 'blue', text: '重连中' },
    failed: { color: 'red', text: '失败' },
    not_configured: { color: 'orange', text: '未配置' },
    stopped: { color: 'default', text: '已停止' },
  }
  const m = map[ws.state] || { color: 'default', text: ws.state || '未知' }
  return <Tag color={m.color}>{m.text}</Tag>
}

function channelStatusTag(enabled) {
  return enabled ? <Tag color="green">已启用</Tag> : <Tag color="orange">未启用</Tag>
}

/* 教学问号：每张渠道卡片标题旁的「?」；hover 显示配置步骤，内含关闭按钮 */
function GuideIcon({ title, steps }) {
  const [open, setOpen] = useState(false)
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger="hover"
      placement="bottomLeft"
      content={
        <div style={{ width: 360 }}>
          <Space style={{ marginBottom: 8, width: '100%', justifyContent: 'space-between' }}>
            <Typography.Text strong>{title}</Typography.Text>
            <Button size="small" type="text" icon={<DeleteIcon />} onClick={() => setOpen(false)}>关闭</Button>
          </Space>
          <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, lineHeight: 1.9, maxHeight: 320, overflow: 'auto' }}>
            {steps.map((s, i) => (
              <li key={i}>
                <span style={{ fontWeight: 600 }}>{s.title}</span>
                {s.description ? `：${s.description}` : ''}
              </li>
            ))}
          </ol>
        </div>
      }
    >
      <QuestionIcon size={13} style={{ color: '#8c8c8c', cursor: 'help' }} />
    </Popover>
  )
}

function ChannelCard({
  name, title, color, guide, view, fields = [], busy, msg, onSave, onTest,
  showSwitch = true, statusLine, testResult, onClearTest, children,
}) {
  const [form] = Form.useForm()
  const [editing, setEditing] = useState(false)
  const enabled = !!view?.enabled

  const save = async () => {
    const vals = form.getFieldsValue() || {}
    const payload = {}
    // 只提交真正修改过的字段：与脱敏展示值相同的（未动的掩码预填）视为「不修改」，
    // 避免把掩码值写回真实凭证；秘密字段留空由后端保持原值。
    for (const f of fields) {
      const v = String(vals[f.key] ?? '').trim()
      const cur = String(view?.[f.key] ?? '')
      if (v === cur) continue
      payload[f.key] = v
    }
    try {
      if (Object.keys(payload).length) await onSave({ [name]: { ...payload } })
      msg.success('配置已保存')
      setEditing(false)
      form.resetFields()
    } catch (e) { msg.error(e.message) }
  }

  const toggle = async (v) => {
    try {
      await onSave({ [name]: { enabled: v } })
      msg.success(v ? `${title} 已启用` : `${title} 已停用`)
    } catch (e) { msg.error(e.message) }
  }

  const summary = fields.some((f) => view?.[f.key])
    ? `已配置：${fields.filter((f) => view?.[f.key]).map((f) => `${f.label} ${view[f.key]}`).join('；')}`
    : ''

  return (
    <Col xs={24} sm={12} xl={8}>
      <Card
        size="small"
        className="channel-card"
        style={{ height: '100%' }}
        title={
          <Space size={6} wrap>
            <span style={{ width: 8, height: 8, borderRadius: 4, background: color, display: 'inline-block' }} />
            {title}
            {guide && <GuideIcon title={guide.title} steps={guide.steps} />}
          </Space>
        }
        extra={showSwitch && <Switch size="small" checked={enabled} loading={busy === 'notify-save'} onChange={toggle} />}
      >
        {/* 状态/配置信息行：所有卡片统一在最上方一行（12px 小字） */}
        <div className="channel-status">
          {statusLine || (editing ? null : <Space size={6}>{channelStatusTag(enabled)}{summary && <Typography.Text type="secondary" style={{ fontSize: 12, lineHeight: '20px' }} ellipsis={{ tooltip: summary }}>{summary}</Typography.Text>}</Space>)}
        </div>

        {/* 中间内容：操作按钮或编辑表单（children 模式给微信/飞书用） */}
        {children
          ? children({ form, editing, setEditing, enabled })
          : !editing && (
            <Space wrap>
              <Button size="small" type={enabled ? 'default' : 'primary'} ghost={enabled} loading={busy === `test-${name}`} onClick={onTest}>
                发送测试消息
              </Button>
              <Button size="small" icon={<SettingsIcon />} onClick={() => { form.setFieldsValue({ ...view }); setEditing(true) }}>
                配置
              </Button>
            </Space>
          )}

        {!children && editing && (
          <Form form={form} layout="vertical" size="small">
            {fields.map((f) => (
              <Form.Item key={f.key} name={f.key} label={
                <span>{f.label}{f.keepEmpty && <Typography.Text type="secondary" style={{ fontSize: 12 }}>（留空不修改）</Typography.Text>}</span>
              } style={{ marginBottom: 6 }}>
                {f.secret ? (
                  <Input.Password placeholder={f.placeholder || '已配置可留空不修改'} autoComplete="new-password" />
                ) : (
                  <Input placeholder={f.placeholder} />
                )}
              </Form.Item>
            ))}
            <Space>
              <Button type="primary" size="small" loading={busy === 'notify-save'} onClick={save}>保存</Button>
              <Button size="small" onClick={() => { setEditing(false); form.resetFields() }}>取消</Button>
            </Space>
          </Form>
        )}

        {/* 测试结果：统一固定在卡片底部区域，无提示时占位保持高度一致 */}
        <div className="channel-result">
          {testResult && (
            <Alert
              type={testResult.ok ? 'success' : 'error'}
              showIcon
              message={testResult.text}
              action={<Button size="small" type="text" onClick={onClearTest}>关闭</Button>}
            />
          )}
        </div>
      </Card>
    </Col>
  )
}

function Notifications({ data, onChanged, msg }) {
  const cfg = data.config
  const feishu = cfg.feishu || {}
  const nch = cfg.notify_channels || {}
  const ws = nch.feishu_ws || {}
  const historyRows = data.notification_history || []
  const confirms = data.card_confirmations || []
  const [fsForm] = Form.useForm()
  const [keysRaw, setKeysRaw] = useState('')
  const [busy, setBusy] = useState('')
  const [showWechatForm, setShowWechatForm] = useState(false)
  const [showFeishuForm, setShowFeishuForm] = useState(false)
  // 每张渠道卡片自持测试结果，互不串扰（不再用全局 testMsg）
  const [tests, setTests] = useState({})
  const setTest = (channel, ok, text) => setTests((prev) => ({ ...prev, [channel]: { ok, text } }))
  const clearTest = (channel) => setTests((prev) => {
    const next = { ...prev }
    delete next[channel]
    return next
  })

  const saveKeys = async () => {
    setBusy('keys')
    try {
      await api.saveSendKeys(keysRaw)
      msg.success('SendKey 已保存，daemon 下一轮生效')
      setKeysRaw('')
      setTest('wechat', true, 'SendKey 已保存，可发送测试消息验证')
      onChanged()
    } catch (e) { msg.error(e.message) } finally { setBusy('') }
  }

  const testAlert = async () => {
    const keys = keysRaw.split('\n').map((s) => s.trim()).filter(Boolean)
    if (keys.length === 0 && cfg.send_keys_count === 0) {
      msg.error('请先填写至少 1 个 SendKey')
      return
    }
    setBusy('test')
    try {
      const r = await api.testAlert(keys.length ? keys : undefined)
      if (r.success > 0) {
        setTest('wechat', true, `测试消息发送完成：成功 ${r.success} / 总计 ${r.total}`)
        msg.success(`测试消息：成功 ${r.success} / ${r.total}`)
      } else {
        setTest('wechat', false, '测试消息发送失败，请检查 SendKey 是否正确')
        msg.error('测试消息发送失败')
      }
    } catch (e) { msg.error(e.message) } finally { setBusy('') }
  }

  const saveFeishu = async () => {
    const values = fsForm.getFieldsValue()
    setBusy('feishu-save')
    try {
      const r = await api.saveFeishu({
        app_id: values.app_id || '',
        app_secret: values.app_secret || '',
        receiver: values.receiver || '',
      })
      msg.success(r.message || '飞书配置已保存')
      fsForm.resetFields()
      onChanged()
    } catch (e) { msg.error(e.message) } finally { setBusy('') }
  }

  const sendTestFeishu = async () => {
    const values = fsForm.getFieldsValue()
    setBusy('feishu-test')
    try {
      const r = await api.testFeishu({
        app_id: values.app_id || '',
        app_secret: values.app_secret || '',
        receiver: values.receiver || '',
      })
      if (r.success) {
        msg.success('测试消息已发送，请到飞书查看')
        setTest('feishu', true, '测试消息已发送，请到飞书查看')
      } else {
        msg.error(r.error || '测试消息发送失败')
        setTest('feishu', false, `发送失败：${r.error || '未知错误'}`)
      }
    } catch (e) { msg.error(e.message) } finally { setBusy('') }
  }

  const saveNotify = async (payload) => {
    setBusy('notify-save')
    try {
      await api.saveNotifyChannels(payload)
      onChanged()
    } catch (e) { msg.error(e.message) } finally { setBusy('') }
  }

  const testChannel = async (channel, label) => {
    setBusy(`test-${channel}`)
    try {
      const r = await api.testNotifyChannel(channel)
      if (r.success) {
        msg.success(`${label} 测试消息已发送${channel === 'feishu' ? '，可在飞书点击卡片按钮验证确认回执' : ''}`)
        setTest(channel, true, r.message_id ? `${label}：${r.message}（${r.message_id}）` : `${label}：${r.message || '已发送'}`)
        onChanged()
      } else {
        msg.error(r.error || `${label} 测试发送失败`)
        setTest(channel, false, `${label}：${r.error || '发送失败'}`)
      }
    } catch (e) { msg.error(e.message) } finally { setBusy('') }
  }

  /* 每张渠道卡片标题旁「?」的配置教学步骤（hover 查看，可关闭） */
  const guides = {
    wechat: {
      title: '微信推送（Server酱）配置步骤',
      steps: [
        { title: '打开 Server酱官网', description: '浏览器访问 sct.ftqq.com，用微信扫码登录' },
        { title: '复制 SendKey', description: '在「SendKey」页复制 SCT 开头的一串字符' },
        { title: '粘贴到输入框', description: '支持每行一个，多个微信号可各配一个' },
        { title: '保存并验证', description: '点击「保存并验证」' },
        { title: '发送测试消息', description: '微信收到即绑定成功，后续低价与告警自动推送' },
      ],
    },
    feishu: {
      title: '飞书机器人 + 加急卡片确认配置步骤',
      steps: [
        { title: '创建企业自建应用', description: '登录 open.feishu.cn 开发者后台 → 创建企业自建应用' },
        { title: '添加机器人能力', description: '应用功能 → 机器人 → 启用' },
        { title: '开通权限并发布', description: '权限管理开通 im:message:send_as_bot、im:message:urgent_app 等，发布版本并等待审核通过' },
        { title: '事件订阅选长连接', description: '事件与回调 → 长连接模式，无需公网回调地址' },
        { title: '复制凭证', description: '「凭证与基础信息」中复制 App ID / App Secret；接收人填你的飞书邮箱或 ou_ 开头的 OpenID' },
        { title: '保存并测试', description: '填入表单保存；发测试消息，再点「发送确认卡片」到飞书点按钮验证回执' },
      ],
    },
    wecom: {
      title: '企业微信（自建应用）配置步骤',
      steps: [
        { title: '创建自建应用', description: '登录 work.weixin.qq.com 管理后台 → 应用管理 → 自建 → 创建应用' },
        { title: '获取企业ID', description: '「我的企业」→ 企业信息 → 企业ID（corp_id，ww 开头）' },
        { title: '获取 AgentId 与 Secret', description: '进入自建应用详情页：AgentId 直接可见，点「查看」获取 Secret' },
        { title: '填写接收人 UserID', description: '通讯录中成员的 UserID（如 ZhangSan），需在应用可见范围内' },
        { title: '保存并发送测试', description: '保存后点「发送测试消息」，企业微信收到即成功' },
      ],
    },
    dingtalk: {
      title: '钉钉群机器人配置步骤',
      steps: [
        { title: '添加自定义机器人', description: '打开目标钉钉群 → 群设置 → 智能群助手 → 添加机器人 → 自定义' },
        { title: '复制 Webhook', description: '添加完成后复制 Webhook 地址（含 access_token）' },
        { title: '安全设置：加签（可选）', description: '若开启加签，复制密钥填入「加签密钥」' },
        { title: '保存并发送测试', description: '保存后点「发送测试消息」，群内收到即成功' },
      ],
    },
    bark: {
      title: 'iOS · Bark 配置步骤',
      steps: [
        { title: '安装 Bark', description: 'App Store 搜索 Bark 安装并打开' },
        { title: '复制设备 Key', description: 'App 首页即为设备 Key（形如 https://api.day.app/XXXXXXXX）' },
        { title: '自建服务器（可选）', description: '使用自建 Bark 服务时填入 server 地址，默认 https://api.day.app' },
        { title: '保存并发送测试', description: '保存后点「发送测试消息」，手机收到即成功' },
      ],
    },
    ntfy: {
      title: '安卓 · ntfy 配置步骤',
      steps: [
        { title: '订阅主题', description: '安装 ntfy 客户端，订阅一个自定义主题名' },
        { title: '填写主题与服务器', description: 'Topic 填订阅的主题名；server 默认 https://ntfy.sh，自建可改' },
        { title: '保存并发送测试', description: '保存后点「发送测试消息」，手机收到即成功' },
      ],
    },
  }

  const channelCards = [
    {
      name: 'wechat', title: '微信', color: '#07c160',
      view: {}, showSwitch: false,
      guide: guides.wechat,
      statusLine: (
        <Space size={6}>
          <Tag color={cfg.send_keys_count > 0 ? 'green' : 'orange'}>
            {cfg.send_keys_count > 0 ? `已绑定 ${cfg.send_keys_count} 个` : '未配置'}
          </Tag>
          {cfg.send_keys_count > 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12, lineHeight: '20px' }} ellipsis={{ tooltip: '低价与 Token 过期告警会推送到微信' }}>
              低价与 Token 过期告警推送到微信
            </Typography.Text>
          )}
        </Space>
      ),
      children: () => (cfg.send_keys_count > 0 && !showWechatForm ? (
        <Space wrap>
          <Button size="small" loading={busy === 'test'} onClick={testAlert}>发送测试消息</Button>
          <Button size="small" icon={<SettingsIcon />} onClick={() => setShowWechatForm(true)}>重新配置</Button>
        </Space>
      ) : (
        <>
          {cfg.send_keys_count === 0 && (
            <Steps
              direction="vertical"
              size="small"
              current={5}
              items={guides.wechat.steps}
              style={{ marginBottom: 10 }}
            />
          )}
          <Input.TextArea
            rows={3}
            placeholder="每行粘贴一个 SendKey，例如 SCT123..."
            value={keysRaw}
            onChange={(e) => setKeysRaw(e.target.value)}
            style={{ fontSize: 12 }}
          />
          <Space wrap style={{ marginTop: 8 }}>
            <Button type="primary" size="small" loading={busy === 'keys'} onClick={saveKeys}>保存并验证</Button>
            <Button size="small" loading={busy === 'test'} onClick={testAlert}>发送测试消息</Button>
            {cfg.send_keys_count > 0 && (
              <Button size="small" onClick={() => { setShowWechatForm(false); setKeysRaw('') }}>收起</Button>
            )}
          </Space>
        </>
      )),
    },
    {
      name: 'feishu', title: '飞书', color: '#3370ff',
      view: {}, showSwitch: false,
      guide: guides.feishu,
      statusLine: (
        <Space size={6}>
          {feishu.configured
            ? <Tag color="green">{feishu.has_secret ? '已配置' : '配置不完整'}</Tag>
            : <Tag color="orange">未配置</Tag>}
          {wsStateTag(ws)}
          {feishu.configured && (
            <Typography.Text
              type="secondary"
              style={{ fontSize: 12, lineHeight: '20px' }}
              ellipsis={{ tooltip: `App ID：${feishu.app_id}；接收人：${feishu.receiver || '—'}；长连接事件 ${ws.event_count || 0} 次${ws.last_event_ts ? `，最近 ${formatTs(ws.last_event_ts)}` : ''}` }}
            >
              App {feishu.app_id} · {feishu.receiver || '未设接收人'} · 事件 {ws.event_count || 0} 次
            </Typography.Text>
          )}
        </Space>
      ),
      children: () => (feishu.configured && !showFeishuForm ? (
        <Space wrap>
          <Button size="small" loading={busy === 'feishu-test'} onClick={sendTestFeishu}>发送测试消息</Button>
          <Button size="small" type="primary" ghost loading={busy === 'test-feishu'} onClick={() => testChannel('feishu', '飞书')}>
            发送确认卡片
          </Button>
          <Button size="small" icon={<SettingsIcon />} onClick={() => setShowFeishuForm(true)}>重新配置</Button>
        </Space>
      ) : (
        <>
          {!feishu.configured && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 8, fontSize: 12 }}
              message={
                <span style={{ fontSize: 12 }}>
                  首次使用按标题旁「<QuestionIcon size={12} style={{ verticalAlign: -2 }} />」完成开放平台建应用/加机器人/权限/长连接；完整图文
                  <Typography.Text code>docs/notify_channels_guide.md</Typography.Text>。
                </span>
              }
            />
          )}
          <Form form={fsForm} layout="vertical" size="small">
            <Form.Item name="app_id" label="App ID" style={{ marginBottom: 6 }}>
              <Input placeholder="cli_ 开头；已配置可留空不修改" style={{ fontSize: 12 }} />
            </Form.Item>
            <Form.Item name="app_secret" label="App Secret" style={{ marginBottom: 6 }}>
              <Input.Password placeholder="已配置可留空不修改（不会回显）" autoComplete="new-password" style={{ fontSize: 12 }} />
            </Form.Item>
            <Form.Item name="receiver" label="接收人" style={{ marginBottom: 6 }}>
              <Input placeholder="你的飞书邮箱，或 ou_ 开头的 OpenID" style={{ fontSize: 12 }} />
            </Form.Item>
          </Form>
          <Space wrap>
            <Button type="primary" size="small" loading={busy === 'feishu-save'} onClick={saveFeishu} icon={<SendIcon />}>保存配置</Button>
            <Button size="small" loading={busy === 'feishu-test'} onClick={sendTestFeishu}>发送测试消息</Button>
            {feishu.configured && (
              <Button size="small" onClick={() => { setShowFeishuForm(false); fsForm.resetFields() }}>收起</Button>
            )}
          </Space>
        </>
      )),
    },
    {
      name: 'wecom', title: '企业微信', color: '#0082ef',
      view: nch.wecom || {}, guide: guides.wecom,
      fields: [
        { key: 'corp_id', label: '企业ID', placeholder: 'ww 开头', secret: true },
        { key: 'agent_id', label: '应用 AgentId', secret: true },
        { key: 'secret', label: '应用 Secret', keepEmpty: true, secret: true },
        { key: 'user_id', label: '成员 UserID', placeholder: '接收人，如 ZhangSan', secret: true },
      ],
    },
    {
      name: 'dingtalk', title: '钉钉', color: '#2a9df4',
      view: nch.dingtalk || {}, guide: guides.dingtalk,
      fields: [
        { key: 'webhook', label: 'Webhook 地址', placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=...', secret: false },
        { key: 'secret', label: '加签密钥（可选）', keepEmpty: true, secret: true },
      ],
    },
    {
      name: 'bark', title: 'iOS · Bark', color: '#f3a933',
      view: nch.bark || {}, guide: guides.bark,
      fields: [
        { key: 'device_key', label: '设备 Key', keepEmpty: true, secret: true },
        { key: 'server', label: '服务地址', placeholder: '默认 https://api.day.app，自建可改' },
      ],
    },
    {
      name: 'ntfy', title: '安卓 · ntfy', color: '#17b26a',
      view: nch.ntfy || {}, guide: guides.ntfy,
      fields: [
        { key: 'topic', label: '主题 Topic', keepEmpty: true, secret: true },
        { key: 'server', label: '服务器地址', placeholder: '默认 https://ntfy.sh，自建可改' },
      ],
    },
  ]

  const historyColumns = [
    { title: '时间', dataIndex: 'ts', width: 160, render: (v) => formatTs(v) },
    { title: '渠道', dataIndex: 'channel_label', width: 110 },
    { title: '级别', dataIndex: 'level', width: 90, render: (v) => (v === 'critical' ? <Tag color="red">加急/阻断</Tag> : v === 'important' ? <Tag color="blue">重要</Tag> : <Tag>{v}</Tag>) },
    { title: '标题', dataIndex: 'title', ellipsis: true },
    { title: '结果', dataIndex: 'ok', width: 90, render: (v) => (v ? <Tag color="green">成功</Tag> : <Tag color="red">失败</Tag>) },
    { title: '说明/错误', dataIndex: 'error', ellipsis: true, render: (v) => (v ? <Typography.Text type="danger" style={{ fontSize: 12 }}>{v}</Typography.Text> : <Typography.Text type="secondary" style={{ fontSize: 12 }}>—</Typography.Text>) },
  ]

  const confirmColumns = [
    { title: '时间', dataIndex: 'ts', width: 160, render: (v) => formatTs(v) },
    { title: '操作人', dataIndex: 'operator', width: 120 },
    { title: '操作', dataIndex: 'confirmed', width: 100, render: (v) => (v ? <Tag color="green">确认已处理</Tag> : <Tag>忽略</Tag>) },
    { title: '消息', dataIndex: 'message_id', ellipsis: true, render: (v) => <span className="mono" style={{ fontSize: 12 }}>{v}</span> },
    { title: '说明', dataIndex: 'note', ellipsis: true, render: (v) => v || '—' },
  ]

  return (
    <div>
      {/* 通知渠道：六张卡片统一样式，标题旁「?」为配置教学 */}
      <Card
        style={{ marginBottom: 16 }}
        title={<Space><NotifyIcon style={{ color: '#07c160' }} />通知渠道</Space>}
      >
        <Row gutter={[16, 16]} align="stretch">
          {channelCards.map((c) => (
            <ChannelCard
              key={c.name}
              name={c.name}
              title={c.title}
              color={c.color}
              view={c.view}
              fields={c.fields}
              busy={busy}
              msg={msg}
              onSave={saveNotify}
              onTest={() => testChannel(c.name, c.title)}
              guide={c.guide}
              showSwitch={c.showSwitch}
              statusLine={c.statusLine}
              testResult={tests[c.name]}
              onClearTest={() => clearTest(c.name)}
            >
              {c.children}
            </ChannelCard>
          ))}
        </Row>
      </Card>

      {/* 通知历史：独立成块，可往下滚动多行 */}
      <Card
        style={{ marginBottom: 16 }}
        size="small"
        title={<Space><HistoryIcon />通知历史（最近 {historyRows.length} 条）</Space>}
      >
        <Table
          rowKey={(_, i) => i}
          size="small"
          pagination={false}
          dataSource={historyRows}
          columns={historyColumns}
          scroll={{ y: 320, x: 'max-content' }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无通知历史，发送测试或触发提醒后出现在这里" /> }}
        />
      </Card>

      {/* 飞书加急卡片确认回执：独立成块，可往下滚动多行 */}
      <Card
        size="small"
        title={<Space><NotifyIcon />飞书加急卡片确认回执（最近 {confirms.length} 条）</Space>}
      >
        <Table
          rowKey={(_, i) => i}
          size="small"
          pagination={false}
          dataSource={confirms.slice().reverse()}
          columns={confirmColumns}
          scroll={{ y: 320, x: 'max-content' }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无回执：在飞书点卡片「确认已处理 / 忽略」后记录在这里" /> }}
        />
      </Card>
    </div>
  )
}

/* ================= 航线查询 ================= */
function fmtDays(days = []) {
  if (!days || !days.length) return '—'
  return days.join(',')
}

function fmtDateRanges(ranges = []) {
  if (!ranges || !ranges.length) return '—'
  return ranges.map(([a, b]) => (a === b ? a.slice(5) : `${a.slice(5)}~${b.slice(5)}`)).join('、')
}

function cityNameOf(label) {
  // 「成都（CTU/TFU）·双流/天府」→「成都」；直接输入三字码/机场名则原样返回
  return String(label || '').split('（')[0].trim()
}

const WEEK_LABELS = ['一', '二', '三', '四', '五', '六', '日']

/* 班期日历（滑动翻月）：首屏为当月（限定在放票窗口内），左右/上下滑动或点箭头切月。
   ok=绿色✓可兑换，no=灰色班期不符，off=浅灰未放票。 */
function ScheduleCalendar({ rec }) {
  const ranges = rec.effective_dates || []
  const days = rec.days || []
  const now = dayjs()
  const startMonth = now.startOf('month')
  const winStart = ranges.length ? dayjs(ranges[0][0]).startOf('month') : startMonth
  const winEndRaw = ranges.length
    ? dayjs(ranges.reduce((m, [, b]) => (b > m ? b : m), ''))
    : now.endOf('year')
  const winEnd = winEndRaw.isAfter(now.endOf('year')) ? now.endOf('year') : winEndRaw
  const minMonth = winStart.isBefore(startMonth) ? startMonth : winStart
  const maxMonth = winEnd.startOf('month')
  const initial = startMonth.isBefore(minMonth) ? minMonth : startMonth.isAfter(maxMonth) ? maxMonth : startMonth
  const [view, setView] = useState(initial)
  const dragRef = useRef({ x: 0, y: 0, active: false })

  const go = (delta) => {
    setView((v) => {
      let nv = v.add(delta, 'month')
      if (nv.isBefore(minMonth)) nv = minMonth
      else if (nv.isAfter(maxMonth)) nv = maxMonth
      return nv
    })
  }

  const dragStart = (e) => {
    const pt = e.touches ? e.touches[0] : e
    dragRef.current = { x: pt.clientX, y: pt.clientY, active: true }
  }
  const dragEnd = (e) => {
    if (!dragRef.current.active) return
    const pt = e.changedTouches ? e.changedTouches[0] : e
    const dx = pt.clientX - dragRef.current.x
    const dy = pt.clientY - dragRef.current.y
    dragRef.current.active = false
    if (Math.abs(dx) >= Math.abs(dy) && Math.abs(dx) > 40) go(dx < 0 ? 1 : -1)
    else if (Math.abs(dy) > 40) go(dy < 0 ? 1 : -1) // 上滑=下一月
  }
  const cancelDrag = () => { dragRef.current.active = false }
  const onWheel = (e) => {
    // 仅接管横向滚轮/触控板横滑，避免影响模态框纵向滚动
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY) && Math.abs(e.deltaX) > 20) {
      go(e.deltaX > 0 ? -1 : 1)
    }
  }

  const statusOf = (d) => {
    const ds = d.format('YYYY-MM-DD')
    const inRange = ranges.some(([a, b]) => ds >= a && ds <= b)
    if (!inRange) return 'off' // 未放票
    // dayjs 核心无 isoWeekday 插件：day() 周日=0 … 周六=6，换算为 周一=1 … 周日=7
    const iso = d.day() === 0 ? 7 : d.day()
    return days.includes(iso) ? 'ok' : 'no' // 班期不符
  }

  const first = view.startOf('month')
  const offset = (first.day() === 0 ? 7 : first.day()) - 1 // 周一排首位
  const cells = []
  for (let i = 0; i < offset; i++) cells.push(null)
  for (let d = 1; d <= view.daysInMonth(); d++) cells.push(first.date(d))
  while (cells.length % 7 !== 0) cells.push(null)

  const statusText = { ok: '可兑换（班期+放票区间匹配）', no: '不可兑换（班期不符）', off: '未放票' }

  return (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message={`放票区间：${fmtDateRanges(ranges)}　班期：${fmtDays(days)}（周一=1 … 周日=7）`}
      />
      <div
        className="cal-wrap"
        onTouchStart={dragStart}
        onTouchEnd={dragEnd}
        onTouchCancel={cancelDrag}
        onMouseDown={dragStart}
        onMouseUp={dragEnd}
        onMouseLeave={cancelDrag}
        onWheel={onWheel}
      >
        <div className="cal-head">
          <Button type="text" icon={<ChevronLeftIcon />} disabled={view.isSame(minMonth, 'month')} onClick={() => go(-1)} />
          <div className="cal-title">{view.format('YYYY年M月')}</div>
          <Button type="text" icon={<ChevronRightIcon />} disabled={view.isSame(maxMonth, 'month')} onClick={() => go(1)} />
        </div>
        <div className="cal-week">
          {WEEK_LABELS.map((w) => <div key={w}>{w}</div>)}
        </div>
        <div key={view.format('YYYY-MM')} className="cal-grid">
          {cells.map((d, i) => (
            d ? (
              <div
                key={i}
                className={`cal-cell cal-cell-${statusOf(d)}${d.isSame(now, 'day') ? ' cal-cell-today' : ''}`}
                title={statusText[statusOf(d)]}
              >
                <span className="cal-num">{d.date()}</span>
                {statusOf(d) === 'ok' && <span className="cal-check" />}
              </div>
            ) : (
              <div key={i} className="cal-cell cal-cell-blank" />
            )
          ))}
        </div>
        <div className="cal-legend">
          <span><i className="cal-dot cal-dot-ok" />可兑换</span>
          <span><i className="cal-dot cal-dot-no" />班期不符</span>
          <span><i className="cal-dot cal-dot-off" />未放票</span>
          <span className="cal-swipe-hint">左右/上下滑动切换月份</span>
        </div>
      </div>
    </div>
  )
}

function FlightQuery({ msg, onChanged, priceQuery }) {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [errMsg, setErrMsg] = useState('')
  const [prices, setPrices] = useState(null)
  const [priceLoading, setPriceLoading] = useState(false)
  const [priceTried, setPriceTried] = useState(false)
  const [stopsInfo, setStopsInfo] = useState(null)
  const [timesMap, setTimesMap] = useState(null) // 实时起降时刻/航站楼（查价后覆盖底表静态 CSV 时刻）
  const [seatsInfo, setSeatsInfo] = useState(null) // 实时余票/舱位（按档位）
  const [calRec, setCalRec] = useState(null)
  // 前端查价频控锁（防风控）：发起查价后锁定 priceQuery.min_interval 秒，避免连点
  const [priceLockUntil, setPriceLockUntil] = useState(0)
  // 出发/到达联动选项（基于当前筛选条件可搜到的航线两端城市）
  const [linkOpts, setLinkOpts] = useState({ from: [], to: [] })
  // 结果行勾选（批量转监控）与弹窗状态
  const [selKeys, setSelKeys] = useState([])
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchBusy, setBatchBusy] = useState(false)
  const [batchGroups, setBatchGroups] = useState([])
  const [batchMinSeats, setBatchMinSeats] = useState(0) // 0=不限
  const [batchCabins, setBatchCabins] = useState([]) // 舱位白名单，空=不限

  const watch = Form.useWatch([], form)
  const dateMode = (watch && watch.dateMode) || 'none'

  // 联动选项：watch 筛选条件（含日期模式），debounce 250ms 调 /api/flights/options
  useEffect(() => {
    if (!watch) return undefined
    const timer = setTimeout(() => {
      const from = String(watch.from || '').trim()
      const to = String(watch.to || '').trim()
      let date = ''
      let date_start = ''
      let date_end = ''
      if (watch.dateMode === 'single' && watch.date) date = watch.date.format('YYYY-MM-DD')
      else if (watch.dateMode === 'range' && watch.dateRange && watch.dateRange.length === 2) {
        date_start = watch.dateRange[0].format('YYYY-MM-DD')
        date_end = watch.dateRange[1].format('YYYY-MM-DD')
      }
      api.flightOptions({
        from: from ? cityNameOf(from) : '',
        to: to ? cityNameOf(to) : '',
        product: watch.product === 'all' ? '' : watch.product,
        date,
        date_start,
        date_end,
      }).then((r) => {
        const next = { from: r.from_options || [], to: r.to_options || [] }
        // 当前已选值若不在返回选项里（如手输三字码），并入以避免选中值消失
        if (from && !next.from.includes(from)) next.from = [from, ...next.from]
        if (to && !next.to.includes(to)) next.to = [to, ...next.to]
        setLinkOpts(next)
      }).catch(() => {})
    }, 250)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watch])

  const optProps = (list) => ({
    showSearch: true,
    allowClear: true,
    // 下拉与选中显示完整「城市（IATA）·机场」，城市/三字码/机场名均可模糊搜到
    options: list.map((v) => ({ value: v, label: v })),
    filterOption: (input, opt) => {
      const q = String(input || '').toUpperCase().trim()
      return String(opt.label ?? opt.value).toUpperCase().includes(q)
    },
    notFoundContent: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前条件下没有可联动城市" />,
  })

  const submit = async (values) => {
    const from = String(values.from || '').trim()
    const to = String(values.to || '').trim()
    setLoading(true)
    setErrMsg('')
    setResult(null)
    setPrices(null)
    setStopsInfo(null)
    setTimesMap(null)
    setSeatsInfo(null)
    setPriceTried(false)
    setSelKeys([])
    try {
      const queryParams = {
        from: from ? cityNameOf(from) : '',
        to: to ? cityNameOf(to) : '',
        product: values.product === 'all' ? '' : values.product,
        flight_no: String(values.flight_no || '').trim(),
      }
      if (values.dateMode === 'single' && values.date) queryParams.date = values.date.format('YYYY-MM-DD')
      else if (values.dateMode === 'range' && values.dateRange && values.dateRange.length === 2) {
        queryParams.date_start = values.dateRange[0].format('YYYY-MM-DD')
        queryParams.date_end = values.dateRange[1].format('YYYY-MM-DD')
      }
      const r = await api.flightQuery(queryParams)
      setResult(r)
      // 仅单日查询时并行查当日低价（原价/优惠价）；区间/不限日期不自动查价
      const singleDate = queryParams.date
      if (from && to && singleDate) {
        const pq = priceQuery || {}
        const interval = Math.max(2, Number(pq.min_interval) || 8)
        const now = Date.now()
        setPriceTried(true)
        if (pq.enabled === false) {
          setErrMsg('实时查价已关闭（防风控），航班信息仍可查询；可在「监控管理」重新开启。')
        } else if (priceLockUntil > now) {
          const remain = Math.ceil((priceLockUntil - now) / 1000)
          setErrMsg(`实时查价过于频繁，请 ${remain} 秒后再试（防风控）。`)
        } else {
          setPriceLockUntil(now + interval * 1000)
          setPriceLoading(true)
          try {
            const p = await api.flightPrices({ from: cityNameOf(from), to: cityNameOf(to), date: singleDate })
            if (p.disabled) {
              setErrMsg(p.prices?.normal?.note || '实时查价已关闭（防风控）。')
            } else {
              setPrices(p.prices)
              setStopsInfo(p.stops || null)
              setTimesMap(p.times || null)
              setSeatsInfo(p.seats || null)
            }
          } catch (e) {
            if (e.status === 429 && e.retry_after) {
              setPriceLockUntil(Date.now() + e.retry_after * 1000)
            }
            setErrMsg(e.message)
          } finally {
            setPriceLoading(false)
          }
        }
      }
    } catch (e) {
      setErrMsg(e.message)
    } finally {
      setLoading(false)
    }
  }

  // 展开 effective_dates 区间到逐日（去重排序）
  const expandDates = (ranges) => {
    const seen = new Set()
    for (const [a, b] of ranges || []) {
      let cur = dayjs(a)
      const end = dayjs(b)
      let guard = 0
      while (!cur.isAfter(end) && guard < 400) {
        seen.add(cur.format('YYYY-MM-DD'))
        cur = cur.add(1, 'day')
        guard += 1
      }
    }
    return [...seen].sort()
  }

  // 按「航线+航班号+起降时刻」分组生成弹窗清单：同一天内多个起飞时间的航班各自成组
  const openBatch = (keys) => {
    const recs = (result?.records || []).filter((_, i) => keys.includes(i))
    if (!recs.length) return
    const groups = []
    const idx = {}
    const searchFrom = watch ? form.getFieldValue('date') || null : null
    const searchRange = watch?.dateMode === 'range' && watch.dateRange ? watch.dateRange : null
    for (const r of recs) {
      const gkey = `${r.origin.city}|${r.dest.city}|${r.flight_no || ''}|${r.dep_time || ''}|${r.arr_time || ''}`
      if (!(gkey in idx)) {
        idx[gkey] = groups.length
        groups.push({
          key: gkey,
          from_city: r.origin.city,
          from_iata: r.origin.iata,
          to_city: r.dest.city,
          to_iata: r.dest.iata,
          flight_no: r.flight_no || '',
          dep_time: r.dep_time || '',
          arr_time: r.arr_time || '',
          product: new Set(),
          ranges: [],
          dates: [],
          rows: [],
        })
      }
      const g = groups[idx[gkey]]
      for (const p of String(r.product || '').split('/')) if (p) g.product.add(p)
      for (const range of r.effective_dates || []) g.ranges.push(range)
      g.rows.push(r)
    }
    for (const g of groups) {
      g.product = [...g.product].sort().join('/')
      g.dates = expandDates(g.ranges)
      // prefill 搜索日期：单日选该日，区间选区间内的可飞日期
      if (searchRange) {
        const [a, b] = searchRange
        g.selected = g.dates.filter((d) => d >= a.format('YYYY-MM-DD') && d <= b.format('YYYY-MM-DD'))
      } else if (searchFrom) {
        const ds = searchFrom.format('YYYY-MM-DD')
        g.selected = g.dates.includes(ds) ? [ds] : []
      } else {
        g.selected = []
      }
    }
    setBatchGroups(groups)
    setBatchMinSeats(0)
    setBatchCabins([])
    setBatchOpen(true)
  }

  const confirmBatch = async () => {
    const items = batchGroups
      .filter((g) => g.dates.length)
      .map((g) => {
        const row = g.rows[0] || {}
        const item = {
          from_code: g.from_iata || row.origin?.iata,
          to_code: g.to_iata || row.dest?.iata,
          dates: g.selected,
          product: g.product,
          flight_no: row.flight_no || '',
          dep_time: row.dep_time || '',
          arr_time: row.arr_time || '',
        }
        // 余票/舱位监控条件：至少 N 张（≥2 才显式写入，1/不限=默认）+ 舱位白名单
        if (batchMinSeats >= 2) item.min_seats = batchMinSeats
        if (batchCabins.length) item.cabins = batchCabins
        return item
      })
      .filter((it) => it.dates.length)
    if (!items.length) {
      msg.error('请至少为一组航线选择一个监控日期。')
      return
    }
    setBatchBusy(true)
    try {
      const r = await api.addTasksBatch(items)
      msg.success(r.message || '已创建监控任务')
      setBatchOpen(false)
      setSelKeys([])
      if (onChanged) onChanged()
    } catch (e) {
      msg.error(e.message)
    } finally {
      setBatchBusy(false)
    }
  }

  const goMonitor = (rec, index) => {
    setSelKeys([index])
    openBatch([index])
  }

  // 舱位白名单常见值（可手输任意舱位码，回车添加）
  const CABIN_OPTIONS = ['B', 'C', 'Y', 'Z', 'R', 'H', 'K', 'L', 'M', 'N', 'Q', 'T', 'X', 'E', 'U', 'V', 'W', 'G', 'S']

  const columns = [
    { title: '航班号', dataIndex: 'flight_no', width: 110, fixed: 'left', render: (v) => <span className="mono">{v}</span> },
    { title: '航司', dataIndex: 'carrier', width: 90 },
    {
      title: '出发', key: 'origin', width: 170,
      render: (_, r) => {
        const rt = timesMap?.[r.flight_no]
        const term = r.origin?.terminal || rt?.dep_terminal
        return (
          <Space direction="vertical" size={0}>
            <span>{cityWithProvince(r.origin.city)}</span>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {r.origin.iata} · {(r.origin.airport || '').replace('机场', '')}
              {term ? ` · ${term}` : ''}
            </Typography.Text>
          </Space>
        )
      },
    },
    {
      title: '到达', key: 'dest', width: 170,
      render: (_, r) => {
        const stop = stopsInfo?.[r.flight_no]
        const isStopover = !!(stop && stop.kind === 'stopover')
        const rt = timesMap?.[r.flight_no]
        const term = r.dest?.terminal || rt?.arr_terminal
        return (
          <Space direction="vertical" size={0}>
            <span>{cityWithProvince(r.dest.city)}{isStopover && <Tag color="orange" style={{ marginLeft: 6 }}>经</Tag>}</span>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {r.dest.iata} · {(r.dest.airport || '').replace('机场', '')}
              {term ? ` · ${term}` : ''}
            </Typography.Text>
          </Space>
        )
      },
    },
    {
      title: '起飞', dataIndex: 'dep_time', width: 80,
      render: (v, r) => {
        const rt = timesMap?.[r.flight_no]
        return <span className="mono">{rt?.dep || v || '—'}</span>
      },
    },
    {
      title: '到达', dataIndex: 'arr_time', width: 80,
      render: (v, r) => {
        const rt = timesMap?.[r.flight_no]
        return <span className="mono">{rt?.arr || v || '—'}</span>
      },
    },
    {
      title: '经停', key: 'stops', width: 180,
      render: (_, r) => {
        const s = stopsInfo?.[r.flight_no]
        if (!s) return <Typography.Text type="secondary">—</Typography.Text>
        if (s.kind === 'direct') return <Tag color="green">直飞</Tag>
        if (s.kind === 'stopover') {
          const via = (s.via || []).join('/')
          const details = s.stops_detail || []
          if (!details.length) {
            return <div style={{ fontSize: 12, color: '#8c8c8c' }}>经停{via ? `·${via}` : ''}</div>
          }
          return (
            <div style={{ padding: '3px 0' }}>
              {details.map((d, i) => {
                const where = [d.city, d.airport ? `${d.airport}机场` : '', d.terminal].filter(Boolean).join(' · ')
                return (
                  <div key={i} style={{ fontSize: 12, color: '#8c8c8c', lineHeight: 1.5 }}>
                    {where}
                    {d.arrive && <span className="mono">　{d.arrive}→{d.depart || '?'}{d.stay ? `（${d.stay}）` : ''}</span>}
                  </div>
                )
              })}
            </div>
          )
        }
        if (s.kind === 'transfer') return <Tag color="purple">中转{s.legs}段</Tag>
        return <Typography.Text type="secondary">—</Typography.Text>
      },
    },
    { title: '班期', dataIndex: 'days', width: 120, render: fmtDays },
    {
      title: '首班', key: 'first_date', width: 80,
      render: (_, r) => {
        const first = Array.isArray(r.effective_dates) && r.effective_dates[0] ? String(r.effective_dates[0][0]) : ''
        return first ? <span className="mono">{first.slice(5)}</span> : '—'
      },
    },
    { title: '可飞日期', dataIndex: 'effective_dates', width: 180, render: fmtDateRanges },
    {
      title: '档位', dataIndex: 'product', width: 130,
      render: (v) => {
        const parts = String(v || '').split('/').map((s) => s.trim()).filter(Boolean)
        if (!parts.length) return <Typography.Text type="secondary">—</Typography.Text>
        return parts.map((p) => (
          <Tag key={p} color={p === '666' ? 'gold' : 'purple'} style={{ marginInlineEnd: 4 }}>{p}</Tag>
        ))
      },
    },
    {
      title: '原价', key: 'orig_price', width: 100, fixed: 'right',
      render: (_, r) => {
        if (priceLoading) return <Spin size="small" />
        const v = prices?.normal?.prices?.[r.flight_no]
        if (v != null) return <span className="mono">¥{v}</span>
        if (priceTried) return <Typography.Text type="secondary" style={{ fontSize: 12 }}>未查询到</Typography.Text>
        return <Typography.Text type="secondary">—</Typography.Text>
      },
    },
    {
      title: '优惠价', key: 'plus_price', width: 100, fixed: 'right',
      render: (_, r) => {
        if (priceLoading) return <Spin size="small" />
        const v = prices?.plus?.prices?.[r.flight_no]
        if (v != null) return <span className="mono" style={{ color: '#cf1322', fontWeight: 600 }}>¥{v}</span>
        if (priceTried) return <Typography.Text type="secondary" style={{ fontSize: 12 }}>未查询到</Typography.Text>
        return <Typography.Text type="secondary">—</Typography.Text>
      },
    },
    {
      title: '余票/舱位', key: 'seats', width: 180, fixed: 'right',
      render: (_, r) => {
        if (priceLoading) return <Spin size="small" />
        const s = seatsInfo?.plus?.[r.flight_no] || seatsInfo?.normal?.[r.flight_no]
        if (!s) {
          return priceTried
            ? <Typography.Text type="secondary" style={{ fontSize: 12 }}>未查询到</Typography.Text>
            : <Typography.Text type="secondary">—</Typography.Text>
        }
        return (
          <Space direction="vertical" size={0}>
            <span><span className="mono" style={{ fontWeight: 600 }}>{s.seats}</span> 张</span>
            <span>
              {(s.cabins || []).map((c) => (
                <Tag
                  key={c.cabin}
                  color={Number(c.qty) >= 10 ? 'green' : Number(c.qty) > 0 ? 'blue' : ''}
                  style={{ marginInlineEnd: 2, fontSize: 11 }}
                >
                  {c.cabin}×{c.qty}
                </Tag>
              ))}
            </span>
          </Space>
        )
      },
    },
    {
      title: '操作', key: 'op', width: 170, fixed: 'right',
      render: (_, r, index) => (
        <Space size={4} wrap>
          <Button size="small" icon={<CalendarIcon />} onClick={() => setCalRec(r)}>班期日历</Button>
          <Button size="small" type="primary" ghost icon={<PlusIcon />} onClick={() => goMonitor(r, index)}>
            转为监控任务
          </Button>
        </Space>
      ),
    },
  ]

  const noDateWarn = result && !(watch && (watch.date || (watch.dateRange && watch.dateRange.length === 2)))

  return (
    <div>
      <Card
        style={{ marginBottom: 16 }}
        title="随心飞航线查询"
      >
        <Form form={form} layout="vertical" onFinish={submit} initialValues={{ product: '666', dateMode: 'none' }}>
          <Row gutter={16}>
            <Col xs={24} lg={12}>
              <Form.Item name="from" label="出发地（联动：只显示能查到航线的城市）">
                <Select placeholder="城市 / 机场 / 三字码" {...optProps(linkOpts.from)} />
              </Form.Item>
            </Col>
            <Col xs={24} lg={12}>
              <Form.Item name="to" label="到达地（联动：只显示能查到航线的城市）">
                <Select placeholder="城市 / 机场 / 三字码" {...optProps(linkOpts.to)} />
              </Form.Item>
            </Col>
            <Col xs={24} lg={12}>
              <Form.Item label="可飞日期">
                <Space wrap style={{ width: '100%' }}>
                  <Form.Item name="dateMode" noStyle>
                    <Radio.Group options={[
                      { label: '不限', value: 'none' },
                      { label: '单日', value: 'single' },
                      { label: '区间', value: 'range' },
                    ]} />
                  </Form.Item>
                  {dateMode !== 'none' && (
                    <Form.Item name={dateMode === 'single' ? 'date' : 'dateRange'} noStyle>
                      {dateMode === 'single' ? (
                        <DatePicker style={{ width: 200, flex: 1 }} placeholder="选择单日" />
                      ) : (
                        <DatePicker.RangePicker style={{ width: 260, flex: 1 }} />
                      )}
                    </Form.Item>
                  )}
                </Space>
              </Form.Item>
            </Col>
            <Col xs={24} lg={12}>
              <Form.Item name="product" label="产品档位">
                <Segmented
                  block
                  options={[
                    { label: '666', value: '666' },
                    { label: '2666', value: '2666' },
                    { label: '全部', value: 'all' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col xs={24} lg={12}>
              <Form.Item name="flight_no" label="航班号（可选）">
                <Input placeholder="如 JD / JD5037" />
              </Form.Item>
            </Col>
            <Col xs={24} lg={12}>
              <Form.Item label=" " colon={false}>
                <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
                  <Button onClick={() => { form.resetFields(); setResult(null); setErrMsg(''); setPrices(null); setStopsInfo(null); setTimesMap(null); setSeatsInfo(null); setPriceTried(false); setSelKeys([]) }}>清空</Button>
                  <Button type="primary" htmlType="submit" loading={loading} icon={<CompassIcon />}>查询</Button>
                </Space>
              </Form.Item>
            </Col>
          </Row>
        </Form>
        {errMsg && <Alert style={{ marginTop: 8 }} type="error" showIcon message={errMsg} />}
      </Card>

      {result && (
        <Card
          title={`查询结果（${result.count} 条）`}
          extra={
            <Button
              type="primary"
              icon={<PlusIcon />}
              disabled={!selKeys.length}
              onClick={() => openBatch(selKeys)}
            >
              批量转为监控任务{selKeys.length ? `（${selKeys.length}）` : ''}
            </Button>
          }
        >
          {noDateWarn && (
            <Alert
              style={{ marginBottom: 12 }}
              type="warning"
              showIcon
              message={null}
              description={
                <span style={{ fontSize: 12 }}>
                  已选择日期的前提下才能查询原价/优惠价与经停信息。当前未选择可飞日期，结果列表仅展示底表静态信息（航司/班期/档位）。选择单日后重新查询即可看到实时价格与经停。
                </span>
              }
            />
          )}
          <Table
            rowKey={(_, i) => i}
            size="small"
            pagination={{ pageSize: 20, showSizeChanger: false }}
            dataSource={result.records}
            columns={columns}
            scroll={{ x: 'max-content' }}
            rowSelection={{ selectedRowKeys: selKeys, onChange: setSelKeys }}
            locale={{ emptyText: <Empty description="没有符合条件的航班，换个条件试试" /> }}
          />
        </Card>
      )}

      <Modal
        open={batchOpen}
        title="批量转为监控任务"
        width={680}
        okText="确认创建"
        okButtonProps={{ loading: batchBusy }}
        cancelText="取消"
        onCancel={() => setBatchOpen(false)}
        onOk={confirmBatch}
      >
        <Alert
          style={{ marginBottom: 12 }}
          type="info"
          showIcon
          message="每个航班（航班号 + 起降时刻不同）分别创建独立任务，不合并；同一航班多个日期合入同一条任务。当前统一监控 199 元优惠价档位。"
        />
        <Row gutter={12} style={{ marginBottom: 12 }}>
          <Col span={10}>
            <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>余票条件 · 至少 N 张才提醒</div>
            <Select
              style={{ width: '100%' }}
              value={batchMinSeats}
              onChange={setBatchMinSeats}
              options={[
                { label: '不限（有票即提醒）', value: 0 },
                { label: '至少 1 张', value: 1 },
                { label: '至少 2 张', value: 2 },
                { label: '至少 3 张', value: 3 },
                { label: '至少 4 张', value: 4 },
              ]}
            />
          </Col>
          <Col span={14}>
            <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>舱位白名单（多选/手输，空=不限舱位）</div>
            <Select
              mode="tags"
              style={{ width: '100%' }}
              placeholder="如 B、C、Z、R…，选中任一舱位有余票即提醒"
              value={batchCabins}
              onChange={setBatchCabins}
              options={CABIN_OPTIONS.map((c) => ({ value: c, label: c }))}
              tokenSeparators={[',', '，']}
              maxTagCount="responsive"
            />
          </Col>
        </Row>
        {batchGroups.map((g) => (
          <Card
            key={g.key}
            size="small"
            style={{ marginBottom: 12 }}
            title={
              <Space size={6}>
                <span className="mono" style={{ fontWeight: 600 }}>{g.flight_no || '—'}</span>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{g.dep_time || '--:--'} → {g.arr_time || '--:--'}</Typography.Text>
                <ChevronRightIcon size={11} style={{ color: '#bfbfbf' }} />
                {cityWithProvince(g.from_city)}（{g.from_iata}）<ChevronRightIcon size={11} style={{ color: '#bfbfbf' }} />{cityWithProvince(g.to_city)}（{g.to_iata}）
              </Space>
            }
            extra={<Tag color={g.product.includes('2666') ? 'purple' : 'gold'}>{g.product}</Tag>}
          >
            <div className="muted" style={{ marginBottom: 6, fontSize: 12 }}>
              勾选航班可飞日期共 {g.dates.length} 天，请选择要监控的日期（可多选）：
            </div>
            <Select
              mode="multiple"
              style={{ width: '100%' }}
              placeholder="选择监控日期"
              value={g.selected}
              maxTagCount="responsive"
              onChange={(v) => {
                const next = batchGroups.map((x) => (x.key === g.key ? { ...x, selected: v } : x))
                setBatchGroups(next)
              }}
              options={g.dates.map((d) => ({ value: d, label: d }))}
            />
          </Card>
        ))}
      </Modal>

      <Modal
        open={!!calRec}
        title={calRec ? `${calRec.flight_no}　${cityWithProvince(calRec.origin.city)} → ${cityWithProvince(calRec.dest.city)}` : ''}
        footer={null}
        width={680}
        onCancel={() => setCalRec(null)}
        destroyOnClose
      >
        {calRec && <ScheduleCalendar rec={calRec} />}
      </Modal>
    </div>
  )
}

/* ================= 历史日志 ================= */
function History({ data, autoRefresh, setAutoRefresh, msg, refresh }) {
  const [clearBusy, setClearBusy] = useState('')

  const doClearLog = async () => {
    setClearBusy('log')
    try {
      const r = await api.clearLog()
      msg.success(r.message || '运行日志已清除')
      refresh()
    } catch (e) { msg.error(e.message) } finally { setClearBusy('') }
  }

  return (
    <div>
      <Card
        title="运行日志（最近 200 行）"
        extra={
          <Space>
            <Popconfirm
              title="清除运行日志？"
              description="原日志将归档为 .bak 文件（可找回）。"
              okText="清除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={doClearLog}
            >
              <Button size="small" danger loading={clearBusy === 'log'} icon={<DeleteIcon />}>清除</Button>
            </Popconfirm>
            <Typography.Text type="secondary">自动刷新</Typography.Text>
            <Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} />
          </Space>
        }
      >
        <div className="log-view">
          {(data.logs || []).map((line, i) => {
            const cls = line.includes('错误') || line.includes('[阻断]') || line.includes('E00001') ? 'err'
              : line.includes('警告') || line.includes('退避') ? 'warn' : ''
            return <div key={i} className={cls}>{line}</div>
          })}
          {(data.logs || []).length === 0 && <div className="muted">暂无日志输出。</div>}
        </div>
      </Card>
    </div>
  )
}

/* ================= App ================= */
// 全局主题：基于 antd 开源设计 token 的现代化定制（主色/圆角/控件密度），替代默认观感
const appTheme = {
  token: {
    colorPrimary: '#2f6bff',
    colorInfo: '#2f6bff',
    borderRadius: 8,
    controlHeight: 34,
  },
  components: {
    Button: { fontWeight: 500 },
    Card: { borderRadiusLG: 12 },
    Menu: { itemBorderRadius: 8, itemHeight: 40 },
    Table: { headerBg: '#f7f8fa' },
    Switch: { trackHeight: 22 },
  },
}

export default function App() {
  const { message } = AntApp.useApp()
  const [tab, setTab] = useState('overview')
  const [historyAuto, setHistoryAuto] = useState(false)
  const { data, loading, error, refresh } = usePolling(
    api.state,
    tab === 'history' ? (historyAuto ? 15000 : 30000) : 15000,
  )

  const msg = {
    success: (t) => message.success(t),
    error: (t) => message.error(t),
    info: (t) => message.info(t),
  }

  const toggleStatus = async () => {
    if (!data) return
    const next = data.config.status === 'running' ? 'stopped' : 'running'
    try {
      await api.setStatus(next)
      msg.success(next === 'running' ? '监控已启动' : '监控已停止')
      refresh()
    } catch (e) { msg.error(e.message) }
  }

  if (loading && !data) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
        <Spin size="large" tip="加载中…" />
      </div>
    )
  }
  if (error && !data) {
    return (
      <Result
        status="warning"
        title="无法连接控制台 API"
        subTitle={error}
        extra={<Button type="primary" onClick={refresh}>重试</Button>}
      />
    )
  }

  return (
    <ConfigProvider theme={appTheme}>
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Header className="app-header">
        <Row align="middle" justify="space-between" style={{ height: '100%' }}>
          <Col>
            <Space size={10}>
              <BrandIcon size={22} style={{ color: 'var(--color-primary)' }} />
              <span className="brand">海航监控</span>
            </Space>
          </Col>
          <Col>
            <Space>
              <Tag color={data.config.status === 'running' ? 'green' : 'default'}>
                <span className="status-dot" style={{ background: data.config.status === 'running' ? '#52c41a' : '#bfbfbf' }} />
                {data.config.status === 'running' ? '监控运行中' : '监控已停止'}
              </Tag>
            </Space>
          </Col>
        </Row>
      </Layout.Header>

      <Menu
        className="app-menu"
        mode="horizontal"
        selectedKeys={[tab]}
        items={TABS}
        onClick={({ key }) => setTab(key)}
      />

      <Layout.Content className="app-content">
        {/* 所有 tab 常驻挂载、display 切换，避免切 tab 丢失组件内状态（查询结果/表单/勾选等） */}
        <div className="page">
          <div style={{ display: tab === 'overview' ? 'block' : 'none' }}>
            <Overview data={data} onGo={setTab} onToggleStatus={toggleStatus} />
          </div>
          <div style={{ display: tab === 'tasks' ? 'block' : 'none' }}>
            <Tasks data={data} onChanged={refresh} msg={msg} onGo={setTab} />
          </div>
          <div style={{ display: tab === 'flights' ? 'block' : 'none' }}>
            <FlightQuery
              msg={msg}
              priceQuery={data?.config?.price_query}
              onChanged={refresh}
            />
          </div>
          <div style={{ display: tab === 'notify' ? 'block' : 'none' }}>
            <Notifications data={data} onChanged={refresh} msg={msg} />
          </div>
          <div style={{ display: tab === 'monitor' ? 'block' : 'none' }}>
            <MonitorSettings data={data} onChanged={refresh} msg={msg} />
          </div>
          <div style={{ display: tab === 'history' ? 'block' : 'none' }}>
            <History data={data} autoRefresh={historyAuto} setAutoRefresh={setHistoryAuto} msg={msg} refresh={refresh} />
          </div>
        </div>
      </Layout.Content>
    </Layout>
    </ConfigProvider>
  )
}