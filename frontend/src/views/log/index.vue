<template>
  <section class="page" data-module="log">
    <header class="page-head">
      <div>
        <h2>采集日志管理</h2>
        <p class="page-desc">
          归档先落完整审计文件再切换状态；清理仅改变可见范围，完整文件、摘要链与原始顺序保留。
          当前身份：{{ store.account.name }}（{{ store.roleLabel }}）
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出采集日志清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
        <span v-if="tab.key === 'pending' && pendingCount > 0" class="badge badge-danger">{{ pendingCount }}</span>
      </button>
    </div>

    <!-- 日志列表 -->
    <template v-if="activeTab === 'list'">
      <form class="filter-bar" @submit.prevent="reload">
        <label v-for="field in filterFields" :key="field" class="filter-item">
          <span>{{ field }}</span>
          <input v-model="filters[field]" :placeholder="`按${field}检索`" />
        </label>
        <label class="filter-item">
          <span>状态</span>
          <select v-model="statusFilter">
            <option value="">全部状态</option>
            <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>范围</span>
          <select v-model="includeHidden" @change="reload">
            <option value="false">默认（隐藏已清理）</option>
            <option value="true">包含已清理记录</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>归档状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)">
            <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
            <td>
              <span v-if="row.status === '已归档' && !row.archive_available" class="badge badge-warn">
                状态已归档·审计文件缺失
              </span>
              <span v-else-if="row.status === '已归档'" class="badge badge-ok">
                已归档·文件完整
              </span>
              <span v-else-if="row.status === '已清理'" class="badge badge-muted">已清理·仅隐藏</span>
              <span v-else class="badge badge-muted">{{ row.status }}</span>
            </td>
            <td class="row-actions">
              <button class="link" type="button" @click="openSummary(row)">查看摘要</button>
              <button
                v-if="row.archive_available"
                class="link"
                type="button"
                @click="openFile(row)"
              >
                打开审计文件
              </button>
              <button
                v-else
                class="link"
                type="button"
                @click="goPending"
              >
                去待处理区补建
              </button>
              <template v-if="store.canWrite">
                <button
                  v-if="row.status === '草稿' || row.status === '记录中'"
                  class="link"
                  type="button"
                  @click="runAction('归档记录', row)"
                >
                  归档记录
                </button>
                <button class="link" type="button" @click="runAction('清理日志', row)">清理日志</button>
              </template>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 2" class="empty-state">
              当前账号授权范围内暂无日志数据
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 归档页：摘要与文件同源 -->
    <template v-else-if="activeTab === 'archive'">
      <table class="data-table">
        <thead>
          <tr>
            <th>记录编号</th>
            <th>关联端点</th>
            <th>请求时间</th>
            <th>响应状态</th>
            <th>重试次数</th>
            <th>文件编号 / 文件名</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in archiveRows" :key="String(item.id)">
            <td>{{ item.summary.fields['记录编号'] }}</td>
            <td>{{ item.summary.fields['关联端点'] }}</td>
            <td>{{ item.summary.fields['请求时间'] }}</td>
            <td>{{ item.summary.fields['响应状态'] }}</td>
            <td>{{ item.summary.fields['重试次数'] }}</td>
            <td>{{ item.summary.file_id }}<br /><span class="page-desc">{{ item.summary.file_name }}</span></td>
            <td class="row-actions">
              <button class="link" type="button" @click="openArchiveById(item.id)">摘要 / 文件</button>
            </td>
          </tr>
          <tr v-if="!archiveRows.length">
            <td colspan="7" class="empty-state">授权范围内暂无可打开的完整审计文件</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 未完成审计待处理区 -->
    <template v-else>
      <table class="data-table">
        <thead>
          <tr>
            <th>记录编号</th>
            <th>关联端点</th>
            <th>请求时间</th>
            <th>待处理原因</th>
            <th>发现时间</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in pendingRows" :key="String(item.log_id)">
            <td>{{ item['记录编号'] }}</td>
            <td>{{ item['关联端点'] }}</td>
            <td>{{ item['请求时间'] }}</td>
            <td>{{ item.reason }}</td>
            <td>{{ item.detected_at }}</td>
            <td><span class="badge badge-warn">{{ item.status }}</span></td>
            <td class="row-actions">
              <button class="link" type="button" @click="openArchiveById(item.log_id)">查看摘要回显</button>
              <button
                v-if="store.canWrite"
                class="link"
                type="button"
                @click="runRepair(item.log_id)"
              >
                补建审计文件
              </button>
              <span v-else class="page-desc">只读账号无补建权限，请联系归档账号</span>
            </td>
          </tr>
          <tr v-if="!pendingRows.length">
            <td colspan="7" class="empty-state">未完成审计待处理区为空，所有已归档记录的审计文件均完整</td>
          </tr>
        </tbody>
      </table>
    </template>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集日志记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="successMessage" class="success-text">{{ successMessage }}</span>
    </footer>

    <!-- 归档抽屉：摘要与完整文件联动回显 -->
    <template v-if="drawer.open">
      <div class="drawer-mask" @click="drawer.open = false" />
      <aside class="drawer">
        <div class="drawer-head">
          <h3>{{ drawer.title }}</h3>
          <button class="drawer-close" type="button" @click="drawer.open = false">×</button>
        </div>

        <div v-if="drawer.error" class="badge badge-danger" style="display:block; margin-bottom:10px">
          {{ drawer.error }}
        </div>

        <!-- 文件缺失时仍回显已知摘要（来自列表/待处理区），联动不中断 -->
        <section v-if="drawer.fallback">
          <h4>已知文件摘要（完整文件待补建）</h4>
          <dl class="kv-grid">
            <template v-for="(value, key) in drawer.fallback" :key="String(key)">
              <dt>{{ key }}</dt><dd>{{ value ?? '—' }}</dd>
            </template>
          </dl>
        </section>

        <template v-else-if="drawer.archive">
          <section>
            <h4>文件摘要（归档页同源）</h4>
            <dl class="kv-grid">
              <dt>文件编号</dt><dd>{{ drawer.archive.summary.file_id }}</dd>
              <dt>文件名</dt><dd>{{ drawer.archive.summary.file_name }}</dd>
              <dt>生成时间</dt><dd>{{ drawer.archive.summary.built_at }}</dd>
              <dt>补建标记</dt><dd>{{ drawer.archive.summary.rebuilt ? `是（${drawer.archive.summary.rebuilt_from}）` : '否' }}</dd>
              <template v-for="(value, key) in drawer.archive.summary.fields" :key="key">
                <dt>{{ key }}</dt><dd>{{ value ?? '—' }}</dd>
              </template>
            </dl>
            <p class="checksum-line">摘要校验和：{{ drawer.archive.summary.summary_checksum }}</p>
          </section>

          <section>
            <h4>完整审计文件内容</h4>
            <dl class="kv-grid">
              <template v-for="(value, key) in drawer.archive.file.content" :key="key">
                <dt>{{ key }}</dt><dd>{{ value ?? '—' }}</dd>
              </template>
            </dl>
            <p class="checksum-line">文件校验和：{{ drawer.archive.file.checksum }}</p>
          </section>

          <section>
            <h4>审计事件链（拒绝原因可查）</h4>
            <ul class="event-list">
              <li v-for="(event, index) in drawer.events" :key="index">
                {{ event.at }}｜{{ event.action }}｜{{ event.operator }}
                <template v-if="event.result">｜{{ event.result }}</template>
              </li>
            </ul>
          </section>
        </template>
      </aside>
    </template>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { errorMessageOf, request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, any> & {
  id: number
  status: string
  archive_available: boolean
  archive_summary: Record<string, unknown> | null
}

type ArchiveItem = {
  id: number
  status: string
  visible: boolean
  summary: {
    file_id: string
    file_name: string
    built_at: string
    rebuilt: boolean
    rebuilt_from: string | null
    file_checksum: string
    summary_checksum: string
    fields: Record<string, string | null>
  }
}

type PendingItem = {
  log_id: number
  '记录编号': string
  '关联端点': string
  '请求时间': string
  reason: string
  detected_at: string
  status: string
}

const ENDPOINT = '/api/log'
const store = useSessionStore()
const columns = ['记录编号', '关联端点', '请求时间', '响应状态', '耗时毫秒', '返回行数', '错误信息', '重试次数']
const filterFields = columns.slice(0, 3)
const statuses = ['草稿', '记录中', '已归档', '已清理']
const tabs = [
  { key: 'list', label: '日志列表' },
  { key: 'archive', label: '归档页' },
  { key: 'pending', label: '未完成审计待处理区' },
] as const

type TabKey = (typeof tabs)[number]['key']

const rows = ref<Row[]>([])
const archiveRows = ref<ArchiveItem[]>([])
const pendingRows = ref<PendingItem[]>([])
const total = ref(0)
const pendingCount = ref(0)
const activeTab = ref<TabKey>('list')
const errorMessage = ref('')
const successMessage = ref('')
const filters = ref<Record<string, string>>({})
const statusFilter = ref('')
const includeHidden = ref('false')
const stats = reactive([
  { label: '授权范围日志总数', value: 0 },
  { label: '已归档记录', value: 0 },
  { label: '审计文件缺失', value: 0 },
  { label: '已清理（仅隐藏）', value: 0 },
])

const drawer = reactive<{
  open: boolean
  title: string
  archive: { file: Record<string, any>; summary: ArchiveItem['summary'] } | null
  events: Record<string, string>[]
  error: string
  fallback: Record<string, string | number | null> | null
}>({
  open: false,
  title: '',
  archive: null,
  events: [],
  error: '',
  fallback: null,
})

function resetFilters() {
  filters.value = {}
  statusFilter.value = ''
  includeHidden.value = 'false'
  void reload()
}

function switchTab(key: TabKey) {
  activeTab.value = key
  errorMessage.value = ''
  if (key === 'archive') void loadArchives()
  if (key === 'pending') void loadPending()
  if (key === 'list') void reload()
}

function goPending() {
  switchTab('pending')
}

async function loadStats() {
  const response = await request(`${ENDPOINT}/stats`)
  if (response.ok) {
    const payload = await response.json()
    stats[0].value = payload.total
    stats[1].value = payload.archived
    stats[2].value = payload.missing_file
    stats[3].value = payload.hidden
  }
}

async function reload() {
  errorMessage.value = ''
  successMessage.value = ''
  const params = new URLSearchParams()
  Object.entries(filters.value).forEach(([key, value]) => {
    if (value) params.set(key, value)
  })
  if (statusFilter.value) params.set('status', statusFilter.value)
  params.set('include_hidden', includeHidden.value)
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error(await errorMessageOf(response, '日志记录列表读取失败'))
    }
    const payload = await response.json()
    rows.value = (payload.items ?? []) as Row[]
    total.value = payload.total ?? rows.value.length
    await loadStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集日志列表读取失败'
  }
}

