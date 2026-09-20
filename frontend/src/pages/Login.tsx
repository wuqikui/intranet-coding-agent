import { useState } from 'react'
import { Card, Form, Input, Button, Typography, Alert, Space } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../store/auth'

const { Title, Text } = Typography

export default function Login() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const login = useAuthStore((s) => s.login)
  const navigate = useNavigate()

  const onSubmit = async (values: { username: string; password: string }) => {
    setLoading(true)
    setError('')
    try {
      await login(values.username, values.password)
      navigate('/')
    } catch (e: any) {
      setError(e.message || '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #1677ff 0%, #1f3a8a 100%)',
      }}
    >
      <Card style={{ width: 400, boxShadow: '0 8px 32px rgba(0,0,0,0.2)' }}>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <div style={{ textAlign: 'center' }}>
            <Title level={3} style={{ margin: 0 }}>
              内网 Coding Agent
            </Title>
            <Text type="secondary">离线多用户 AI 编码平台</Text>
          </div>

          {error && <Alert type="error" message={error} showIcon closable onClose={() => setError('')} />}

          <Form name="login" layout="vertical" onFinish={onSubmit} size="large">
            <Form.Item
              name="username"
              rules={[{ required: true, message: '请输入用户名' }]}
            >
              <Input prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
            </Form.Item>
            <Form.Item
              name="password"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password
                prefix={<LockOutlined />}
                placeholder="密码"
                autoComplete="current-password"
              />
            </Form.Item>
            <Form.Item style={{ marginBottom: 0 }}>
              <Button type="primary" htmlType="submit" block loading={loading}>
                登 录
              </Button>
            </Form.Item>
          </Form>

          <Text type="secondary" style={{ fontSize: 12, display: 'block', textAlign: 'center' }}>
            支持本地账号 / LDAP / AD · 仅内网访问
          </Text>
        </Space>
      </Card>
    </div>
  )
}
