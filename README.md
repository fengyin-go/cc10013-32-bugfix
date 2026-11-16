# API数据聚合采集中心

面向多源API接入、接口编排、数据聚合、响应校验、频率控制与数据转储的API数据聚合采集中心。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 接口管理 | `endpoint` | API端点 | 端点编号、接口地址、请求方法 |
| 接口编排 | `chain` | 调用链路 | 链路编号、端点序列、数据传递映射 |
| 频率控制 | `freq` | 频率规则 | 规则编号、目标端点、每秒上限 |
| 数据映射 | `mapper` | 映射规则 | 规则编号、源字段路径、目标字段名 |
| 响应校验 | `validator` | 校验规则 | 规则编号、关联端点、校验字段 |
| 失败重试 | `retry` | 重试策略 | 策略编号、关联端点、最大重试 |
| 数据聚合 | `aggregator` | 聚合任务 | 任务编号、输入端点列表、合并策略 |
| 凭证管理 | `auth` | 认证凭证 | 凭证编号、关联端点、认证类型 |
| 缓存策略 | `cache` | 缓存规则 | 规则编号、关联端点、缓存键模板 |
| 数据转储 | `dump` | 转储配置 | 配置编号、数据来源、存储类型 |
| 采集调度 | `schedule` | 调度计划 | 计划编号、关联链路、调度表达式 |
| 采集监控 | `monitor` | 监控指标 | 指标编号、关联端点、成功率阈值 |
| 数据转换 | `transform` | 转换脚本 | 脚本编号、输入数据源、转换表达式 |
| 数据清洗 | `cleanse` | 清洗规则 | 规则编号、目标字段、空值替换 |
| 限流配置 | `ratelimit` | 限流规则 | 规则编号、目标端点、令牌速率 |
| 采集日志 | `log` | 日志记录 | 记录编号、关联端点、请求时间 |
| 增量采集 | `delta` | 增量游标 | 游标编号、目标端点、增量字段 |
| 数据导出 | `export` | 导出通道 | 通道编号、输出数据集、目标系统 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 采集日志归档与审计（log 模块）

针对「归档失败却被标记已归档、清理抹掉字段、只读越权」三类问题，log 模块有一套独立
于通用状态机的审计约束：

- **先落文件再切状态**：`归档记录` 先构造完整审计文件并通过两级校验和复核
  （`app/archive_file.py`），复核通过才写入 `archive` 并把状态切成「已归档」；
  任何一步失败状态保持原样，并在**未完成审计待处理区**登记索引。
- **待处理区与对账**：`GET /api/log/pending-audits` 返回「已归档但缺文件/文件损坏」的
  索引；服务端每次读取都会全量对账，索引被误删会自动补回，`补建审计文件` 成功后自动移出。
- **清理只改可见范围**：`清理日志` 只翻转 `visible`，请求时间、响应状态、重试次数等
  原始字段、完整文件、摘要链与列表原始顺序全部保留；默认列表隐藏，`include_hidden=true`
  可找回。审计文件不完整时拒绝清理。
- **保留期冲突**：记录带 `retain_until`，当天不晚于保留期时清理请求返回
  409 `RETENTION_CONFLICT`，保留期保护优先，拒绝原因写入事件链，可在审计抽屉里查到。
- **按端点授权**：身份经请求头 `X-Operator-Id`（可选 `X-Role`）传入，账号注册表在
  `app/audit_auth.py`，角色以注册表为准、前端自报角色不能提权。只读账号只能看到受控
  共享端点且无任何写入口；写动作越权返回 403 并把原因记入事件链。

| 账号 | 角色 | 授权端点 | 写动作 |
| --- | --- | --- | --- |
| `readonly-01` | 只读审计员 | EP-LOG-WEB、EP-LOG-ORDER | 全部拒绝 |
| `archivist-01` | 归档专员 | EP-LOG-WEB、EP-LOG-ORDER、EP-LOG-PAY | 归档/补建/清理 |
| `admin-01` | 保留期管理员 | 全部 | 归档/补建/清理 |

新增接口（均挂在 `/api/log` 下）：`GET /archives`（归档页同源摘要）、
`GET /archives/{id}/file`（打开完整文件，缺失/损坏返回 409 结构化错误）、
`GET /pending-audits`（待处理区索引）、`GET /stats`（看板计数）。
注意静态路径注册在 `/{entry_id}` 之前，避免 `export` 被当成 entry_id 解析成 422。

### 复测

```bash
cd backend
.venv/bin/python run_tests.py            # 15 个单元/接口回归用例（任意工作目录均可运行）
# 另开终端起服务后：./run.sh
.venv/bin/python run_tests.py e2e        # 三账号 + 保留期冲突的 32 项端到端复测
# e2e 依赖服务处于种子初始态；复测前重启一次后端即可
```

领域：数据工程
子方向：数据采集、爬虫与RPA