async function loadArchives() {
  try {
    const response = await request(`${ENDPOINT}/archives`)
    if (!response.ok) {
      throw new Error(await errorMessageOf(response, '归档页读取失败'))
    }
    const payload = await response.json()
    archiveRows.value = (payload.items ?? []) as ArchiveItem[]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归档页读取失败'
  }
}

async function loadPending() {
  try {
    const response = await request(`${ENDPOINT}/pending-audits`)
    if (!response.ok) {
      throw new Error(await errorMessageOf(response, '待处理区读取失败'))
    }
    const payload = await response.json()
    pendingRows.value = (payload.items ?? []) as PendingItem[]
    pendingCount.value = payload.total ?? pendingRows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '待处理区读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(
        payload?.detail?.message ?? payload?.message ?? '采集日志动作未生效，请稍后重试',
      )
    }
    successMessage.value = payload.message ?? '操作已生效'
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集日志操作失败'
  }
}

async function runRepair(logId: number) {
  try {
    const response = await request(`${ENDPOINT}/${logId}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action: '补建审计文件' } }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload?.detail?.message ?? payload?.message ?? '补建未生效')
    }
    successMessage.value = payload.message ?? '审计文件已补建'
    errorMessage.value = ''
    await loadPending()
    await loadStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补建审计文件失败'
  }
}

async function openArchiveById(entryId: number) {
  drawer.open = true
  drawer.title = `审计文件 #${entryId}`
  drawer.archive = null
  drawer.events = []
  drawer.error = ''
  drawer.fallback = null
  try {
    const [fileResponse, entryResponse] = await Promise.all([
      request(`${ENDPOINT}/archives/${entryId}/file`),
      request(`${ENDPOINT}/${entryId}`),
    ])
    const entry = entryResponse.ok ? ((await entryResponse.json()) as Row) : null
    if (entry) {
      drawer.events = (entry.events ?? []) as Record<string, string>[]
    }
    if (!fileResponse.ok) {
      drawer.error = await errorMessageOf(fileResponse, '审计文件无法打开')
      // 文件缺失也要把已知摘要回显出来：明细可读即按明细字段构造摘要。
      if (entry) {
        drawer.fallback = buildFallbackSummary(entry)
      }
      return
    }
    drawer.archive = await fileResponse.json()
  } catch (error) {
    drawer.error = error instanceof Error ? error.message : '审计抽屉加载失败'
  }
}

function buildFallbackSummary(source: Row): Record<string, string | number | null> {
  const picked: Record<string, string | number | null> = {}
  for (const field of ['记录编号', '关联端点', '请求时间', '响应状态', '重试次数']) {
    picked[field] = (source[field] as string | number | null) ?? null
  }
  return picked
}

function openFile(row: Row) {
  void openArchiveById(row.id)
}

function openSummary(row: Row) {
  if (row.archive_available) {
    void openArchiveById(row.id)
    return
  }
  // 缺失文件：抽屉回显已知摘要，并明确提示文件未落盘、已在待处理区。
  drawer.open = true
  drawer.title = String(row['记录编号'])
  drawer.archive = null
  drawer.events = []
  drawer.fallback = buildFallbackSummary(row)
  drawer.error = '审计文件缺失：该记录被标记为已归档但没有完整文件，索引已落入未完成审计待处理区，请补建后再打开。'
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

onMounted(() => {
  void reload()
  void loadPending()
})
</script>
