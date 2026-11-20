# 站日标签勘误

值班员按 `README.md` 登记站日口径、接收原批并定稿。观测键抄反时，复核员提交一张标签勘误单。原件继续保留实际收到的标签和读数，后发清单采用核定后的标签，夜班凭固定清单行位复核。

## 申请与首执

新增 `facts.corrections.relabel_facts(db, request_id, reason, changes)`。调用方在事务外调用，按现有文本校验规则提供申请号、理由和目标键。`changes` 是非空有序序列，各项为 `(old, new_key)`。`old` 用 `OldImage.from_fact(load_current_fact(db, station, day, key))` 取得。整单限同一站日，所有源键各异，每项须改为不同的键，所有目标键各异。目标键可以是整单另一项的源键，也可以空闲，仍在账且未参加本单的键不可覆盖。完整旧像和占位均以同一提交前像核对。

成功首执有 `request_id`、`reason`、`after` 三个字段。`after` 是按申请项顺序排列的 `CurrentFact` 元组，其中的键已经核定，站日、读数和原批槽与旧像一致。事实身份、标签依据和首执同次提交。重投按申请号、理由和完整有序项判定，等同请求返回首次结果，之后的改值、换标或撤销不影响首执。标签申请号在库内唯一，和原读数勘误号、报告号各自编号。

空单、混用站日、重复源键或目标键、原键等于目标键及不合规字段使用 `ValueError`。旧像失效、单外占位或同号异请求使用 `storage.Conflict`。SQLite 提交失败保留原异常。任何失败都不得留下部分事实、标签依据或新首执。

每次换标均产生新的标签身份。对新申请，`relabel_facts` 和 `correct_fact` 都核对旧像携带的身份。旧键、旧值和原槽都吻合时，过时的标签身份仍须拒绝。普通换值沿用当前标签依据，并保留获准时实际采用的标签身份。换标沿用当时的读数依据。撤销后，新批同键使用新原槽起算。原批重投和普通勘误首执继续按原规则领取。旧像仍可用原构造形式表示从未换标的事实。

## 定稿与查源

继续用 `delivery.issue.publish(db, station, day, report_id)` 定稿，用 `delivery.pages.read_page(db, report_id, start, length)` 按零基绝对行位领页。覆盖计数、超限量以及分页约定沿用 V1，同幅时按定稿采用的核定键升序。定稿冻结该次实际采用的身份。既有报告号重领同一成果。

`delivery.source_lookup.read_source(db, report_id, position)` 保留 `SourceEvidence` 的原字段，新增 `label_changes`，其值为从原槽到定稿实际采用的标签身份之间的有序 `(request_id, reason)` 元组。无标签勘误时返回空元组。`original` 返回实际原槽及原标签，批号通过 `row.source.batch_id` 获取。`correction_id` 和 `correction_reason` 仍表示定稿采用的读数勘误，无勘误时均为空。

查源要核对该冻结行的站日、原批槽、核定键、读数和各自采用的依据。读数勘误的标签上下文按该次读数勘误获准时的身份核对，即使后续换标后仍采用这次读数勘误。原槽、标签依据或读数依据缺失、错绑原槽、错绑标签经历时，使用 `storage.IntegrityViolation`。查源依据冻结事实，后续有效键和新成果不改变其解释。通过现有打开、初始化入口继续使用已有库及其中的旧报告和首执。

## 手算交班

`examples/label_review.json` 给出一份独立交班的原始输入。用 `register` 和 `ingest` 登记上限 10 与早班原件，先签 R0。`pairs` 中每对是值班员填写的源键和目标键，实际申请使用对应时点的完整旧像。按 `swap` 互换后签 R1，按 `reading_fix` 普通换值后签 R2。再按 `move_out`、`move_back` 分别换标并签 R3、R4。最后撤销现行 B，按 `reintake` 新批接纳并签 R5。以下是各清单行位 0 的预期。

| 清单 | 定稿键 | 读数 | 原批/原槽/原标签 | 标签勘误编号，按序 | 读数勘误 |
| --- | --- | ---: | --- | --- | --- |
| R0 | A | 19 | EARLY/0/A | 空 | 无 |
| R1 | B | 19 | EARLY/0/A | TAG-SWAP | 无 |
| R2 | B | 21 | EARLY/0/A | TAG-SWAP | VALUE-1 |
| R3 | C | 21 | EARLY/0/A | TAG-SWAP、TAG-OUT | VALUE-1 |
| R4 | B | 21 | EARLY/0/A | TAG-SWAP、TAG-OUT、TAG-BACK | VALUE-1 |
| R5 | B | 22 | NEXT/0/B | 空 | 无 |

R1 的行位 1 为 A=14，来自 EARLY 原槽 1 的原标签 B，亦采用 TAG-SWAP。D=10 始终计为未超限。可以在 R2 后提交 `occupied` 检查单外 D 的占位。拿 R2 时的 B 旧像在 R4 后发起新勘误会被拒绝，重投原 VALUE-1 请求则可领取原首执。检查一份清单的证据被移除或错绑到另一已提交事实时，查源应拒绝。

保留 `python3 -B check_project.py` 的原回归和 `python3 -B demo.py` 的原演示。沿用项目的 `.pointwise-runtime.json`，将新链路核验接入 `verify`，结果不符时以非零状态结束。回滚可复用 `tests.support.deny_commit`，对照失败前后的事实和首执，再用原申请号重试。将交班演示接入 `demo`，展示重开后 R2 和 R4 的相同定稿键、读数及不同标签经历。
