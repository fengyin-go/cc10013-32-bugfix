<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">API数据聚合采集中心</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向多源API接入、接口编排、数据聚合、响应校验、频率控制与数据转储的API数据聚合采集中心。</span>
        <span class="account-switch">
          当前值班：{{ store.account.name }}（{{ store.roleLabel }}）
          <select :value="store.operatorId" @change="onSwitch(($event.target as HTMLSelectElement).value)">
            <option v-for="account in ACCOUNTS" :key="account.id" :value="account.id">
              {{ account.name }} · {{ ROLE_LABELS[account.role] }}
            </option>
          </select>
        </span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
import { watch } from 'vue'

import { setOperatorId } from '@/api/client'
import { ACCOUNTS, ROLE_LABELS, useSessionStore } from '@/stores/session'

const store = useSessionStore()

watch(
  () => store.operatorId,
  (id) => setOperatorId(id),
  { immediate: true },
)

function onSwitch(operatorId: string) {
  store.switchAccount(operatorId)
}

const navItems = [{ label: "运营概览", path: "/" }, { label: "接口管理", path: "/endpoint" }, { label: "接口编排", path: "/chain" }, { label: "频率控制", path: "/freq" }, { label: "数据映射", path: "/mapper" }, { label: "响应校验", path: "/validator" }, { label: "失败重试", path: "/retry" }, { label: "数据聚合", path: "/aggregator" }, { label: "凭证管理", path: "/auth" }, { label: "缓存策略", path: "/cache" }, { label: "数据转储", path: "/dump" }, { label: "采集调度", path: "/schedule" }, { label: "采集监控", path: "/monitor" }, { label: "数据转换", path: "/transform" }, { label: "数据清洗", path: "/cleanse" }, { label: "限流配置", path: "/ratelimit" }, { label: "采集日志", path: "/log" }, { label: "增量采集", path: "/delta" }, { label: "数据导出", path: "/export" }]
</script>
