import { useEffect, useState } from 'react'
import {
  Card,
  Tabs,
  Table,
  Button,
  Space,
  Typography,
  Tag,
  Form,
  Input,
  Select,
  message,
  Statistic,
  Row,
  Col,
  theme,
} from 'antd'
import {
  ReloadOutlined,
  DownloadOutlined,
  DatabaseOutlined,
  TeamOutlined,
  AuditOutlined,
  CloudUploadOutlined,
} from '@ant-design/icons'
import api from '../api/client'

const { Paragraph } = Typography

interface AuditRow {
  id: string
  timestamp: string
  user_id: string
  role: string
  action: string
  model: string
  tool: string
  duration_ms: number
  tokens_in: number
  tokens_out: number
}

interface UserRow {
  id: number
  username: string
  role: string
}

const ROLES = [
  { value: 'admin', label: '管理员' },
  { value: 'dev', label: '开发者' },
  { value: 'readonly', label: '只读' },
]

export default function Admin() {
  const { token: themeToken } = theme.useToken()
  const [users, setUsers] = useState<UserRow[]>([])
  const [audit, setAudit] = useState<AuditRow[]>([])
  const [stats, setStats] = useState<any>({})
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm()

  const loadUsers = () =>
    api.get('/admin/users').then((r: any) => setUsers(r?.items || [])).catch(() => {})
  const loadAudit = () =>
    api.get('/admin/audit?limit=100').then((r: any) => setAudit(r?.items || [])).catch(() => {})
  const loadStats = () =>
    api.get('/admin/stats').then((r: any) => setStats(r || {})).catch(() => {})

  useEffect(() => {
    Promise.all([loadUsers(), loadAudit(), loadStats()])
  }, [])

  const onRefresh = async () => {
    setLoading(true)
    await Promise.all([loadUsers(), loadAudit(), loadStats()])
    setLoading(false)
    message.success('已刷新')
  }

  const onCreateUser = async (values: any) => {
    await api.post('/admin/users', values)
    message.success('用户已创建')
    form.resetFields()
    loadUsers()
  }

  const onToggleMaintenance = async (enable: boolean) => {
    await api.post('/admin/maintenance', { enable })
    message[enable ? 'warning' as const : 'success' as const](enable ? '已开启出网维护模式（全程审计）' : '已关闭出网维护模式')
    loadStats()
  }

  const onImportModel = async () => {
    const url = prompt('输入模型下载 URL（仅管理员临时联网）：')
    if (!url) return
    const sha = prompt('输入预期 sha256（必填，用于校验）：')
    if (!sha) return
    await api.post('/admin/import-model', { url, sha256: sha })
    message.success('已提交导入任务，请到日志查看进度')
    loadStats()
  }

  const auditColumns = [
    { title: '时间', dataIndex: 'timestamp', width: 180, render: (t: string) => new Date(t).toLocaleString() },
    { title: '用户', dataIndex: 'user_id', width: 120 },
    { title: '角色', dataIndex: 'role', width: 90, render: (r: string) => <Tag>{r}</Tag> },
    { title: '动作', dataIndex: 'action', width: 140 },
    { title: '模型', dataIndex: 'model', width: 160 },
    { title: '工具', dataIndex: 'tool', width: 120 },
    { title: '耗时(ms)', dataIndex: 'duration_ms', width: 100 },
    { title: '入tokens', dataIndex: 'tokens_in', width: 90 },
    { title: '出tokens', dataIndex: 'tokens_out', width: 90 },
  ]

  const userColumns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '用户名', dataIndex: 'username', width: 200 },
    {
      title: '角色',
      dataIndex: 'role',
      width: 120,
      render: (r: string) => <Tag color={r === 'admin' ? 'red' : r === 'dev' ? 'blue' : 'default'}>{r}</Tag>,
    },
  ]

  return (
    <div style={{ padding: 16, background: themeToken.colorBgLayout, height: 'calc(100vh - 64px)', overflow: 'auto' }}>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="用户总数" value={users.length} prefix={<TeamOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="审计记录" value={audit.length} prefix={<AuditOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="维护模式"
              value={stats.maintenance_mode ? '开启' : '关闭'}
              prefix={<CloudUploadOutlined />}
              valueStyle={{ color: stats.maintenance_mode ? '#fa8c16' : '#52c41a' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="推理后端" value={stats.inference_backend || 'mock'} prefix={<DatabaseOutlined />} />
          </Card>
        </Col>
      </Row>

      <Tabs
        defaultActiveKey="users"
        items={[
          {
            key: 'users',
            label: '用户管理',
            children: (
              <Space direction="vertical" style={{ width: '100%' }}>
                <Card title="新增用户" size="small">
                  <Form form={form} layout="inline" onFinish={onCreateUser}>
                    <Form.Item name="username" rules={[{ required: true, message: '用户名' }]}>
                      <Input placeholder="用户名" />
                    </Form.Item>
                    <Form.Item name="password" rules={[{ required: true, message: '密码' }]}>
                      <Input.Password placeholder="密码" />
                    </Form.Item>
                    <Form.Item name="role" rules={[{ required: true }]} initialValue="dev">
                      <Select style={{ width: 120 }} options={ROLES} />
                    </Form.Item>
                    <Form.Item>
                      <Button type="primary" htmlType="submit">
                        创建
                      </Button>
                    </Form.Item>
                  </Form>
                </Card>
                <Table
                  rowKey="id"
                  size="small"
                  columns={userColumns}
                  dataSource={users}
                  pagination={{ pageSize: 10 }}
                />
              </Space>
            ),
          },
          {
            key: 'audit',
            label: '审计日志',
            children: (
              <Table
                rowKey="id"
                size="small"
                columns={auditColumns}
                dataSource={audit}
                pagination={{ pageSize: 20 }}
                scroll={{ x: 1100 }}
              />
            ),
          },
          {
            key: 'model',
            label: '模型与维护',
            children: (
              <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                <Card title="出网维护模式" size="small">
                  <Paragraph type="secondary">
                    临时联网用于下载模型/依赖；全程审计。完成后务必关闭。
                  </Paragraph>
                  <Space>
                    <Button
                      type="primary"
                      danger
                      icon={<CloudUploadOutlined />}
                      onClick={() => onToggleMaintenance(true)}
                      disabled={!!stats.maintenance_mode}
                    >
                      开启出网
                    </Button>
                    <Button onClick={() => onToggleMaintenance(false)} disabled={!stats.maintenance_mode}>
                      关闭出网
                    </Button>
                  </Space>
                </Card>
                <Card title="离线模型导入" size="small">
                  <Paragraph type="secondary">
                    临时联网下载 → sha256 校验 → 入内网模型库 → 后端注册。运行时禁止联网。
                  </Paragraph>
                  <Button type="primary" icon={<DownloadOutlined />} onClick={onImportModel}>
                    导入模型
                  </Button>
                </Card>
              </Space>
            ),
          },
        ]}
        tabBarExtraContent={
          <Button icon={<ReloadOutlined />} onClick={onRefresh} loading={loading}>
            刷新
          </Button>
        }
      />
    </div>
  )
}
