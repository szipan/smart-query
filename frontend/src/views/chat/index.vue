<template>
  <div class="chat-layout">
    <!-- Left Sidebar: Conversation History -->
    <aside class="chat-sidebar">
      <div class="sidebar-header">
        <div class="logo">
          <el-icon :size="22"><ChatDotRound /></el-icon>
          <span>Smart Query</span>
        </div>
        <el-button type="primary" :icon="Plus" circle size="small" @click="handleNewChat" />
      </div>

      <div class="session-list">
        <div
          v-for="session in chatStore.sessions"
          :key="session.id"
          class="session-item"
          :class="{ active: session.id === chatStore.activeSessionId }"
          @click="chatStore.setActive(session.id)"
        >
          <el-icon><ChatLineSquare /></el-icon>
          <span class="session-title">{{ session.title }}</span>
          <el-icon
            class="session-delete"
            @click.stop="chatStore.deleteSession(session.id)"
          >
            <Delete />
          </el-icon>
        </div>
      </div>

      <div class="sidebar-footer">
        <span class="version">智能问数 v2.0</span>
      </div>
    </aside>

    <!-- Main Chat Area -->
    <main class="chat-main">
      <!-- Messages Area -->
      <div class="messages-container" ref="messagesContainer">
        <!-- Welcome state when no messages -->
        <div v-if="!activeMessages.length" class="welcome-area">
          <div class="welcome-icon">
            <el-icon :size="48" color="#409EFF"><DataAnalysis /></el-icon>
          </div>
          <h2 class="welcome-title">智能问数助手</h2>
          <p class="welcome-desc">用自然语言查询您的业务数据，我来帮您生成 SQL 并获取结果</p>

          <!-- Recommended Questions -->
          <div class="recommend-questions">
            <div
              v-for="(q, idx) in recommendQuestions"
              :key="idx"
              class="recommend-item"
              @click="handleSend(q)"
            >
              <el-icon><Search /></el-icon>
              <span>{{ q }}</span>
            </div>
          </div>
        </div>

        <!-- Chat Messages -->
        <div v-else class="messages-list">
          <ChatMessage
            v-for="msg in activeMessages"
            :key="msg.id"
            :message="msg"
          />
        </div>
      </div>

      <!-- Input Area -->
      <div class="input-area">
        <div class="input-wrapper">
          <div class="textarea-wrap" @keydown.enter.exact.prevent="handleEnterKey">
            <el-input
              v-model="inputText"
              type="textarea"
              :autosize="{ minRows: 1, maxRows: 4 }"
              placeholder="输入您的问题，例如：本月各电站发电量是多少？"
              :disabled="isLoading"
            />
          </div>
          <el-button
            class="send-btn"
            type="primary"
            :icon="Promotion"
            circle
            :loading="isLoading"
            :disabled="!inputText.trim() || isLoading"
            @click="handleSend()"
          />
        </div>
        <div class="input-tips">
          <span>按 Enter 发送，Shift+Enter 换行</span>
          <span v-if="lastCached" class="cache-hint">
            <el-icon><Lightning /></el-icon> 上次回答来自缓存
          </span>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue'
import {
  Plus,
  Delete,
  ChatDotRound,
  ChatLineSquare,
  Search,
  Promotion,
  DataAnalysis,
  Lightning,
} from '@element-plus/icons-vue'
import { useChatStore } from '@/stores/chat'
import ChatMessage from './ChatMessage.vue'

const chatStore = useChatStore()
const inputText = ref('')
const messagesContainer = ref<HTMLElement>()

const activeMessages = computed(() => chatStore.activeSession?.messages || [])

const isLoading = computed(() => {
  const msgs = activeMessages.value
  return msgs.length > 0 && msgs[msgs.length - 1].loading === true
})

const lastCached = computed(() => {
  const msgs = activeMessages.value
  if (msgs.length < 2) return false
  const last = msgs[msgs.length - 1]
  return last.role === 'assistant' && last.cached
})

