import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import dayjs from 'dayjs'
import { postQuery } from '@/api/query'
import type { QueryResponse } from '@/api/query'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  sql?: string
  data?: Record<string, unknown>[]
  columns?: string[]
  cached?: boolean
  error?: string
  timestamp: number
  loading?: boolean
}

export interface ChatSession {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: number
}

function genId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
}

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<ChatSession[]>([])
  const activeSessionId = ref<string>('')

  const activeSession = computed(() =>
    sessions.value.find((s) => s.id === activeSessionId.value)
  )

  function createSession() {
    const session: ChatSession = {
      id: genId(),
      title: '新对话',
      messages: [],
      createdAt: Date.now(),
    }
    sessions.value.unshift(session)
    activeSessionId.value = session.id
    return session
  }

  function deleteSession(id: string) {
    sessions.value = sessions.value.filter((s) => s.id !== id)
    if (activeSessionId.value === id) {
      activeSessionId.value = sessions.value[0]?.id || ''
    }
  }

  function setActive(id: string) {
    activeSessionId.value = id
  }

  async function sendMessage(question: string) {
    if (!activeSession.value) createSession()
    const session = activeSession.value!

    // Update session title from first question
    if (session.messages.length === 0) {
      session.title = question.slice(0, 20) + (question.length > 20 ? '...' : '')
    }

    // Add user message
    session.messages.push({
      id: genId(),
      role: 'user',
      content: question,
      timestamp: Date.now(),
      loading: false,
    })

    // Add placeholder for assistant
    const botIdx = session.messages.length
    session.messages.push({
      id: genId(),
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      loading: true,
    })

    try {
      const resp: QueryResponse = await postQuery({
        question,
        session_id: session.id,
      })
      // Replace the bot message entirely to guarantee reactivity
      session.messages[botIdx] = {
        ...session.messages[botIdx],
        content: resp.answer || '查询完成',
        sql: resp.sql,
        data: resp.data,
        columns: resp.columns,
        cached: resp.cached,
        error: resp.error,
        loading: false,
        timestamp: Date.now(),
      }
    } catch (err: any) {
      session.messages[botIdx] = {
        ...session.messages[botIdx],
        content: '请求失败',
        error: err.message || '网络错误',
        loading: false,
        timestamp: Date.now(),
      }
    }
  }

  // Init with one session
  if (sessions.value.length === 0) {
    createSession()
  }

  return {
    sessions,
    activeSessionId,
    activeSession,
    createSession,
    deleteSession,
    setActive,
    sendMessage,
  }
})
