import { Card, Tag, Timeline, Typography, Empty, theme } from 'antd'
import {
  CheckCircleTwoTone,
  ClockCircleOutlined,
  LoadingOutlined,
  CloseCircleTwoTone,
  BuildOutlined,
} from '@ant-design/icons'
import type { ChatMessage } from '../store/chat'

const { Text, Paragraph } = Typography

type StepStatus = 'pending' | 'running' | 'success' | 'failed'

interface Step {
  title: string
  description?: string
  status: StepStatus
}

/** 从消息 meta 推导工程循环步骤。 */
function deriveSteps(messages: ChatMessage[]): Step[] {
  const steps: Step[] = []

  for (const m of messages) {
    if (m.role !== 'assistant' || !m.meta) continue

    if (m.meta.files?.length) {
      steps.push({
        title: '文件树生成',
        description: m.meta.files.join(', '),
        status: 'success',
      })
    }
    if (m.meta.build_log) {
      const rounds = m.meta.fix_rounds ?? 0
      steps.push({
        title: `工程循环${rounds > 0 ? `（修复 ${rounds} 轮）` : ''}`,
        description: '真实编译 + 自动修复',
        status: rounds > 0 ? (rounds >= 5 ? 'failed' : 'success') : 'success',
      })
    }
  }

  const last = messages[messages.length - 1]
  if (last?.role === 'assistant' && last.content && !last.meta?.build_log) {
    steps.push({ title: '生成中', description: last.content.slice(0, 60), status: 'running' })
  }

  if (steps.length === 0) {
    steps.push({ title: '等待需求', status: 'pending' })
  }
  return steps
}

function stepIcon(status: StepStatus) {
  switch (status) {
    case 'success':
      return <CheckCircleTwoTone twoToneColor="#52c41a" />
    case 'failed':
      return <CloseCircleTwoTone twoToneColor="#ff4d4f" />
    case 'running':
      return <LoadingOutlined style={{ color: '#1677ff' }} />
    default:
      return <ClockCircleOutlined style={{ color: '#bfbfbf' }} />
  }
}

function stepColor(status: StepStatus) {
  switch (status) {
    case 'success':
      return 'green'
    case 'failed':
      return 'red'
    case 'running':
      return 'blue'
    default:
      return 'default'
  }
}

interface Props {
  messages: ChatMessage[]
  projectId: string | null
}

export default function TaskBoard({ messages, projectId }: Props) {
  const { token: themeToken } = theme.useToken()
  const steps = deriveSteps(messages)

  return (
    <Card
      size="small"
      style={{ height: '100%', overflow: 'auto', background: themeToken.colorBgContainer }}
      title={
        <span>
          <BuildOutlined /> 任务看板
        </span>
      }
      extra={projectId ? <Tag color="blue">{projectId}</Tag> : <Tag>无项目</Tag>}
    >
      {steps.length === 1 && steps[0].status === 'pending' ? (
        <Empty description="提交需求后将显示生成步骤" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Timeline
          items={steps.map((s, i) => ({
            key: i,
            dot: stepIcon(s.status),
            children: (
              <div>
                <Text strong>{s.title}</Text>
                <div>
                  <Tag color={stepColor(s.status)}>{s.status.toUpperCase()}</Tag>
                </div>
                {s.description && (
                  <Paragraph type="secondary" style={{ marginTop: 4, marginBottom: 0, fontSize: 12 }}>
                    {s.description}
                  </Paragraph>
                )}
              </div>
            ),
          }))}
        />
      )}
    </Card>
  )
}
