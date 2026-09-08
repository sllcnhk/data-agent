# 小工具 · 合并 CSV 文件 —— 开发与测试计划

> 版本 v2.17.0 · **已实现并验证**（2026-08-24）
> 经 11 轮 grilling 逐条确认，以下决策均已与需求方达成一致。
> 实施结果与偏差见 **§12**。

## 交付状态速览

| 项 | 结果 |
|---|---|
| 单元 / 集成测试 | `test_merge_csv.py` **169 passed**（A–M 段） |
| 回归 | merge_excel 40 · csv_tail 382 · export_csv_keyset 23 · rbac 85 —— 全过 |
| 真实数据端到端 | 84 文件 / **4.57 GiB** → **23.5 s（199 MB/s）** |
| 行数 | 精确 **5,329,303**；导出侧自报 **5,329,303** → 对账 **matched** |
| 字段内换行诊断 | `physical − rows = 0`（逐文件亦全为 0） |
| 输出校验 | BOM 恰好 1 个且在开头 · 表头恰好 1 份 · 17 列 · 尾部引号闭合 |

---

## 0. 需求

把多个**字段相同**的 CSV 文件按文件名排序，依次逐个合并为**一个**新的 CSV 文件，
表头只保留一份。典型来源是数据导出功能的**按日期分块导出**（`date_chunked`）产物。
数据量大（实测：单次导出 35+ 文件、单文件 8–151 MB、累计 2.4 GB 且仍在增长）。

### 现场实测基线（2026-08-24 采样自正在运行的导出任务）

```
customer_data/superadmin/exports/export_20260824_093905/
  export_20260601_to_20260601.csv   120,436,022 B
  export_20260602_to_20260602.csv   151,038,234 B
  ...（35 个，仍在增长）
```

| 事实 | 实测值 |
|---|---|
| 编码 | UTF-8 **带 BOM**（`EF BB BF`） |
| 表头 | `"Call ID","customer_extra_json",...,"combined_list"` —— 17 列，全字段带引号 |
| 表头跨文件一致性 | 逐字节 MD5 一致 |
| 双引号密度 | 前 20 MB 内 3,226,171 个（ClickHouse 对 String 一律加引号） |
| 引号内换行 | 首文件 0 条（物理行 132,103 == 记录数 132,103） |
| 文件末尾 | `0x0A`，引号状态闭合 |
| 同目录副产物 | 存在 `.zip`（需在文件浏览中排除） |

---

## 1. 已确认决策清单

| # | 议题 | 决策 |
|---|---|---|
| 1 | 文件来源 | **上传 + 服务器端选文件都要**，按实际情况选 |
| 2 | 输出格式 | **只输出 CSV**，不提供 xlsx 选项 |
| 3 | 实现方式 | **字节级拼接**（`copyfileobj`），非逐行解析 |
| 3b | 行数口径 | **永远精确**（numpy 引号奇偶法，见 §3）—— 撤销早期的"约数"折中 |
| 4 | 表头校验 | 列数不同→阻断；**表头文字不同默认也阻断**，`strict_header=false` 可降级为 warning |
| 5 | 写入中文件防护 | `export_jobs` 反查非终态→**阻断**；mtime 仅在 provenance 未知时给 warning（**不做静默期阻断**）；**锚定 size** |
| 6 | 结果取回 | **显示绝对路径 + 一键复制**为主；保留原生 `<a href>` 流式下载，**绝不用 blob** |
| 7 | 磁盘/取消 | 提交时磁盘空间预检（×1.1）；取消**回退到上一个文件边界**；**取消保留、失败删除** |
| 7b | 取消可见性 | 记录 `last_merged_file`，明确告知"已成功合并到哪个文件（第 N/M 个）" |
| 8 | 交付节奏 | **暂存开发**（见 §8）—— `backend/**/*.py` 会触发 uvicorn reload，打断正在跑的导出 |
| 9 | 代码组织 | **完全独立一套**，不泛化现有 merge_excel |
| 9b | 明细粒度 | `source_files` 回填**每个源文件贡献的行数** |
| 10 | 排序 | **自然排序**（数字感知）为默认，提交前展示排序结果供确认 |
| 10b | 编码 | 规则是**编码一致性**，不是"必须 UTF-8"；全非 UTF-8 且猜测一致→放行；混合→阻断；UTF-16/32→硬阻断 |
| 11 | 选文件入口 | **四个并列入口**：导出任务 / 目录 / 手工文件 / 本地上传，共用一个已选清单，可混合 |
| 11b | 对账 | 用 `export_jobs.chunk_files[].rows` 与合并结果**双向交叉对账** |
| 11c | 并发 | 全局**串行**（`max_workers=1`） |
| 11d | 上传上限 | **1 GB**/文件（Excel 工具是 200 MB） |

