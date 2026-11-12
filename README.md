# 招投标公告采集 API

面向招投标专员的公共采购公告采集服务：同步指定公告列表页与详情页，抽取
**公告编号、项目编号、公告类型、发布时间、投标截止时间、引用编号**，沿引用链
归并首次公告、更正公告与撤销公告，自动识别缺失引用、引用成环、并行更正等冲突，
并输出每条链的状态、有效截止时间与来源链接。另提供**双语核对**接口：实时比对
同站中、英文目录的公告，发现译文与原文在截标时间、公告类型、项目编号上的冲突。

技术栈：FastAPI + BeautifulSoup4 + SQLite，Docker Compose 同时启动采集 API
与一个内置的示例公告站（含中、英文目录）。

## 目录结构

```
.
├── app/                    # 采集 API
│   ├── config.py           # 环境变量配置
│   ├── db.py               # SQLite 连接与表结构
│   ├── fetcher.py          # HTTP 抓取（超时 + 有限重试）
│   ├── parser.py           # 列表页/详情页解析（BeautifulSoup，中英双语）
│   ├── chains.py           # 引用链归并、冲突识别、有效截止时间计算
│   ├── service.py          # 同步编排与持久化（含失败保留上次结果）
│   ├── bilingual.py        # 中英文目录双语核对（实时抓取，不入库）
│   ├── models.py           # 请求/响应模型
│   └── main.py             # FastAPI 路由
├── sample_site/            # 示例公告站（仅 Python 标准库，含英文目录）
│   ├── server.py
│   └── Dockerfile
├── tests/                  # pytest 测试（31 个用例）
├── Dockerfile              # API 镜像
├── docker-compose.yml      # api + sample-site 两个服务
└── requirements.txt
```

## 快速开始（Docker Compose）

```bash
docker compose up --build
```

- 采集 API：http://localhost:8000 （交互式文档 /docs）
- 示例公告站：http://localhost:8080/announcements （英文目录 /en/announcements）

触发一次同步：

```bash
curl -X POST http://localhost:8000/api/v1/sync \
  -H 'Content-Type: application/json' \
  -d '{"list_url": "http://sample-site:8080/announcements"}'
```

查询引用链与孤立异常公告：

```bash
curl http://localhost:8000/api/v1/chains
```

SQLite 数据文件存放在 Docker 卷 `api-data`（容器内 `/data/procurement.db`），
容器重建后数据不丢失。

## 本地开发（无 Docker 时）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# 终端 1：示例公告站
SAMPLE_PORT=8080 python -I sample_site/server.py

# 终端 2：API
DB_PATH=./data/procurement.db uvicorn app.main:app --reload --port 8000

# 测试
pytest -q
```

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/api/v1/sync` | 同步指定列表页及其全部详情页，请求体 `{"list_url": "..."}` |
| GET | `/api/v1/chains` | 返回全部引用链 `chains` 与孤立异常公告 `orphans` |
| GET | `/api/v1/announcements/{notice_no}` | 查询单条公告（含抓取错误、链状态） |
| GET | `/api/v1/sync-runs?limit=20` | 同步执行记录 |
| POST | `/api/v1/bilingual-check` | 中英文目录双语核对，请求体 `{"zh_list_url": "...", "en_list_url": "..."}` |

`/api/v1/chains` 响应示例（节选）：

```json
{
  "chains": [
    {
      "chain_id": "A-001",
      "project_no": "PRJ-A",
      "status": "ok",
      "effective_deadline": "2026-11-25 15:00:00",
      "announcements": [
        {
          "notice_no": "A-001",
          "notice_type": "first",
          "deadline": "2026-11-20 09:30:00",
          "ref_notice_no": null,
          "source_url": "http://sample-site:8080/notice/A-001",
          "conflict_reasons": [],
          "effective_deadline": "2026-11-25 15:00:00"
        }
      ]
    }
  ],
  "orphans": [
    {
      "notice_no": "D-001",
      "status": "orphan",
      "conflict_reasons": ["missing_reference"],
      "ref_notice_no": "X-999",
      "effective_deadline": null,
      "source_url": "http://sample-site:8080/notice/D-001"
    }
  ]
}
```

## 业务规则

### 链归并

- 公告类型映射：含「首次」→ `first`，含「更正」→ `correction`，
  含「撤销」→ `revocation`。
- 更正/撤销通过「引用编号」指向上一条公告；沿引用反向追溯，链标识取链上
  首次公告的公告编号。
- **同一项目编号下的不同首次公告保持独立**，不做合并（示例中 PRJ-G 有两条链）。
- 链状态：
  - `ok`：结构正常，返回有效截止时间；
  - `cancelled`：链上存在撤销公告，整条链失效，有效截止时间为 `null`；
  - `conflict`：链上存在冲突，**不推测截止时间**，有效截止时间为 `null`。
- 有效截止时间：取链上明确给出的最新截止时间；更正未给出截止时间时沿用链上
  此前的截止时间（继承已有数据，而非推测）。
- 撤销公告之后又挂接的公告不再属于该链，作为孤立异常公告处理
  （原因 `entry_after_revocation`）。

### 冲突识别（冲突即不推测截止时间）

| 冲突原因 | 触发条件 |
| --- | --- |
| `missing_reference` | 引用编号在库中不存在；或更正/撤销公告未填引用编号 |
| `reference_cycle` | 引用关系成环（含自环） |
| `parallel_correction` | 同一公告被两个及以上公告引用（并行更正） |
| `first_notice_with_reference` | 首次公告却携带引用编号（结构异常） |

无法归入任何链的公告进入 `orphans`，每条都带状态、冲突原因、来源链接，
且有效截止时间为 `null`。

### 详情抓取失败的容错

