import React, { useEffect, useState } from 'react'
import {
  Alert, App as AntApp, Button, Card, Col, DatePicker, Descriptions, Empty, Form, Input,
  InputNumber, Layout, Menu, Popconfirm, Result, Row, Segmented, Select, Space, Spin,
  Statistic, Steps, Switch, Table, Tag, TimePicker, Tooltip, Typography,
} from 'antd'
import {
  DashboardOutlined, DeleteOutlined, FileTextOutlined, PlusOutlined, PoweroffOutlined,
  ProjectOutlined, RocketOutlined, SafetyCertificateOutlined, SendOutlined, SettingOutlined,
  WechatOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, formatTs } from './api.js'
import { Sparkline, usePolling } from './components.jsx'

const TABS = [
  { key: 'overview', icon: <DashboardOutlined />, label: '总览' },
  { key: 'tasks', icon: <ProjectOutlined />, label: '任务' },
  { key: 'tickets', icon: <SafetyCertificateOutlined />, label: '票据管理' },
  { key: 'settings', icon: <SettingOutlined />, label: '设置' },
  { key: 'history', icon: <FileTextOutlined />, label: '历史日志' },
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
  const hits = (data.history || []).filter((r) => Number(r.price) <= 500).slice(0, 5)

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
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="监控状态"
              value={running ? '运行中' : '已停止'}
              valueStyle={{ color: running ? 'var(--color-success)' : '#8c8c8c' }}
            />
            <div className="muted" style={{ margin: '4px 0 12px' }}>daemon 按监控时段轮询抓价</div>
            <Button
              type={running ? 'default' : 'primary'}
              icon={running ? <PoweroffOutlined /> : undefined}
              onClick={onToggleStatus}
            >
              {running ? '停止监控' : '启动监控'}
            </Button>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="监控任务" value={stats.task_count} />
            <div className="muted" style={{ margin: '4px 0 12px' }}>启用 {stats.enabled_count} 个</div>
            <Button type="link" style={{ paddingLeft: 0 }} onClick={() => onGo('tasks')}>管理任务</Button>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="近 24h 命中" value={stats.hit_count_24h} valueStyle={{ color: 'var(--color-low-price)' }} />
            <div className="muted" style={{ margin: '4px 0 12px' }}>价格 ≤ 目标价的记录数</div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="历史记录" value={stats.history_count} />
            <div className="muted" style={{ margin: '4px 0 12px' }}>最近 50 条可查</div>
            <Button type="link" style={{ paddingLeft: 0 }} onClick={() => onGo('history')}>查看历史</Button>
          </Card>
        </Col>
      </Row>

      {!cfg.plus_ticket.configured && (
        <Alert
          style={{ marginTop: 16 }}
          type="warning"
          showIcon
          message="PLUS 票据未配置，PLUS 任务将无法查询。"
          action={<Button size="small" type="link" onClick={() => onGo('tickets')}>去配置</Button>}
        />
      )}

      <Card
        style={{ marginTop: 16 }}
        title="最近低价命中"
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
function Tasks({ data, cityOptions, onChanged, msg }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  const cfg = data.config
  const tasks = data.tasks || []

  const taskHistory = (t) => {
    const end = t.date_end && t.date_end !== t.date ? t.date_end : t.date
    return (data.history || []).filter(
      (r) => String(r.from).includes(t.from_code)
        && String(r.to).includes(t.to_code)
        && String(r.date) >= String(t.date)
        && String(r.date) <= String(end),
    )
  }
  const sparkValues = (t) => taskHistory(t).map((r) => Number(r.price)).slice(-7)
  const isHit = (t) => taskHistory(t).some((r) => Number(r.price) <= Number(t.target_price))
  const lastPrice = (t) => {
    const vals = taskHistory(t).map((r) => Number(r.price))
    return vals.length ? `¥${vals[vals.length - 1]}` : ''
  }

  const submit = async (values) => {
    if (!values.range || values.range.length === 0) {
      msg.error('请先选择监控日期。')
      return
    }
    if (!values.from) { msg.error('请先选择出发地。'); return }
    if (!values.to) { msg.error('请先选择到达地。'); return }
    if (values.fare === 'plus' && !cfg.plus_ticket.configured) {
      msg.error('你选择了 PLUS专享，但尚未配置 PLUS 票据，请先到「票据管理」粘贴抓包 cURL。')
      return
    }
    const [start, end] = values.range
    setBusy(true)
    try {
      const r = await api.addTask({
        date: start.format('YYYY-MM-DD'),
        date_end: end ? end.format('YYYY-MM-DD') : '',
        from_city: values.from,
        to_city: values.to,
        target_price: values.price,
        fare_type: values.fare,
      })
      msg.success(r.message || '任务已添加')
      form.resetFields()
      onChanged()
    } catch (e) {
      msg.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  const toggleEnabled = async (t) => {
    try {
      await api.setTaskEnabled(t.id, !t.enabled)
      onChanged()
    } catch (e) { msg.error(e.message) }
  }

  const doDelete = async (t) => {
    try {
      await api.deleteTask(t.id)
      msg.success(`任务已删除：${t.from_code} → ${t.to_code}`)
      onChanged()
    } catch (e) { msg.error(e.message) }
  }

  const columns = [
    {
      title: '航线', key: 'route',
      render: (_, t) => (
        <Space direction="vertical" size={0}>
          <span>{shortCity(t.from_city)} → {shortCity(t.to_city)}</span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{t.from_code} → {t.to_code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '监控日期', key: 'date',
      render: (_, t) => {
        const single = !t.date_end || t.date_end === t.date
        return single ? <span>{t.date}</span> : <span>{t.date} ~ {t.date_end}</span>
      },
    },
    { title: '类型', dataIndex: 'fare_type', width: 110, render: fareTag },
    {
      title: '目标价', key: 'target', width: 130,
      render: (_, t) => (
        <Space direction="vertical" size={4}>
          <span>≤ ¥{t.target_price}</span>
          <HitTag hit={isHit(t)} />
        </Space>
      ),
    },
    {
      title: '最近价格', key: 'spark', width: 190,
      render: (_, t) => {
        const vals = sparkValues(t)
        return (
          <Space direction="vertical" size={2}>
            <Sparkline values={vals} hit={isHit(t)} />
            {lastPrice(t) && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{lastPrice(t)} · 近 {Math.min(vals.length, 7)} 次</Typography.Text>}
          </Space>
        )
      },
    },
    {
      title: '状态', key: 'enabled', width: 90,
      render: (_, t) => <Switch size="small" checked={t.enabled} onChange={() => toggleEnabled(t)} />,
    },
    {
      title: '操作', key: 'op', width: 70,
      render: (_, t) => (
        <Popconfirm
          title={`删除任务 ${t.from_code} → ${t.to_code}`}
          description="删除后 daemon 将不再查询，价格历史保留。不可撤销。"
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => doDelete(t)}
        >
          <Button type="text" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ]

  return (
    <div>
      <Card title="添加监控任务">
        <Form form={form} layout="vertical" onFinish={submit} initialValues={{ price: 199, fare: 'normal' }}>
          <Row gutter={16}>
            <Col xs={24} md={10}>
              <Form.Item name="range" label="监控日期（区间内命中即提醒）">
                <DatePicker.RangePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={7}>
              <Form.Item name="from" label="出发地" rules={[{ required: true, message: '请选择出发地' }]}>
                <Select
                  showSearch
                  placeholder="选择城市 / 输入三字码"
                  options={cityOptions.map((v) => ({ value: v, label: v }))}
                  filterOption={(input, opt) => String(opt.value).toUpperCase().includes(input.toUpperCase().trim())}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={7}>
              <Form.Item name="to" label="到达地" rules={[{ required: true, message: '请选择到达地' }]}>
                <Select
                  showSearch
                  placeholder="选择城市 / 输入三字码"
                  options={cityOptions.map((v) => ({ value: v, label: v }))}
                  filterOption={(input, opt) => String(opt.value).toUpperCase().includes(input.toUpperCase().trim())}
                />
              </Form.Item>
            </Col>
            <Col xs={12} md={6}>
              <Form.Item name="price" label="提醒阈值（元）">
                <InputNumber min={1} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={12} md={6}>
              <Form.Item name="fare" label="票价类型">
                <Segmented
                  block
                  options={[{ label: '普通票价', value: 'normal' }, { label: 'PLUS专享', value: 'plus' }]}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={12} style={{ display: 'flex', alignItems: 'flex-end', paddingBottom: 24 }}>
              <Button type="primary" htmlType="submit" loading={busy} icon={<PlusOutlined />}>添加任务</Button>
            </Col>
          </Row>
        </Form>
      </Card>

      <Card
        style={{ marginTop: 16 }}
        title={`监控任务（${tasks.length}）`}
        extra={<Typography.Text type="secondary">15 秒自动刷新 · 区间任务每轮逐日查询</Typography.Text>}
      >
        <Table
          rowKey="id"
          size="middle"
          pagination={false}
          dataSource={tasks}
          columns={columns}
          scroll={{ x: 'max-content' }}
          locale={{ emptyText: <Empty description="还没有监控任务，先在上方添加一个" /> }}
        />
      </Card>
    </div>
  )
}

/* ================= 票据管理 ================= */
function TicketCard({ title, fareType, ticket, onChanged, msg }) {
  const [raw, setRaw] = useState('')
  const [revealed, setRevealed] = useState('')
  const [secondsLeft, setSecondsLeft] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

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

  const save = async () => {
    setError('')
    if (!raw.trim()) { setError('内容为空，未保存。'); return }
    setBusy(true)
    try {
      const r = await api.saveTicket(fareType, raw)
      if (!r.ok) { setError(r.error || '保存失败'); return }
      msg.success(`${title} 票据已保存，daemon 下一轮自动生效`)
      setRaw('')
      onChanged()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const reveal = async () => {
    if (revealed) { setRevealed(''); setSecondsLeft(0); return }
    try {
      const r = await api.ticketRaw(fareType)
      setRevealed(r.raw || '')
      setSecondsLeft(30)
    } catch (e) {
      msg.error(e.message)
    }
  }

  const bodyOk = ticket.configured && ticket.length > 0

  return (
    <Card
      title={title}
      extra={<Tag color={ticket.configured ? 'green' : 'orange'}>{ticket.configured ? '已配置' : '未配置'}</Tag>}
    >
      <Descriptions size="small" column={1}>
        <Descriptions.Item label="请求地址">
          {ticket.configured ? (
            <Tooltip title={ticket.url}>
              <Typography.Text ellipsis style={{ maxWidth: 280, display: 'block' }}>{ticket.url}</Typography.Text>
            </Tooltip>
          ) : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="体积">{(ticket.length / 1024).toFixed(1)} KB</Descriptions.Item>
      </Descriptions>

      <Space style={{ margin: '8px 0 12px' }} size={[8, 8]} wrap>
        <Tag color={ticket.configured ? 'green' : 'red'}>{ticket.configured ? '✓' : '✗'} 请求地址已解析</Tag>
        <Tag color={bodyOk ? 'green' : 'red'}>{bodyOk ? '✓' : '✗'} 请求体完整</Tag>
        {ticket.configured && (
          <Button size="small" type="link" onClick={reveal}>{revealed ? '收起明文' : '查看明文'}</Button>
        )}
      </Space>

      {revealed && (
        <div className="raw-box">
          <pre>{revealed}</pre>
          <div className="muted" style={{ fontSize: 12 }}>{secondsLeft} 秒后自动收起</div>
        </div>
      )}

      <Input.TextArea
        rows={6}
        placeholder={`粘贴 ${title} 的完整 cURL（含 Cookie、token、hnairSign）…`}
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
      />
      {error && <Alert style={{ marginTop: 8 }} type="error" showIcon message={error} />}
      <div style={{ marginTop: 12, textAlign: 'right' }}>
        <Space>
          <Button onClick={() => setRaw('')}>清空</Button>
          <Button type="primary" loading={busy} onClick={save}>保存票据</Button>
        </Space>
      </div>
    </Card>
  )
}

function Tickets({ data, onChanged, msg }) {
  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <TicketCard title="PLUS 专享票据" fareType="plus" ticket={data.config.plus_ticket} onChanged={onChanged} msg={msg} />
        </Col>
        <Col xs={24} lg={12}>
          <TicketCard title="普通票价票据" fareType="normal" ticket={data.config.normal_ticket} onChanged={onChanged} msg={msg} />
        </Col>
      </Row>
      <Card style={{ marginTop: 16 }}>
        <Typography.Text type="secondary">
          PLUS 通道抓 <span className="mono">ffl/airLowFareSearch</span>，普通票价抓 <span className="mono">airLowFareSearch</span>，两份要各自抓取。票据保存后 daemon 下一轮自动生效，无需重启。
        </Typography.Text>
      </Card>
    </div>
  )
}

/* ================= 设置 ================= */
function Settings({ data, onChanged, msg }) {
  const cfg = data.config
  const feishu = cfg.feishu || {}
  const [fsForm] = Form.useForm()
  const [keysRaw, setKeysRaw] = useState('')
  const [busy, setBusy] = useState('')
  const [testMsg, setTestMsg] = useState('')
  const [start, setStart] = useState(() => dayjs(cfg.monitor_window?.start || '07:00', 'HH:mm'))
  const [end, setEnd] = useState(() => dayjs(cfg.monitor_window?.end || '23:00', 'HH:mm'))
  const [proxy, setProxy] = useState(cfg.proxy || '')
  const [signRefresh, setSignRefresh] = useState(cfg.sign_refresh || false)

  const saveKeys = async () => {
    setBusy('keys')
    try {
      await api.saveSendKeys(keysRaw)
      msg.success('SendKey 已保存，daemon 下一轮生效')
      setKeysRaw('')
      setTestMsg('')
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
        setTestMsg(`测试消息发送完成：成功 ${r.success} / 总计 ${r.total}`)
        msg.success(`测试消息：成功 ${r.success} / ${r.total}`)
      } else {
        setTestMsg('测试消息发送失败，请检查 SendKey 是否正确')
        msg.error('测试消息发送失败')
      }
    } catch (e) { msg.error(e.message) } finally { setBusy('') }
  }

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
      if (r.success) msg.success('测试消息已发送，请到飞书查看')
      else msg.error(r.error || '测试消息发送失败')
    } catch (e) { msg.error(e.message) } finally { setBusy('') }
  }

  const wechatSteps = [
    { title: '打开 Server酱官网', description: '浏览器访问 sct.ftqq.com，用微信扫码登录' },
    { title: '复制 SendKey', description: '在「SendKey」页复制 SCT 开头的一串字符' },
    { title: '粘贴到输入框', description: '支持每行一个，多个微信号可各配一个' },
    { title: '保存并验证', description: '点击下方「保存并验证」' },
    { title: '发送测试消息', description: '微信收到即绑定成功，后续低价与告警自动推送' },
  ]

  return (
    <div>
      {/* 通知渠道 */}
      <Card
        style={{ marginBottom: 16 }}
        title={<Space><WechatOutlined style={{ color: '#07c160' }} />通知渠道</Space>}
        extra={<Typography.Text type="secondary">低价提醒、Token 过期告警会同时推送到已配置的渠道</Typography.Text>}
      >
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card
              type="inner"
              title="微信推送（Server酱）"
              extra={
                <Tag color={cfg.send_keys_count > 0 ? 'green' : 'orange'}>
                  {cfg.send_keys_count > 0 ? `已绑定 ${cfg.send_keys_count} 个` : '未配置'}
                </Tag>
              }
            >
              <Steps
                direction="vertical"
                size="small"
                current={5}
                items={wechatSteps}
                style={{ marginBottom: 16 }}
              />
              <Input.TextArea
                rows={4}
                placeholder="每行粘贴一个 SendKey，例如 SCT123..."
                value={keysRaw}
                onChange={(e) => setKeysRaw(e.target.value)}
              />
              <Space style={{ marginTop: 12 }}>
                <Button type="primary" loading={busy === 'keys'} onClick={saveKeys}>保存并验证</Button>
                <Button loading={busy === 'test'} onClick={testAlert}>发送测试消息</Button>
              </Space>
              {testMsg && (
                <Alert
                  style={{ marginTop: 8 }}
                  type={testMsg.includes('失败') ? 'error' : 'success'}
                  showIcon
                  message={testMsg}
                />
              )}
            </Card>
          </Col>

          <Col xs={24} lg={12}>
            <Card
              type="inner"
              title="飞书通知（自建应用机器人）"
              extra={
                feishu.configured
                  ? <Tag color="green">{feishu.has_secret ? `已配置 ${feishu.app_id}` : '配置不完整'}</Tag>
                  : <Tag color="orange">未配置</Tag>
              }
            >
              {feishu.configured && (
                <Alert
                  style={{ marginBottom: 12 }}
                  type="success"
                  showIcon
                  message={`当前 App ID：${feishu.app_id}；接收人：${feishu.receiver || '—'}`}
                />
              )}
              <Form form={fsForm} layout="vertical">
                <Form.Item name="app_id" label="App ID">
                  <Input placeholder="cli_ 开头；已配置可留空不修改" />
                </Form.Item>
                <Form.Item name="app_secret" label="App Secret">
                  <Input.Password placeholder="已配置可留空不修改（不会回显）" />
                </Form.Item>
                <Form.Item name="receiver" label="接收人">
                  <Input placeholder="你的飞书邮箱，或 ou_ 开头的 OpenID" />
                </Form.Item>
              </Form>
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 12 }}
                message={
                  <span>
                    首次使用：在飞书开放平台创建「企业自建应用」→ 添加「机器人」能力 → 开通
                    <span className="mono"> im:message:send_as_bot </span> 权限 → 发布版本 → 拿到 App ID / App Secret，
                    再填入上方并点「保存配置」→「发送测试消息」。完整图文步骤见项目目录
                    <Typography.Text code>docs/feishu_notify_guide.md</Typography.Text>。
                  </span>
                }
              />
              <Space>
                <Button type="primary" loading={busy === 'feishu-save'} onClick={saveFeishu} icon={<SendOutlined />}>保存配置</Button>
                <Button loading={busy === 'feishu-test'} onClick={sendTestFeishu}>发送测试消息</Button>
              </Space>
            </Card>
          </Col>
        </Row>
      </Card>

      {/* 监控时段 */}
      <Card style={{ marginBottom: 16 }} title="每日监控时段">
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

/* ================= 历史日志 ================= */
function History({ data, autoRefresh, setAutoRefresh }) {
  const columns = [
    { title: '时间', dataIndex: 'ts', width: 160, render: (v) => formatTs(v) },
    { title: '日期', dataIndex: 'date', width: 110 },
    { title: '航线', key: 'route', render: (_, r) => `${shortCity(r.from)} → ${shortCity(r.to)}` },
    { title: '类型', dataIndex: 'fare_type', width: 100, render: fareTag },
    { title: '航班', dataIndex: 'flight', width: 100, render: (v) => <span className="mono">{v}</span> },
    {
      title: '价格(元)', dataIndex: 'price', width: 100, align: 'right',
      render: (v) => {
        const low = Number(v) <= 500
        return <span style={{ fontWeight: low ? 700 : 400, color: low ? 'var(--color-low-price)' : undefined }}>{v}</span>
      },
    },
  ]

  return (
    <div>
      <Card style={{ marginBottom: 16 }} title="价格历史（最近 50 条）" extra={<Typography.Text type="secondary">命中行红色高亮</Typography.Text>}>
        <Table
          rowKey={(_, i) => i}
          size="small"
          pagination={false}
          dataSource={data.history || []}
          columns={columns}
          scroll={{ x: 'max-content' }}
          rowClassName={(r) => (Number(r.price) <= 500 ? 'hit-row' : '')}
          locale={{ emptyText: <Empty description="暂无价格历史，daemon 抓到价格后才会写入" /> }}
        />
      </Card>

      <Card
        title="运行日志（最近 200 行）"
        extra={
          <Space>
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
export default function App() {
  const { message } = AntApp.useApp()
  const [tab, setTab] = useState('overview')
  const [historyAuto, setHistoryAuto] = useState(false)
  const [cityOptions, setCityOptions] = useState([])
  const { data, loading, error, refresh } = usePolling(
    api.state,
    tab === 'history' ? (historyAuto ? 15000 : 30000) : 15000,
  )

  useEffect(() => {
    api.cityOptions().then((r) => setCityOptions(r.options || [])).catch(() => {})
  }, [])

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
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Header className="app-header">
        <Row align="middle" justify="space-between" style={{ height: '100%' }}>
          <Col>
            <Space size={10}>
              <RocketOutlined style={{ fontSize: 22, color: 'var(--color-primary)' }} />
              <span className="brand">海航监控</span>
            </Space>
          </Col>
          <Col>
            <Space>
              <Tag color={data.config.status === 'running' ? 'green' : 'default'}>
                <span className="status-dot" style={{ background: data.config.status === 'running' ? '#52c41a' : '#bfbfbf' }} />
                {data.config.status === 'running' ? '监控运行中' : '监控已停止'}
              </Tag>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setTab('tasks')}>添加任务</Button>
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
        <div className="page">
          {tab === 'overview' && <Overview data={data} onGo={setTab} onToggleStatus={toggleStatus} />}
          {tab === 'tasks' && <Tasks data={data} cityOptions={cityOptions} onChanged={refresh} msg={msg} />}
          {tab === 'tickets' && <Tickets data={data} onChanged={refresh} msg={msg} />}
          {tab === 'settings' && <Settings data={data} onChanged={refresh} msg={msg} />}
          {tab === 'history' && <History data={data} autoRefresh={historyAuto} setAutoRefresh={setHistoryAuto} />}
        </div>
      </Layout.Content>
    </Layout>
  )
}