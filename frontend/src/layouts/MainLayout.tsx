import { useEffect } from 'react'
import { Layout, Menu, Avatar, Dropdown, Space, Typography, theme } from 'antd'
import {
  MessageOutlined,
  SettingOutlined,
  LogoutOutlined,
  UserOutlined,
  FolderOutlined,
} from '@ant-design/icons'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../store/auth'

const { Header, Sider, Content } = Layout
const { Text } = Typography

const roleLabels: Record<string, string> = {
  admin: '管理员',
  dev: '开发者',
  readonly: '只读',
}

export default function MainLayout() {
  const { user, token, logout, fetchMe } = useAuthStore()
  const navigate = useNavigate()
  const location = useLocation()
  const { token: themeToken } = theme.useToken()

  useEffect(() => {
    if (token && !user) fetchMe()
  }, [token, user, fetchMe])

  const isAdmin = user?.role === 'admin'

  const menuItems = [
    { key: '/chat', icon: <MessageOutlined />, label: '会话' },
    { key: '/', icon: <FolderOutlined />, label: '项目' },
    ...(isAdmin ? [{ key: '/admin', icon: <SettingOutlined />, label: '管理后台' }] : []),
  ]

  const selectedKey = location.pathname.startsWith('/admin')
    ? '/admin'
    : location.pathname.startsWith('/chat')
    ? '/chat'
    : '/'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 24px',
          background: themeToken.colorBgContainer,
          borderBottom: `1px solid ${themeToken.colorBorderSecondary}`,
        }}
      >
        <Space>
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: 6,
              background: 'linear-gradient(135deg, #1677ff, #1f3a8a)',
            }}
          />
          <Text strong>内网 Coding Agent</Text>
        </Space>
        <Dropdown
          menu={{
            items: [
              {
                key: 'logout',
                icon: <LogoutOutlined />,
                label: '退出登录',
                onClick: () => {
                  logout()
                  navigate('/login')
                },
              },
            ],
          }}
        >
          <Space style={{ cursor: 'pointer' }}>
            <Avatar size="small" icon={<UserOutlined />} />
            <Text>{user?.username || '未知'}</Text>
            <Text type="secondary">·</Text>
            <Text type="secondary">{user ? roleLabels[user.role] || user.role : ''}</Text>
          </Space>
        </Dropdown>
      </Header>
      <Layout>
        <Sider width={200} style={{ background: themeToken.colorBgContainer }}>
          <Menu
            mode="inline"
            selectedKeys={[selectedKey]}
            items={menuItems}
            onClick={({ key }) => navigate(key)}
            style={{ borderRight: 0, height: '100%' }}
          />
        </Sider>
        <Content style={{ padding: 0, background: themeToken.colorBgLayout }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
