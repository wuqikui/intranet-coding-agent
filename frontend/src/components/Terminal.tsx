import { useEffect, useRef } from 'react'
import { Typography, theme } from 'antd'
import { Terminal as XTerm } from 'xterm'
import { FitAddon } from 'xterm-addon-fit'
import 'xterm/css/xterm.css'

const { Text } = Typography

interface Props {
  projectId: string | null
  /** 当次会话累积的终端输出（来自 Agent 推送）。*/
  output: string
}

/**
 * 终端回放组件 — 只读展示 Agent 在沙箱中执行的真实命令与输出。
 * 不开放给 readonly 角色（由父层控制渲染）。
 */
export default function Terminal({ projectId, output }: Props) {
  const termRef = useRef<HTMLDivElement>(null)
  const xtermRef = useRef<XTerm | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const { token: themeToken } = theme.useToken()

  useEffect(() => {
    if (!termRef.current || xtermRef.current) return
    const term = new XTerm({
      fontFamily: 'Menlo, Consolas, "DejaVu Sans Mono", monospace',
      fontSize: 12,
      convertEol: true,
      disableStdin: true,
      cursorBlink: false,
      theme: {
        background: themeToken.colorBgContainer ?? '#1e1e1e',
        foreground: themeToken.colorText ?? '#d4d4d4',
      },
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(termRef.current)
    fit.fit()
    xtermRef.current = term
    fitRef.current = fit
    return () => {
      term.dispose()
      xtermRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 增量写入 — 每次 output 变化时只追加新增部分
  const lastWrittenRef = useRef(0)
  useEffect(() => {
    const term = xtermRef.current
    if (!term) return
    if (output.length > lastWrittenRef.current) {
      term.write(output.slice(lastWrittenRef.current))
      lastWrittenRef.current = output.length
      fitRef.current?.fit()
    }
  }, [output])

  // 切换项目时重置回放
  useEffect(() => {
    xtermRef.current?.clear()
    lastWrittenRef.current = 0
    if (output) {
      xtermRef.current?.write(output)
      lastWrittenRef.current = output.length
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: '#1e1e1e' }}>
      <div
        style={{
          padding: '6px 12px',
          borderBottom: `1px solid #333`,
          background: '#252526',
          color: '#cccccc',
        }}
      >
        <Text style={{ color: '#cccccc' }}>终端回放</Text>
      </div>
      <div ref={termRef} style={{ flex: 1, padding: 4, background: '#1e1e1e' }} />
    </div>
  )
}
