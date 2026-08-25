<template>
  <div class="chat-message" :class="message.role">
    <!-- User Message -->
    <div v-if="message.role === 'user'" class="message-row user-row">
      <div class="message-bubble user-bubble">
        <p>{{ message.content }}</p>
      </div>
      <div class="avatar user-avatar">
        <el-icon><User /></el-icon>
      </div>
    </div>

    <!-- Assistant Message -->
    <div v-else class="message-row assistant-row">
      <div class="avatar bot-avatar">
        <el-icon><DataAnalysis /></el-icon>
      </div>
      <div class="message-bubble bot-bubble">
        <!-- Loading state -->
        <div v-if="message.loading" class="loading-dots">
          <span></span><span></span><span></span>
        </div>

        <template v-else>
          <!-- Error -->
          <div v-if="message.error" class="error-block">
            <el-alert :title="message.error" type="error" :closable="false" show-icon />
          </div>

          <!-- Answer text -->
          <p v-if="message.content" class="answer-text">{{ message.content }}</p>

          <!-- SQL Block -->
          <div v-if="message.sql" class="sql-block">
            <div class="sql-header">
              <span class="sql-label">
                <el-icon><Document /></el-icon> SQL
              </span>
              <el-tag v-if="message.cached" type="warning" size="small">缓存命中</el-tag>
              <el-button size="small" text @click="copySql">
                <el-icon><CopyDocument /></el-icon> 复制
              </el-button>
            </div>
            <pre class="sql-code"><code>{{ message.sql }}</code></pre>
          </div>

          <!-- Data Table -->
          <div v-if="message.data && message.data.length" class="data-block">
            <div class="data-header">
              <span class="data-label">
                <el-icon><Grid /></el-icon> 查询结果
              </span>
              <el-tag size="small">{{ message.data.length }} 条</el-tag>
            </div>
            <el-table
              :data="message.data"
              border
              stripe
              size="small"
              max-height="320"
              class="result-table"
            >
              <el-table-column
                v-for="col in displayColumns"
                :key="col"
                :prop="col"
                :label="col"
                :min-width="120"
                show-overflow-tooltip
              />
            </el-table>
          </div>

          <!-- Timestamp -->
          <div class="message-meta">
            <span class="timestamp">{{ formatTime(message.timestamp) }}</span>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import {
  User,
  DataAnalysis,
  Document,
  CopyDocument,
  Grid,
} from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import type { ChatMessage } from '@/stores/chat'

const props = defineProps<{ message: ChatMessage }>()

const displayColumns = computed(() => {
  if (props.message.columns?.length) return props.message.columns
  if (props.message.data?.length) return Object.keys(props.message.data[0])
  return []
})

function formatTime(ts: number) {
  return dayjs(ts).format('HH:mm')
}

function copySql() {
  if (props.message.sql) {
    navigator.clipboard.writeText(props.message.sql)
    ElMessage.success('SQL 已复制')
  }
}
</script>

<style lang="less" scoped>
.chat-message {
  margin-bottom: 20px;
}

.message-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;

  &.user-row {
    justify-content: flex-end;
  }

  &.assistant-row {
    justify-content: flex-start;
  }
}

.avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;

  &.user-avatar {
    background: #409eff;
    color: white;
  }

  &.bot-avatar {
    background: #e8f4ff;
    color: #409eff;
  }
}

.message-bubble {
  max-width: 720px;
  padding: 12px 16px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;

  &.user-bubble {
    background: #409eff;
    color: white;
    border-bottom-right-radius: 4px;

    p {
      margin: 0;
      white-space: pre-wrap;
    }
  }

  &.bot-bubble {
    background: #f4f4f5;
    color: #303133;
    border-bottom-left-radius: 4px;
  }
}

// Loading animation
.loading-dots {
  display: flex;
  gap: 4px;
  padding: 4px 0;

  span {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #909399;
    animation: bounce 1.4s infinite both;

    &:nth-child(1) { animation-delay: 0s; }
    &:nth-child(2) { animation-delay: 0.16s; }
    &:nth-child(3) { animation-delay: 0.32s; }
  }
}

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

// SQL Block
.sql-block {
  margin-top: 12px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
  background: white;

  .sql-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    background: #fafafa;
    border-bottom: 1px solid #e4e7ed;

    .sql-label {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 12px;
      font-weight: 600;
      color: #606266;
    }
  }

  .sql-code {
    margin: 0;
    padding: 12px 16px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 13px;
    color: #1e293b;
    background: #f8fafc;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }
}

// Data Table Block
.data-block {
  margin-top: 12px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;

  .data-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    background: #fafafa;
    border-bottom: 1px solid #e4e7ed;

    .data-label {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 12px;
      font-weight: 600;
      color: #606266;
    }
  }

  .result-table {
    :deep(.el-table__header th) {
      background: #f5f7fa;
      font-weight: 600;
    }
  }
}

// Error
.error-block {
  margin-bottom: 8px;
}

// Answer
.answer-text {
  margin: 0 0 4px;
  white-space: pre-wrap;
}

// Meta
.message-meta {
  margin-top: 8px;
  display: flex;
  justify-content: flex-end;

  .timestamp {
    font-size: 11px;
    color: #909399;
  }
}
</style>
