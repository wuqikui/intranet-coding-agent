import { create } from 'zustand'
import { streamChat } from '../api/client'

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  meta?: { files?: string[]; build_log?: string; fix_rounds?: number }
}

interface ChatState {
  messages: ChatMessage[]
  isStreaming: boolean
  projectId: string | null
  sendMessage: (content: string) => Promise<void>
  stop: () => void
  clear: () => void
}

let abortController: AbortController | null = null

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  isStreaming: false,
  projectId: null,
  sendMessage: async (content) => {
    set((s) => ({ messages: [...s.messages, { role: 'user', content }], isStreaming: true }))
    abortController = new AbortController()

    let assistantContent = ''
    let assistantMeta: any = {}
    set((s) => ({ messages: [...s.messages, { role: 'assistant', content: '' }] }))

    try {
      const stream = streamChat(
        [...get().messages.filter((m) => m.role !== 'assistant' || m.content)],
        { signal: abortController.signal },
      )
      for await (const chunk of stream) {
        if (chunk.content) assistantContent += chunk.content
        if (chunk.meta) assistantMeta = { ...assistantMeta, ...chunk.meta }
        set((s) => {
          const msgs = [...s.messages]
          msgs[msgs.length - 1] = { role: 'assistant', content: assistantContent, meta: assistantMeta }
          return { messages: msgs }
        })
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        set((s) => {
          const msgs = [...s.messages]
          msgs[msgs.length - 1] = {
            role: 'assistant',
            content: assistantContent + `\n\n[错误] ${e.message}`,
          }
          return { messages: msgs }
        })
      }
    } finally {
      set({ isStreaming: false })
    }
  },
  stop: () => {
    abortController?.abort()
    set({ isStreaming: false })
  },
  clear: () => set({ messages: [], projectId: null }),
}))