---

## 2. 关键约束：uvicorn reload 会打断正在运行的导出

[run.py:41-47](../run.py#L41-L47)：

```python
uvicorn.run("backend.main:app", reload=True,
            reload_dirs=["backend"], reload_includes=["*.py"])
```

- **修改 / 新建 `backend/**/*.py` → 进程重载 → 正在跑的 `asyncio.create_task` 导出任务直接死亡**，
  DB 里的 job 永久卡在 `running`，输出 CSV 停在半条记录上。
- **安全**（不触发 reload，`FileFilter` 只放行 `*.py`）：`.md`、`frontend/**/*.ts(x)`、`.pyc` / `__pycache__`。
- 结论：所有 Python 代码先在 **`c:\tmp\merge_csv_dev\`**（uvicorn 监视范围之外）开发与单测，
  导出结束后一次性拷入，**只触发一次可控重启**。

---

## 3. 核心算法：numpy 引号奇偶扫描

### 3.1 原理

RFC4180 的一个性质：`""` 转义是**两个**引号，不改变奇偶性。因此

> **截至字节 i 的引号累计数为奇数 ⟺ 字节 i 位于引号内**

恒成立。于是：

```python
a      = np.frombuffer(chunk, dtype=np.uint8)
parity = np.bitwise_xor.accumulate(a == 0x22)   # 前缀引号奇偶
inside = np.bitwise_xor(parity, carry)          # carry = 上一 chunk 结束时的奇偶
term   = (a == 0x0A) & ~inside                  # 引号外的 \n = 真正的记录终止符

records        += int(np.count_nonzero(term))
physical_lines += int(np.count_nonzero(a == 0x0A))
carry           = bool(inside[-1])
```

跨 chunk 只需携带 **1 bit** 状态（`carry`），任意分片结果等价。

### 3.2 实测性能与正确性

| 项 | 结果 |
|---|---|
| 吞吐 | **235 MB/s**（114.9 MB 文件 0.49 s，单线程） |
| 合法 RFC4180 模糊测试 | **6000 例 × chunk∈{1,2,3,7,64,1M}：行数错 0 / 尾部奇偶错 0 / 表头边界错 0** |
| 真实文件 | `first_end=254`，`csv` 模块解析恰好 1 条记录，17 列，列名正确 |

因为合并本身**已经要读取每个字节**，奇偶扫描是对已在内存中的数据做纯 CPU 向量运算，
且 235 MB/s 快于磁盘写入 —— **精确行数实质上是免费的**。

### 3.3 三个白捡的副产品

1. **表头边界**：第一个 `term` 位置 + 1 = 表头结束偏移 → 后续文件从这里开始拷贝。
2. **截断检测**：拷完一个文件时 `carry` 必须为 `False`（引号闭合）。为 `True` 说明文件被截断或仍在写入
   —— 比 mtime 启发式强得多，且免费。
3. **诊断信息**：`physical_lines − records` = 该文件字段内换行的条数。

### 3.4 已知限制（必须在文档与 UI 中如实说明）

奇偶模型**无法区分**「`""` 转义」与「先闭合再重开」—— 两者奇偶序列同构。这正是记录计数
仍然正确的原因，但也意味着**"开引号位置是否合法"在该模型内不可表达**。因此对**畸形 CSV**
（双引号出现在非引号字段的中间，如 `ab"cd,e`）：

- 记录计数可能与 Python `csv` 模块的宽容解读不一致（实测 452/3000 分歧）；
- **字节拷贝永远字节精确**，不受影响；
- 唯一有真实数据损坏风险的是**表头边界错位** → 用 §4 的 V3 交叉校验硬挡；
- 尾部奇偶检测挡住截断。

行数口径定义为「**引号外 `\n` 的个数，按 RFC4180**」。ClickHouse `FORMAT CSVWithNames`
与 Excel 导出均符合 RFC4180，此口径对它们精确。文档中明确写出。

> 不复用 [csv_tail.py](../backend/services/csv_tail.py) 的 `CsvRecordBoundaryScanner`：
> 它是 Python 逐字节 for 循环（5–15 MB/s），慢 20–50 倍。**该模块保持原样不动**（导出续传仍在用）。

---

## 4. 校验管线

### 4.1 提交时（同步，快 —— 每文件读头部 64 KB + 尾部 4 KB）

| 编号 | 校验 | 失败处理 |
|---|---|---|
| V1 | 文件存在、可读、非空 | 阻断 |
| V2 | 编码分类与一致性（§4.3） | 阻断 / warning |
| V3 | **表头边界交叉校验**：奇偶法定位 `first_end`，切出该段用 `csv` 模块解析，**必须恰好 1 条记录** | 阻断 |
| V4 | 列数一致（基准 = 排序后首文件） | 阻断 |
| V5 | 表头文字一致（`strict_header=true`） | 阻断，报出「第 N 列：首文件 `X` vs `f.csv` `Y`」；`false` → warning |
| V6 | 末尾字节是否为 `\n`（弱检测） | 非 `\n` → 运行时补齐 + warning |
| V7 | `export_jobs` 反查（`file_path` 及 `chunk_files[].file_path`），命中非终态（pending/running/cancelling） | 阻断 |
| V8 | mtime < 60 s **且** V7 查不到 provenance | warning（不阻断） |
| V9 | 磁盘空间预检：`disk_usage(output_dir).free > sum(sizes) × 1.1` | 阻断，报具体数字 |
| V10 | 锚定 `size` 快照写入 `source_files[].size` | — |

> 说明：**尾部引号闭合检测无法在提交时廉价完成**（奇偶性需从文件头累积），
> 故移至运行时 R5。

### 4.2 运行时（每源文件）

| 编号 | 动作 |
|---|---|
| R1 | **只读锚定的 `size` 字节**，多出来的一律不读（防止校验与拷贝之间文件又被追加） |
| R2 | 记录本文件开始前的**输出偏移** `offset_before`（取消/失败回退点） |
| R3 | 首文件：保留 BOM（若有）+ 表头，从 0 开始拷；后续文件：**逐文件判断**是否带 BOM，从 `first_end` 开始拷 |
| R4 | 逐 chunk（8 MB）：numpy 奇偶扫描 → `records` / `physical_lines`；同时写出 |
| R5 | 文件结束：`carry` 必须为 `False`。为 `True` → **失败**，`truncate(offset_before)`，报「文件 X 引号未闭合，疑似被截断或仍在写入」 |
| R6 | 文件结束：若最后字节非 `\n` → **补一个 `\n`** + warning（"末尾无换行符，已补齐；若该文件当时仍在写入，其末行可能残缺"） |
| R7 | 回填 `source_files[i] = {..., rows, physical_lines, bytes_written}` |
| R8 | 更新 `done_files` / `done_bytes` / `total_rows` / `last_merged_file` |
| R9 | 取消检查在 **chunk 边界**即时响应 → `truncate(offset_before)`，丢弃当前文件已拷部分，状态 `cancelled`，**保留结果** |

**取消后的结果文件 100% 是有效 CSV**：截断点恰在完整文件边界，且每个文件都已补齐尾换行。
`last_merged_file` + `done_files` 让用户能精确知道续传起点。

### 4.3 编码分类与一致性规则

1. 读每文件头部 64 KB + 前 4 字节 BOM 嗅探。
2. 分类：
   - `utf16` / `utf32`（BOM `FF FE` / `FE FF` / 4 字节变体）→ **硬阻断**
     （UTF-16 中 `\n` 是 `0A 00`，字节级记录边界与 BOM 剥离全部不成立）
   - `utf8-bom`（`EF BB BF` 开头）
   - `ascii`（全部字节 < 0x80）→ **与任何单/双字节编码兼容，不参与一致性判定**
   - `utf8`（严格 UTF-8 解码成功且含非 ASCII）
   - 其他 → 用 `charset_normalizer`（v3.4.4，已随 `requests` 安装）猜编码名
3. 判定：
   - 全 utf8 系 → 输出 UTF-8，**BOM 跟随首文件**
   - 全非 utf8 且猜测编码名一致（如都是 `gb18030`）→ **放行**，输出与源同编码、不加 BOM，
     记 warning：「所有文件均为非 UTF-8（推测 gb18030），已按原字节合并；推测基于统计，请自行确认」
   - 混合（utf8 掺非 utf8，或两种不同的非 utf8）→ **阻断**，列出每文件分类
4. BOM 剥离是**逐文件**判断的（不假定所有文件都带）。

---

## 5. 数据模型

### `merge_csv_jobs`（新表，`backend/models/merge_csv_job.py`）

```python
id                UUID PK
user_id           String(64)   idx
username          String(100)
job_name          String(200)  nullable

# 配置
has_header        Boolean  default True
strict_header     Boolean  default True
sort_mode         String(20) default 'natural'   # natural | lexicographic

# 源文件清单（排序后）；运行中回填 rows/physical_lines/bytes_written
source_files      JSONB
# [{filename, file_path, size, origin, encoding,
#   rows, physical_lines, bytes_written,
#   export_job_id, expected_rows}]           # origin: upload | server

status            String(20) default 'pending' idx
# pending → running → completed / failed
#                   ↘ cancelling → cancelled

# 进度（字节 100% 精确且免费）
total_files       Integer
done_files        Integer  default 0
total_bytes       BigInteger
done_bytes        BigInteger default 0
total_rows        BigInteger default 0        # 精确记录数（不含表头）
total_physical_lines BigInteger default 0     # 诊断用

last_merged_file  String(500)                 # 最后一个完整合入的源文件名

# 输出
output_filename   String(500)
file_path         String(1000)
file_size         BigInteger
output_encoding   String(32)                  # utf-8-sig | utf-8 | gb18030 | ...

# 对账
expected_total_rows BigInteger nullable       # 来自 export_jobs.chunk_files[].rows 之和
reconcile_status  String(20)  nullable        # matched | mismatched | unavailable
reconcile_detail  JSONB       nullable        # 逐文件差异

warnings          JSONB
error_message     Text
created_at / started_at / finished_at / updated_at
```

索引：`user_id` / `status` / `created_at`（同 `merge_excel_jobs`）。

迁移脚本：`backend/scripts/migrate_merge_csv.py`（additive DDL，可在服务运行时执行）。

---

## 6. 后端文件与 API

| 文件 | 职责 |
|---|---|
| `backend/services/csv_merge_core.py` | **纯逻辑，零依赖**（只 import numpy/csv/pathlib）：奇偶扫描器、表头定位与交叉校验、编码分类、自然排序、一致性校验。100% 可单测 |
| `backend/services/csv_merge_service.py` | job 状态机 + 线程池（`max_workers=1`）+ 进度落库 + 取消/失败回退 + 对账 |
| `backend/api/tools_merge_csv.py` | REST 端点 |
| `backend/models/merge_csv_job.py` | ORM |
| `backend/scripts/migrate_merge_csv.py` | 建表 |
| `backend/scripts/init_rbac.py` | **修改**：新增 `tools:merge_csv` 权限 |
| `backend/main.py` | **修改**：注册 router |
| `backend/models/__init__.py` | **修改**：导入新 model |

### API（前缀 `/api/v1/tools/merge-csv`，全部要求 `tools:merge_csv`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/export-jobs` | 入口①：列出本用户 CSV 导出任务（含 `chunk_files` 摘要：文件数、总大小、总行数、各分块状态）。`.csv` 缺失但同名 `.zip` 存在 → 标注「已压缩，请先解压」 |
| GET | `/dirs` | 入口②：列 `customer_data/{username}/` 下含 `.csv` 的目录（文件数、总大小、最新 mtime） |
| GET | `/files` | 入口③：列指定目录下 `.csv`（**忽略 `.zip` 等**），支持文件名模式过滤；路径必须在 `customer_data/{username}/` 内（防穿越） |
| POST | `/upload` | 入口④：单文件流式落盘，上限 **1 GB**，仅 `.csv` |
| POST | `/preview` | 提交前预检：跑 V1–V10，返回排序结果、基准表头、每文件编码/大小、warnings、阻断原因、磁盘空间、预估耗时。**不建 job** |
| POST | `/execute` | 建 job 并后台执行 |
| GET | `/jobs/{id}` | 状态与进度 |
| POST | `/jobs/{id}/cancel` | 取消 |
| DELETE | `/jobs/{id}` | 删记录 + 删输出文件；**`origin=server` 的源文件绝不删**，`origin=upload` 的临时文件清理 |
| GET | `/jobs` | 分页历史 |
| GET | `/jobs/{id}/download` | 流式 `FileResponse`（支持 Range）；前端用原生 `<a href>`，**不用 blob** |

`/preview` 是独立端点，让"排序对不对、表头一致不一致、磁盘够不够"在**建 job 之前**就暴露。

---

## 7. 前端

| 文件 | 说明 |
|---|---|
| `frontend/src/services/mergeCsvApi.ts` | 新增 |
| `frontend/src/pages/tools/MergeCsv.tsx` | 新增 |
| `frontend/src/App.tsx` | +`<Route path="/tools/merge-csv" element={<MergeCsv />} />` |
| `frontend/src/components/AppLayout.tsx` | +`{ key:'/tools/merge-csv', label:'合并CSV文件', perm:'tools:merge_csv' }` |

> 路由/菜单可在阶段 0 就加：`tools:merge_csv` 权限尚未入库时菜单项**自动隐藏**，不会出现死链接。

### 页面结构

```
区域 1  选择文件（四个并列入口，Segmented 等权切换）
        ┌ 导出任务 ┬ 目录 ┬ 手工选择 ┬ 本地上传 ┐
        └──────────┴──────┴──────────┴──────────┘
区域 2  已选文件（唯一事实源，可混合来源）
        · 自然排序后的完整列表，显示序号 / 文件名 / 大小 / 来源 / 编码
        · 折叠态只显示「共 N 个，首:xxx  末:yyy，合计 X.X GB」
        · 可单独移除
区域 3  选项 + 预检
        · 包含表头(默认开) / 严格表头校验(默认开) / 排序方式(自然·默认 | 字典) / 任务名称
        · 【预检】按钮 → 展示阻断项(红)、warnings(黄)、磁盘空间、预估耗时
        · 【开始合并】仅在预检通过后可点
区域 4  历史任务（分页 + 2 s 轮询）
        · 进度条按 done_bytes/total_bytes（精确）
        · 副文本：done_files/total_files · total_rows 行 · last_merged_file
        · 完成后：绝对路径 + 【复制路径】+【下载】+【对账结果】
        · 对账一致 → 绿 ✓「导出侧 4,231,556 行 = 合并后 4,231,556 行」
          不一致 → 红字并列出逐文件差异
        · cancelled/failed → 明确显示「已成功合并到 xxx.csv（第 23/60 个）」
        · 源文件明细可展开：每文件贡献行数
```

---

## 8. 开发阶段

### 阶段 0 —— 现在，零重启风险

- [x] 本计划文档 `docs/MERGE_CSV_PLAN.md`（`.md` 不触发 reload）
- [ ] `frontend/src/services/mergeCsvApi.ts`
- [ ] `frontend/src/pages/tools/MergeCsv.tsx`
- [ ] `App.tsx` / `AppLayout.tsx` 接线（权限未入库 → 菜单自动隐藏）

### 阶段 1 —— 现在，暂存目录开发 + 单测

工作目录 `c:\tmp\merge_csv_dev\`（**在 uvicorn 监视范围之外**）

- [ ] `csv_merge_core.py` —— 纯逻辑核心
- [ ] `test_csv_merge_core.py` —— 测试段 **A / B / C / D / E**（纯逻辑，不需要服务与 DB）
- [ ] 用真实文件（那 35 个 CSV，只读）验证：表头一致性、编码分类、精确行数、吞吐

### 阶段 2 —— 导出结束后，由你择时（**唯一一次重启**）

1. `c:\tmp\merge_csv_dev\*.py` → `backend/services/` 等目标位置（触发一次 reload）
2. `migrate_merge_csv.py` 建表
3. `init_rbac.py` 补种 `tools:merge_csv` 权限
4. 全量 `pytest`：新增段 **F–N** + 回归 `test_merge_excel.py`
5. **真实数据端到端**：把那 35 个（届时可能 80+）CSV 真跑一次，核对对账结果
6. 干净重启后端，前端 HMR 自动生效

> 命令一律用 `/d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest ... -v -s`
> 测试数据命名一律 `from test_utils import make_test_username, make_test_rolename`

---

## 9. 测试计划 `test_merge_csv.py`

| 段 | 主题 | 用例要点 |
|---|---|---|
| **A** | 奇偶扫描核心 | A1 纯文本 / A2 引号含逗号 / A3 引号含换行 / A4 `""` 转义 / A5 `"a""\nb"` / A6 CRLF / A7 空字段 `"",""` / A8 带 BOM / A9 **chunk 恰好切在 `""` 中间** / A10 chunk∈{1,2,3,7,64,1M} 结果等价 / A11 **6000 例合法 RFC4180 模糊 vs `csv` 模块** / A12 尾部未闭合 → `carry=True` / A13 `physical_lines − records` == 引号内换行数 |
| **B** | 表头边界 | B1 `first_end` 偏移正确 / B2 切段用 `csv` 解析恰好 1 条 / B3 表头含引号内换行 / B4 超长表头（>1 MB，读到上限 16 MB 后报错）/ B5 畸形表头 `ab"cd,e` → V3 阻断 / B6 只有表头无数据行的文件 |
| **C** | 编码 | C1 utf8 / C2 utf8-bom / C3 纯 ascii 与任何编码兼容 / C4 全 gb18030 → 放行 + warning / C5 utf8 + gbk 混合 → 阻断 / C6 gbk + big5 → 阻断 / C7 UTF-16 LE/BE → 硬阻断 / C8 逐文件 BOM 剥离（部分带部分不带） |
| **D** | 结构校验 | D1 列数不同 → 阻断且报出具体文件与列数 / D2 表头文字不同 + `strict_header=true` → 阻断且指出第几列 / D3 同上 + `false` → warning + 按位置合并 / D4 `has_header=false` → 只比列数、不跳行 |
| **E** | 排序 | E1 `part1..part10` 自然排序正确 / E2 定长日期文件名 **自然排序与字典排序逐字节一致** / E3 `sort_mode=lexicographic` 时 part10 排在 part2 前（行为可选） / E4 中文文件名 / E5 大小写混合 |
| **F** | 合并正确性 | F1 输出行数 == 各文件行数之和 / F2 表头恰好 1 份 / F3 BOM 恰好 1 个且在文件开头 / F4 缺尾换行 → 补齐且不粘行 / F5 空文件跳过 / F6 只有表头的文件贡献 0 行 / F7 输出用 `csv` 模块完整重读，记录数与列数全对 / F8 单文件（退化为拷贝） / F9 首文件带 BOM、后续不带 |
| **G** | 取消 | G1 chunk 边界即时响应 / G2 `truncate` 回上一文件边界 / G3 取消后结果用 `csv` 重读仍是有效 CSV / G4 `last_merged_file` 与 `done_files` 一致且诚实 / G5 pending 阶段取消 → 直接 `cancelled` / G6 取消后**保留**输出文件 |
| **H** | 失败与清理 | H1 引号未闭合 → 失败 + `truncate` 回本文件起点 / H2 模拟 disk full（monkeypatch write 抛 IOError）→ 失败 + 删半截文件 / H3 源文件中途被删 → 失败 / H4 删除 job 时 `origin=server` 源文件**未被删** / H5 删除 job 时 `origin=upload` 临时文件被清理 |
| **I** | 磁盘预检 | I1 空间不足 → 阻断且报出「需要 X / 剩余 Y」 / I2 恰好 1.1 倍边界 |
| **J** | 写入中防护 | J1 `export_jobs.file_path` 命中 running → 阻断 / J2 命中 `chunk_files[].file_path` running → 阻断 / J3 命中 completed → 放行 / J4 provenance 未知 + mtime<60s → warning 不阻断 / J5 **锚定 size**：校验后文件被追加，只读锚定字节数 |
| **K** | API 与权限 | K1 无 token → 401 / K2 无 `tools:merge_csv` → 403 / K3 superadmin 放行 / K4 upload 非 `.csv` → 400 / K5 upload >1 GB → 413 / K6 `/files` 路径穿越 `../` → 403 / K7 `/preview` 不建 job / K8 `/execute` → job_id / K9 `/jobs/{id}` 进度字段齐全 / K10 `/cancel` 非法状态 → 400 / K11 `/download` 未完成 → 400 / K12 `/export-jobs` 标注 `.zip` 已压缩 |
| **L** | 对账 | L1 行数一致 → `matched` / L2 不一致 → `mismatched` + 逐文件差异 / L3 无 `expected_rows`（目录/上传来源）→ `unavailable` / L4 部分文件有 expected_rows |
| **M** | 前端 | M1 四入口切换互不干扰 / M2 已选清单可混合来源、可移除 / M3 排序结果展示（首/末/总数） / M4 预检未过时【开始合并】禁用 / M5 进度条用 `done_bytes` / M6 复制路径 / M7 下载走 `<a href>` 非 blob / M8 轮询在无活跃任务时停止 |
| **N** | 回归 | N1 `test_merge_excel.py` 全过 / N2 `test_csv_tail.py` 全过（未改动） / N3 `test_data_export_csv_keyset.py` 全过 / N4 `test_rbac.py` 权限矩阵新增项不破坏既有 |

**性能验收**（阶段 2 用真实数据）：2.4 GB / 35 文件，端到端 **≤ 60 s**，
精确行数与导出侧对账一致，峰值内存 < 200 MB。

---

## 10. 风险与已知限制

| 风险 | 处理 |
|---|---|
| 畸形 CSV（引号在非引号字段中间）行数口径与 `csv` 模块分歧 | 表头交叉校验硬挡最危险路径；口径按 RFC4180 定义并写入文档；字节拷贝不受影响 |
| `charset_normalizer` 编码推测可能错 | 非 UTF-8 放行时**必给 warning**，不静默通过 |
| 头部 64 KB 编码检测不覆盖文件深处 | 编码是文件级属性，实践上不会前半 UTF-8 后半 GBK；不做全量解码（性能） |
| 输出可能达数十 GB 占满磁盘 | 提交时预检 ×1.1；运行时 IOError → 失败并删半截文件；提供删除按钮 |
| 用户勾中仍在写入的文件 | `export_jobs` 反查 + 锚定 size + 运行时引号闭合检测（三层） |
| 一次 uvicorn 重启不可避免 | 压缩为阶段 2 的单次可控重启，由需求方择时 |
| 阶段 0 前端先落地，API 尚不存在 | 权限未入库 → 菜单自动隐藏，用户看不到入口 |

---

## 12. 实施结果与相对计划的偏差

### 12.1 计划外发现并修掉的问题

| # | 问题 | 影响 | 处理 |
|---|---|---|---|
| 1 | 用了 `asyncio.to_thread`，它是 **Python 3.9+**；本环境 `python -V` = **3.8.20** | `/preview` 与 `/execute` 直接 500 | 改用 `loop.run_in_executor` + `functools.partial`（`run_in_executor` 不吃 kwargs） |
| 2 | 计划里写的 `export_jobs.chunk_files` 不存在，实际字段是 **`output_files`** | 入口① 与对账全部拿不到数据 | 修正，并把实测 JSON 形状写进 docstring（`{index, date_start, date_end, filename, file_path, file_size, rows, sheets, status, _depth, _retry_count}`） |
| 3 | 上传文件存成 `{upload_id}.csv`，而排序是按 `filename` 做的 → **上传路径变成按 UUID 随机排序** | 直接违背「按文件名排序依次合并」这一核心需求，且**不报任何错** | 改存 `uploads/{upload_id}/{原文件名}`，原名天然保留；`_safe_upload_id()` 校验 UUID、`_safe_filename()` 剥目录成分。回归 K10b 故意**倒序上传** part10→part2→part1 验证 |
| 4 | 首文件把表头也喂进扫描器、事后 `records - 1` → `physical_lines` 多算一行表头 | `physical − rows`（前端「字段内换行」列）**恒定虚高 1**，假阳性；4.57 GB 真实数据上误报「1 处字段内换行」，实际 0 处 | 改为**表头只写不扫**（`copy_start` / `scan_start` 分离），对「表头自身含引号内换行」也精确。回归 F9b / F9c |

### 12.2 相对计划的设计变更

- **行数口径从「口径 C 折中」升级为「永远精确」**。计划初稿是「无引号才精确、有引号标约数」，
  但实测你的数据每 20 MB 有 322 万个双引号（ClickHouse 对 String 一律加引号），那个折中
  等于永远显示「约 N 行」。改用 numpy 奇偶法后精确且实质免费，`rows_exact` 字段因此**取消**。
- **编码规则从「必须 UTF-8」改为「必须一致」**（需求方提出）。全 GBK 可放行。
- **mtime 静默期阻断取消**（需求方明确不接受等待），降级为「provenance 未知 + 刚被修改」时的
  非阻断 warning。
- **`_build_active_export_path_index()` 增加 Python 侧终态复判**，不只依赖 SQL 过滤 ——
  这个不变量（"只有未完成的导出才算活跃"）决定会不会把已完成的导出误判成「仍在写入」而
  错误阻断合并，值得在两处都钉住，成本为零。

### 12.3 奇偶法正确性的验证过程（值得留档）

1. 第一版模糊测试用**随机字节**生成「CSV」，炸了 452/3000 —— 但失败样本全是 `bb",ab` 这类
   **引号出现在非引号字段中间**的非法 RFC4180。生成器造的是垃圾，不是 CSV。
2. 第二版加了「开引号位置是否合法」检测器，在合法输入上误报 4387/6000 —— 因为 `""""`
   的奇偶序列与「闭合后重开」同构，**该判断在奇偶模型内不可表达**。这恰恰解释了为什么
   计数仍然正确（两种解读对计数等价）。检测器废弃。
3. 终版：用**独立实现的三状态机**（区分转义与闭合）作真值，并先自证它与 Python `csv`
   模块一致。结果 **6000 例 × chunk∈{1,2,3,7,64,1M}：行数错 0 / 引号态错 0 / 首记录边界错 0**。
4. 表头边界改用 `csv` 模块精确交叉校验（表头只几 KB，零成本），挡住畸形 CSV 唯一的
   真实损坏路径。

### 12.4 已知遗留

- `tsconfig.json` 缺 `"types": ["vite/client"]`，导致全项目 17 处 `import.meta.env` 类型报错
  （含既有的 `mergeExcelApi.ts`）。本次新增文件沿用同一模式，**未引入新错误类型**，也未修
  这个共享配置。
- **合并 Excel 工具有与 §12.1 #3 相同的既有 bug**：
  [tools_merge_excel.py:143](../backend/api/tools_merge_excel.py#L143) 存的 `filename` 是
  `{upload_id}{suffix}`，[excel_merge_service.py:269](../backend/services/excel_merge_service.py#L269)
  按它排序 → 上传的 Excel 也是按 UUID 排序。不在本次范围，未修。

---

## 11. 待办确认（不阻塞开发）

- 输出文件命名：`{job_name or 'merged'}_{job_id}.csv`（沿用 Excel 工具惯例）
- 输出目录固定 `customer_data/{username}/tools/merge_csv/jobs/`（受
  `filesystem_write_allowed_dirs` 边界约束，不开放自定义路径）
- 是否需要「导出任务分块全选时自动排除 `status != completed` 的分块」——倾向：自动排除并提示
