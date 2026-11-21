# 潮汐观测资料中心站日异常数仓

本库供观测值班、质控、日结和夜班复核共用。值班员登记站日上限并交接具名原批，复核员提交勘误，质控员试算超限量，日结员签发固定清单。夜班重开库后按行位取页，从清单结果追溯当时采用的勘误与原观测。

## 运行

需要 Python 3.11 标准库。在项目根目录执行：

```text
python3 -B check_project.py
python3 -B demo.py
```

回归入口由控制台物化。演示读取 `examples/bay_day.json`，在测试临时目录建库并重开。任何实际结果与手算不符时命令以非零状态结束。

## Python 接口

使用 `warehouse.open_warehouse(path)` 打开 SQLite 库，并用 `warehouse.initialize(db)` 初始化。连接由调用方关闭。首次初始化补入湾口站 `WB01`。重复初始化保留既有站名、自建站点及业务数据。公开入口自行管理一致读事务或写事务，请在事务外调用。所有结果为不可变数据对象。

| 接口 | 参数与结果 |
| --- | --- |
| `limits.day_policy.register` | `db, station, day, limit, source`。返回首次冻结的 `DayPolicy`。同站日同上限复用首次来源，异值拒绝。 |
| `batches.intake.ingest` | `db, station, day, batch_id, rows`。`rows` 是由 `(key, reading)` 组成的有序序列。接纳回执列出原槽引用。同批号同站日同原序复领，异件或任一键冲突整批拒绝。 |
| `facts.corrections.correct_fact` | `db, request`。使用 `CorrectionRequest(correction_id, old, action, reason, reading=None)`。`OldImage` 包含站、日、键、读数和完整 `FactSource`，也可用 `OldImage.from_fact(...)` 构造。`replace` 支持整数或缺测，`withdraw` 要求 `reading=None`。首执含更新后的事实，撤销时该字段为 `None`。 |
| `facts.corrections.relabel_facts` | `db, request_id, reason, changes`。`changes` 是按申请项顺序排列的 `(OldImage, new_key)` 非空序列，整单限同一站日。返回含 `request_id`、`reason`、`after` 的 `LabelReceipt`，`after` 与申请项同序，保留读数与原槽并带上新标签身份。允许互换与闭环，目标键不得占用单外现行键。 |
| `quality.evaluate.preview` | `db, station, day`。返回口径、异常明细和三类覆盖统计。 |
| `delivery.issue.publish` | `db, station, day, report_id`。返回 `IssuedReport`，包含固定报表和定稿首执。同号同站日复领原成果，异绑定拒绝。 |
| `delivery.pages.read_page` | `db, report_id, start, length`。`start` 为零基绝对行位，`length` 为非负整数。零长和越尾均返回空切片及原总行数。 |
| `delivery.source_lookup.read_source` | `db, report_id, position`。从固定行返回原槽、定稿时采用的读数勘误编号及依据，以及从原槽到定稿标签身份的有序 `(request_id, reason)` 标签沿革（`label_changes`）。 |

调用方可用 `facts.repository.load_current_fact(db, station, day, key)` 取得勘误旧像，用 `batches.repository.load_raw_slot(db, batch_id, position)` 查原槽。站日使用 `YYYY-MM-DD`。标识和依据为非空白文本，按原文比较。读数与上限是精确整数毫米，布尔值、浮点数及文本数值拒绝，`None` 表示缺测。上限和读数以十六进制整数文本无损存储，缺测存为空值。计算时恢复 Python 整数，支持超出 SQLite 整数范围的值及差值。

缺测占用原槽并单独计数。读数等于上限计未超限，严格大于上限才有异常行。异常行按超限量降序和观测键升序排列。已登记空日的试算为空且三类计数均为零。撤销移除现行事实并保留原件和首执，合法新批可接纳同一个键。定稿从一致事实像重新试算并校核，再把结果和首执一起提交。

标签勘误按申请号、理由和完整有序项判重，等同请求返回首次结果，改值、换标或撤销不影响首执。同号异请求、旧像失效和单外占位使用 `storage.Conflict`。标签申请号与原读数勘误号、报告号各自编号。

非法参数使用 `ValueError`，异绑定、撞键及旧像不符使用 `storage.Conflict`。未知站点、站日或清单使用 `storage.UnknownRecord`。固定查源行位越界使用 `IndexError`，结果校核或来源完整性失败使用 `storage.IntegrityViolation`。SQLite 失败保留原异常，跨组件写入一同回滚。

## 湾口站交班样例

1. 值班员凭 `P-17` 登记 2026-10-08 上限 10 毫米。
2. 早班接纳 C=15、M=缺测、A=12，A 保留原槽 2。
3. 晚班接纳 B=10、D=13，早班重投后仍只有五条现行事实。
4. 复核员把 A 更正为 15 并撤销 D，两份原批留存。
5. 质控员核得 A、C 各超限 5 毫米，覆盖计数为 2、1、1。
6. 日结员定稿 `R1`，同幅异常按键排为 A、C。
7. A 后续改为 9。夜班重开后，两个单行页仍读到 A=15、C=15。
8. 夜班从 `R1` 的行位 0 找到早班槽 2 的 A=12 和定稿采用的勘误依据。