- 列表页抓取失败：本次同步标记 `failed`，不动库内现有数据。
- 单条详情页抓取/解析失败：
  - 此前成功过 → **保留上次完整结果**，仅记录 `last_fetch_error` 与同步时间，
    同步状态为 `partial`，响应中 `kept_last` 计数；
  - 从未成功 → 写入占位记录并归入孤立异常，避免静默丢失。
- 每次同步后基于库内现有数据全量重算链状态。

## 双语核对（中/英文目录比对）

面向采购站数据质检员：核对同一批公告的中文与英文页面，防止译文的截标时间
或公告类型与中文原文冲突。

```bash
curl -X POST http://localhost:8000/api/v1/bilingual-check \
  -H 'Content-Type: application/json' \
  -d '{
    "zh_list_url": "http://sample-site:8080/announcements",
    "en_list_url": "http://sample-site:8080/en/announcements"
  }'
```

核对规则：

- 分别抓取两份目录与全部详情页，按公告编号配对，比较**项目编号**、
  **归一化后的公告类型**（first/correction/revocation，中英文关键词均可识别）
  与**归一化后的截止时间**（支持 `Nov 20, 2026 09:30` 等英文写法）。
- 不一致字段在 `differences` 中返回**双侧页面原值**与来源链接。
- 两份目录完整稳定（首次读取成功且结束前重读一致）时，`missing` 报告
  单侧缺号（`only_in_zh` / `only_in_en`）。
- 同一语言内同号指向不同链接：该编号列入 `ambiguous` 待核实，
  **不任选一条配对**。
- 结束前重读两份目录；**目录变动、详情读取或解析失败时，保留已确认的
  差异，整次标为 `needs_review`，绝不报全部一致**。
- 只使用本次抓取的页面，不以库中旧详情补齐（本接口不读写数据库）。

响应状态：`consistent`（全部一致）/ `differences_found`（存在确认差异或
单侧缺号）/ `needs_review`（待核实，原因见 `review_reasons`，失败明细见
`failures`）。响应示例（节选）：

```json
{
  "status": "differences_found",
  "checked_pairs": 16,
  "differences": [
    {
      "notice_no": "A-003",
      "field": "deadline",
      "zh_value": "2026-11-25 15:00:00",
      "en_value": "Nov 26, 2026 15:00",
      "zh_url": "http://sample-site:8080/notice/A-003",
      "en_url": "http://sample-site:8080/en/notice/A-003"
    }
  ],
  "missing": {"only_in_zh": ["H-001"], "only_in_en": ["EN-001"]},
  "ambiguous": [],
  "failures": [],
  "review_reasons": []
}
```

## 示例公告站内置场景

| 编号 | 场景 | 预期结果 |
| --- | --- | --- |
| A-001 ~ A-003 | 首次 + 两条更正（中间一条无截止时间） | `ok`，有效截止取最新，中间项继承 |
| B-001 ~ B-003 | 首次 + 更正 + 撤销 | `cancelled`，有效截止为 null |
| C-001 ~ C-003 | 两条更正并行引用同一首次公告 | `conflict`（parallel_correction） |
| D-001 | 引用不存在的 X-999 | 孤立，missing_reference |
| E-001 / E-002 | 互相引用成环 | 孤立，reference_cycle |
| F-001 | 更正公告无引用编号 | 孤立，missing_reference |
| G-001 / G-101（+G-102） | 同项目两个独立首次公告 | 两条独立链 |
| H-001 | 可切换 500 故障 | 验证失败保留上次结果 |

故障开关（仅示例站）：

```bash
curl "http://localhost:8080/fail-toggle?enabled=1"   # 开启 H-001 详情 500
curl "http://localhost:8080/fail-toggle?enabled=0"   # 恢复
curl "http://localhost:8080/fail-status"
# 也可指定其他公告编号：/fail-toggle?enabled=1&notice=A-001（中英文详情同时生效）
```

### 英文目录（双语核对演示）

英文目录位于 `http://localhost:8080/en/announcements`，与中文同批但内置
以下差异与异常情形：

| 编号 | 情形 | 双语核对预期 |
| --- | --- | --- |
| A-003 | 英文截标时间晚一天 | `deadline` 差异 |
| B-003 | 中文撤销，英文误译为 Correction Notice | `notice_type` 差异 |
| F-001 | 英文项目编号误写为 PRJ-F-EN | `project_no` 差异 |
| H-001 | 英文目录缺失 | `missing.only_in_zh` |
| EN-001 | 英文目录独有 | `missing.only_in_en` |
| D-001 | 开启 duplicate-toggle 后同号双链接 | 列入 `ambiguous`，不配对 |
| EN-900 | 开启 mutate-toggle 后逐次增删 | 目录变动，整次 `needs_review` |

双语演示开关（仅示例站）：

```bash
curl "http://localhost:8080/en/duplicate-toggle?enabled=1"  # 英文目录 D-001 同号双链接
curl "http://localhost:8080/en/duplicate-toggle?enabled=0"  # 恢复
curl "http://localhost:8080/en/mutate-toggle?enabled=1"     # 英文目录每次请求交替增删 EN-900
curl "http://localhost:8080/en/mutate-toggle?enabled=0"     # 恢复
```

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_PATH` | `/data/procurement.db` | SQLite 文件路径 |
| `FETCH_TIMEOUT` | `10` | 单次 HTTP 超时秒数 |
| `FETCH_RETRIES` | `2` | 网络错误/5xx 的重试次数（4xx 不重试） |
| `RETRY_BACKOFF` | `0.5` | 重试退避基数秒 |
| `KEEP_LAST_ON_DETAIL_FAILURE` | `true` | 详情失败时保留上次完整结果 |
| `SAMPLE_PORT`（仅示例站） | `8080` | 示例站监听端口 |
