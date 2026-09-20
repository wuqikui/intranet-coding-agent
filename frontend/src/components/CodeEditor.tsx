import { useEffect, useState } from 'react'
import { Spin, Empty, Typography, theme } from 'antd'
import Editor from '@monaco-editor/react'
import api from '../api/client'

const { Text } = Typography

interface Props {
  projectId: string | null
  filePath: string | null
}

function detectLanguage(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  const map: Record<string, string> = {
    ts: 'typescript',
    tsx: 'typescript',
    js: 'javascript',
    jsx: 'javascript',
    json: 'json',
    py: 'python',
    cpp: 'cpp',
    cc: 'cpp',
    cxx: 'cpp',
    c: 'c',
    h: 'cpp',
    hpp: 'cpp',
    rs: 'rust',
    go: 'go',
    java: 'java',
    kt: 'kotlin',
    md: 'markdown',
    yml: 'yaml',
    yaml: 'yaml',
    dockerfile: 'dockerfile',
    sh: 'shell',
    toml: 'ini',
    ini: 'ini',
    txt: 'plaintext',
  }
  if (path.endsWith('CMakeLists.txt')) return 'cmake'
  if (path.endsWith('Makefile')) return 'makefile'
  if (path.endsWith('Dockerfile')) return 'dockerfile'
  return map[ext] || 'plaintext'
}

export default function CodeEditor({ projectId, filePath }: Props) {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const { token: themeToken } = theme.useToken()

  useEffect(() => {
    if (!projectId || !filePath) {
      setContent('')
      return
    }
    setLoading(true)
    api
      .get(`/agent/projects/${projectId}/file`, { params: { path: filePath } })
      .then((res: any) => {
        setContent(res?.content ?? '')
      })
      .catch(() => setContent(''))
      .finally(() => setLoading(false))
  }, [projectId, filePath])

  if (!filePath) {
    return (
      <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Empty description="从左侧选择文件查看代码" />
      </div>
    )
  }

  if (loading) {
    return (
      <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin />
      </div>
    )
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: themeToken.colorBgContainer }}>
      <div
        style={{
          padding: '8px 12px',
          borderBottom: `1px solid ${themeToken.colorBorderSecondary}`,
          background: themeToken.colorFillQuaternary,
        }}
      >
        <Text code>{filePath}</Text>
      </div>
      <div style={{ flex: 1 }}>
        <Editor
          language={detectLanguage(filePath)}
          value={content}
          onChange={(v) => setContent(v ?? '')}
          options={{
            readOnly: false,
            minimap: { enabled: true },
            fontSize: 13,
            automaticLayout: true,
            tabSize: 2,
            scrollBeyondLastLine: false,
          }}
        />
      </div>
    </div>
  )
}
