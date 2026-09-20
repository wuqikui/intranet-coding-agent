import { useEffect, useState } from 'react'
import { Tree, Spin, Empty, Typography, theme } from 'antd'
import type { DataNode } from 'antd/es/tree'
import { FolderOutlined, FileOutlined } from '@ant-design/icons'
import api from '../api/client'

const { Text } = Typography

interface FileEntry {
  name: string
  path: string
  is_dir: boolean
  children?: FileEntry[]
}

function toTreeNodes(entries: FileEntry[]): DataNode[] {
  return entries.map((e) => ({
    key: e.path,
    title: e.name,
    isLeaf: !e.is_dir,
    icon: e.is_dir ? <FolderOutlined /> : <FileOutlined />,
    children: e.children ? toTreeNodes(e.children) : undefined,
  }))
}

interface Props {
  projectId: string | null
  onSelectFile: (path: string) => void
}

export default function FileTree({ projectId, onSelectFile }: Props) {
  const [tree, setTree] = useState<DataNode[]>([])
  const [loading, setLoading] = useState(false)
  const { token: themeToken } = theme.useToken()

  useEffect(() => {
    if (!projectId) {
      setTree([])
      return
    }
    setLoading(true)
    api
      .get(`/agent/projects/${projectId}/files`)
      .then((res: any) => {
        const entries: FileEntry[] = res?.entries || res || []
        setTree(toTreeNodes(entries))
      })
      .catch(() => setTree([]))
      .finally(() => setLoading(false))
  }, [projectId])

  if (!projectId) {
    return (
      <div style={{ padding: 16, textAlign: 'center' }}>
        <Empty description="尚未关联项目" />
      </div>
    )
  }

  if (loading) {
    return (
      <div style={{ padding: 16, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }

  return (
    <div style={{ height: '100%', overflow: 'auto', background: themeToken.colorBgContainer }}>
      <div style={{ padding: '8px 12px', borderBottom: `1px solid ${themeToken.colorBorderSecondary}` }}>
        <Text strong>文件树</Text>
      </div>
      <Tree
        showIcon
        treeData={tree}
        defaultExpandAll
        onSelect={(keys) => {
          if (keys.length > 0) onSelectFile(String(keys[0]))
        }}
      />
    </div>
  )
}
