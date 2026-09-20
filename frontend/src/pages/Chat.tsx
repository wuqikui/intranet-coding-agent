import { useState, useRef, useEffect } from 'react'
import {
  Layout,
  Input,
  Button,
  Space,
  Tooltip,
  Empty,
  Tag,
  theme,
} from 'antd'
import {
  SendOutlined,
  StopOutlined,
  ClearOutlined,
  CodeOutlined,
  DesktopOutlined,
  AppstoreOutlined,
} from '@ant-design/icons'
import { useChatStore } from '../store/chat'
import { useAuthStore } from '../store/auth'
import FileTree from '../components/FileTree'
import CodeEditor from '../components/CodeEditor'
import Terminal from '../components/Terminal'
import TaskBoard from '../components/TaskBoard'

const { Sider, Content } = Layout

type RightPanel = 'editor' | 'terminal' | 'tasks'

export default function Chat() {
  const { messages, isStreaming, projectId, sendMessage, stop, clear } = useChatStore()
  const user = useAuthStore((s) => s.user)
  const canShell = user?.role !== 'readonly'

  const [input, setInput] = useState('')
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [rightPanel, setRightPanel] = useState<RightPanel>('editor')
  const [terminalOutput, setTerminalOutput] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  const { token: themeToken } = theme.useToken()

  // 当最新 assistant 消息带 build_log 时，同步到终端回放
  useEffect(() => {
    const last = messages[messages.length - 1]
    if (last?.role === 'assistant' && last.meta?.build_log) {
      setTerminalOutput(last.meta.build_log)
    }
  }, [messages])

  // 自动滚动到底
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const onSend = () => {
    const text = input.trim()
    if (!text || isStreaming) return
    setInput('')
    sendMessage(text)
  }

  const sidebar = (
    <Sider width={240} style={{ background: themeToken.colorBgContainer, overflow: 'auto' }}>
      <FileTree projectId={projectId} onSelectFile={setSelectedFile} />
    </Sider>
  )

  const rightPanelContent = (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div
        style={{
          display: 'flex',
          gap: 8,
          padding: '6px 12px',
          borderBottom: `1px solid ${themeToken.colorBorderSecondary}`,
          background: themeToken.colorFillQuaternary,
        }}
      >
        <Tooltip title="代码">
          <Button
            size="small"
            type={rightPanel === 'editor' ? 'primary' : 'text'}
            icon={<CodeOutlined />}
            onClick={() => setRightPanel('editor')}
          />
        </Tooltip>
        {canShell && (
          <Tooltip title="终端回放">
            <Button
              size="small"
              type={rightPanel === 'terminal' ? 'primary' : 'text'}
              icon={<DesktopOutlined />}
              onClick={() => setRightPanel('terminal')}
            />
          </Tooltip>
        )}
        <Tooltip title="任务看板">
          <Button
            size="small"
            type={rightPanel === 'tasks' ? 'primary' : 'text'}
            icon={<AppstoreOutlined />}
            onClick={() => setRightPanel('tasks')}
          />
        </Tooltip>
      </div>
      <div style={{ flex: 1, overflow: 'hidden' }}>
        {rightPanel === 'editor' && (
          <CodeEditor projectId={projectId} filePath={selectedFile} />
        )}
        {rightPanel === 'terminal' && canShell && (
          <Terminal projectId={projectId} output={terminalOutput} />
        )}
        {rightPanel === 'tasks' && (
          <TaskBoard messages={messages} projectId={projectId} />
        )}
        {rightPanel === 'terminal' && !canShell && (
          <div style={{ padding: 24, textAlign: 'center' }}>
            <Empty description="readonly 角色无终端权限" />
          </div>
        )}
      </div>
    </div>
  )

  return (
    <Layout style={{ height: 'calc(100vh - 64px)' }}>
      {sidebar}
      <Content style={{ display: 'flex', flexDirection: 'column', padding: 0, background: themeToken.colorBgLayout }}>
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            minWidth: 0,
          }}
        >
          {/* 消息列表 */}
          <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
            {messages.length === 0 ? (
              <Empty
                style={{ marginTop: 80 }}
                description="提交自然语言需求开始生成项目"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            ) : (
              messages.map((m, i) => (
                <div
                  key={i}
                  style={{
                    marginBottom: 16,
                    display: 'flex',
                    justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
                  }}
                >
                  <div
                    style={{
                      maxWidth: '80%',
                      padding: '8px 12px',
                      borderRadius: 8,
                      background:
                        m.role === 'user'
                          ? themeToken.colorPrimary
                          : themeToken.colorBgContainer,
                      color: m.role === 'user' ? '#fff' : themeToken.colorText,
                      border: `1px solid ${themeToken.colorBorderSecondary}`,
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}
                  >
                    <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
                    {m.meta?.fix_rounds !== undefined && (
                      <div style={{ marginTop: 8 }}>
                        <Tag color="orange">修复 {m.meta.fix_rounds} 轮</Tag>
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}
            <div ref={endRef} />
          </div>

          {/* 输入框 */}
          <div
            style={{
              borderTop: `1px solid ${themeToken.colorBorderSecondary}`,
              padding: 12,
              background: themeToken.colorBgContainer,
            }}
          >
            <Space.Compact style={{ width: '100%' }}>
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPressEnter={onSend}
                placeholder="例如：生成一个带 CMake 的 C++ 计算器 + FastAPI 后端 + React 前端的全栈项目"
                disabled={isStreaming}
                size="large"
              />
              {isStreaming ? (
                <Button danger size="large" icon={<StopOutlined />} onClick={stop}>
                  停止
                </Button>
              ) : (
                <Button type="primary" size="large" icon={<SendOutlined />} onClick={onSend}>
                  发送
                </Button>
              )}
              <Tooltip title="清空">
                <Button size="large" icon={<ClearOutlined />} onClick={clear} />
              </Tooltip>
            </Space.Compact>
          </div>
        </div>
        <div style={{ width: '55%', height: '100%', borderLeft: `1px solid ${themeToken.colorBorderSecondary}` }}>
          {rightPanelContent}
        </div>
      </Content>
    </Layout>
  )
}
