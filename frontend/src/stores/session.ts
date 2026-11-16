import { defineStore } from 'pinia'

export type Role = 'readonly' | 'archivist' | 'admin'

export type Account = {
  id: string
  name: string
  role: Role
  endpoints: string[]
}

// 与后端 app/audit_auth.py 的账号注册表保持一致；角色以后端注册表为准，
// 前端只负责显隐入口，真正的拦截仍在后端完成。
export const ACCOUNTS: Account[] = [
  { id: 'readonly-01', name: '只读审计员', role: 'readonly', endpoints: ['EP-LOG-WEB', 'EP-LOG-ORDER'] },
  { id: 'archivist-01', name: '归档专员', role: 'archivist', endpoints: ['EP-LOG-WEB', 'EP-LOG-ORDER', 'EP-LOG-PAY'] },
  { id: 'admin-01', name: '保留期管理员', role: 'admin', endpoints: ['*'] },
]

export const ROLE_LABELS: Record<Role, string> = {
  readonly: '只读账号',
  archivist: '归档账号',
  admin: '管理员账号',
}

export const useSessionStore = defineStore('session', {
  state: () => ({
    operatorId: 'readonly-01',
    shiftLabel: '白班 08:00-20:00',
    scope: 'API数据聚合采集中心',
  }),
  getters: {
    account(state): Account {
      return ACCOUNTS.find((item) => item.id === state.operatorId) ?? ACCOUNTS[0]
    },
    role(): Role {
      return this.account.role
    },
    roleLabel(): string {
      return ROLE_LABELS[this.role]
    },
    // 只读账号在界面上不出现任何写动作入口；后端仍会再次校验。
    canWrite(): boolean {
      return this.role !== 'readonly'
    },
  },
  actions: {
    switchAccount(operatorId: string) {
      this.operatorId = operatorId
    },
  },
})