const recommendQuestions = [
  '本月各电站发电量是多少？',
  '今年各电站总发电量排名',
  '查询所有电站的装机容量',
  '各组织本月发电量对比',
]

function handleNewChat() {
  chatStore.createSession()
  inputText.value = ''
}

function handleEnterKey(e: KeyboardEvent) {
  if (e.shiftKey) return
  handleSend()
}

async function handleSend(text?: string) {
  const question = (text || inputText.value).trim()
  if (!question) return
  inputText.value = ''
  await chatStore.sendMessage(question)
  await nextTick()
  scrollToBottom()
}

function scrollToBottom() {
  const el = messagesContainer.value
  if (el) {
    el.scrollTop = el.scrollHeight
  }
}

watch(activeMessages, () => {
  nextTick(scrollToBottom)
}, { deep: true })
</script>

<style lang="less" scoped>
.chat-layout {
  display: flex;
  width: 100%;
  height: 100vh;
  overflow: hidden;
}

// ─── Left Sidebar ────────────────────────────────────
.chat-sidebar {
  width: 260px;
  min-width: 260px;
  background: #1e1e2e;
  color: #cdd6f4;
  display: flex;
  flex-direction: column;

  .sidebar-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px;
    border-bottom: 1px solid #313244;

    .logo {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 16px;
      font-weight: 600;
      color: #cdd6f4;
    }
  }

  .session-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px;

    .session-item {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 12px;
      border-radius: 8px;
      cursor: pointer;
      margin-bottom: 4px;
      transition: background 0.2s;

      &:hover {
        background: #313244;
      }

      &.active {
        background: #45475a;
      }

      .session-title {
        flex: 1;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: 13px;
      }

      .session-delete {
        opacity: 0;
        transition: opacity 0.2s;
        color: #f38ba8;

        &:hover {
          color: #eba0ac;
        }
      }

      &:hover .session-delete {
        opacity: 1;
      }
    }
  }

  .sidebar-footer {
    padding: 12px 16px;
    border-top: 1px solid #313244;
    text-align: center;

    .version {
      font-size: 12px;
      color: #6c7086;
    }
  }
}

// ─── Main Chat Area ──────────────────────────────────
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #ffffff;
  overflow: hidden;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

// ─── Welcome Area ────────────────────────────────────
.welcome-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 40px;

  .welcome-icon {
    margin-bottom: 16px;
  }

  .welcome-title {
    font-size: 24px;
    font-weight: 600;
    color: #303133;
    margin-bottom: 8px;
  }

  .welcome-desc {
    font-size: 14px;
    color: #909399;
    margin-bottom: 32px;
  }

  .recommend-questions {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    max-width: 600px;
    width: 100%;

    .recommend-item {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px 16px;
      border: 1px solid #e4e7ed;
      border-radius: 8px;
      cursor: pointer;
      font-size: 13px;
      color: #606266;
      transition: all 0.2s;

      &:hover {
        border-color: #409eff;
        color: #409eff;
        background: #ecf5ff;
      }
    }
  }
}

// ─── Messages List ───────────────────────────────────
.messages-list {
  max-width: 900px;
  margin: 0 auto;
  width: 100%;
}

// ─── Input Area ──────────────────────────────────────
.input-area {
  padding: 16px 24px;
  border-top: 1px solid #ebeef5;
  background: #fafafa;

  .input-wrapper {
    max-width: 900px;
    margin: 0 auto;
    display: flex;
    align-items: flex-end;
    gap: 12px;

    .textarea-wrap {
      flex: 1;
    }

    :deep(.el-textarea__inner) {
      border-radius: 12px;
      padding: 12px 16px;
      resize: none;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
    }

    .send-btn {
      flex-shrink: 0;
    }
  }

  .input-tips {
    max-width: 900px;
    margin: 8px auto 0;
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: #909399;

    .cache-hint {
      display: flex;
      align-items: center;
      gap: 4px;
      color: #e6a23c;
    }
  }
}
</style>
