# 振动试验台采集交班

电机装配厂的工艺员按规程录制电机振速，值班质检员把实测原据、换算结果和首执交班。接班质检员在设备关闭后仍可按原号核账，并根据班次最大振速安排复测。

## 运行

使用 Python 3.11 标准库，在项目根目录执行。控制台提供 `check_project.py`，执行完整正常回归。

```sh
python3 -B check_project.py
python3 -B -m review.readout demo
```

可直接运行同一套 unittest 用例。

```sh
python3 -B -m unittest discover -s tests
```

演示从 `samples/procedures.json` 装入规程，以 `samples/day.json` 重放一天的录制。程序在临时 SQLite 账库里模拟一次真实的签存写入失败。第 41 次交班失败时已经归还 A 槽，第 42 次仍占用 B 槽等待量程，第 43 次复用 A 槽。关源、重开后分别签出 `[20,40]` 和 `[300,500]`，班次结果为 `[41,43]`、`500 mm/s`。演示核对实测出处和下游班次结论后输出情景记录。

## Python 接口

连接应由调用方管理，使用标准 `sqlite3.connect`，进入业务接口前结束调用方自己的事务。每个账库同时开一个设备会话。活设备及其连接由开工线程操作。正常收工时调用 `close()` 或使用会话上下文管理器，随后可以关闭连接。关源后已接管原件、已签成果及首执仍可领取。设备关闭时尚未齐备的采集保留待齐登记，其未完成的活设备录制结束。

| 入口 | 输入和输出 |
| --- | --- |
| `recipes.catalog.ProcedureCatalog(db)` | `seed_defaults(declarations)` 补装缺失版本并返回新增数。`register(revision)` 登记独立版本，同键同内容可重投，异内容抛出 `ValueError`。`get((name, revision))` 找不到版本时抛出 `KeyError`。 |
| `bench.session.start(db, procedure_key, recording)` | 固定规程，返回 `BenchSession`。未知版本抛出 `KeyError`，无效样例或通道抛出 `ValueError`，已有活会话抛出 `BenchBusy`。 |
| `session.begin_capture()` | 返回含会话号、采集号、槽号和占用实例的 `SlotLease`。两槽都占用时抛出 `SlotsFull`，可在归还后重试。 |
| `session.writer.write_waveform(lease, values)` / `write_range(lease, multiplier)` | 分别模拟实际写毕并返回原区 `RegionReady`。区域可按任意顺序写入，重复写入只允许同值。 |
| `session.record_step()` | 执行一个剧本步骤。`begin` 返回槽凭据，写入返回 `None`，`deliver` 返回待投送通知。步骤用尽抛出 `StopIteration`。 |
| `capture.intake.deliver(session, feedback)` | 采录通知并返回 `CaptureStatus`。反馈必须对应原区实写，待齐时返回 `pending`，持久接管完成时返回 `held`。同证据可重投，冲突抛出 `ValueError`。 |
| `capture.resume.status(db, session_id, number)` | 返回 `unknown`、`pending` 或 `held`。已接管时包含完整原值、归还确认及冻结规程。 |
| `capture.resume.confirm_return(session, number)` | 以持久接管依据归还或补记对应的旧槽实例，成功返回 `HeldCapture`。 |
| `handover.publish.publish(db, number)` | 返回 `unknown`、`pending` 或带 `IssuedRecord` 的 `issued`。SQLite 写入或提交异常向调用方报告，之后可按原号重办。 |
| `review.readout.get(db, number)` | 返回已签档案及首执。无成果返回 `None`。`review.provenance.verify_provenance(record, number)` 可独立取得并核对冻结规程、原值和两区出处。 |
| `review.shift.shift(db, name)` | 返回指定原班次的有序已签号、峰值、原单位和胜出采集号。空班的峰值字段为 `None`，空白班次抛出 `ValueError`。 |

默认新账库的首个采集号为 41。接入预编号的录制时，可在首次开工前用 `bench.session.RunIndex(db, first_capture=起始编号)` 初始化序列。已初始化账库沿用持久序列。样例字段 `shift` 和 `channel` 为非空白文本，`steps` 为有序数组。每步通过 `label` 关联一次采集。`begin` 后可执行 `waveform` 或 `range`，`deliver` 引用同标签下已写入的 `waveform` 或 `range` 区域。可以重复投送已有通知。空步骤列表用于从 Python 接口手工触发录制。

## 数据和签存

- `procedure_revisions` 独立保存每个版本。开工同时把该版本完整声明固定在会话里。重复初始化只补缺失的版本。
- `capture_index` 在槽分配提交成功后公布采集号。分区通知包括会话、号、槽、槽占用实例和区域。由实际匹配的双区组成的完整件持久保存于 `held_captures`，归还按原实例确认。接管失败保留活槽和该会话已接纳的通知。归还确认写入失败时，活设备仍可确认原实例的归还，旧确认不影响新实例。
- `issued_results` 和 `first_receipts` 同事务提交。每个原波形样值乘以本次允许的正整数倍率。精确整数换算保留符号和原序，绝对峰值取换算后最大绝对值。例 `[-2,4] × 10` 得 `[-20,40]`，峰值 `40 mm/s`。空波形、非整数样值和无效倍率不能签存，全零非空波形峰值为 0。历史领取读取已提交的固定换算值。
- 班表仅由指定原班次的正式成果组成，按采集号升序。比较统一到 µm/s，倍率分别为 `1 mm/s = 1000 µm/s` 和 `1 µm/s = 1 µm/s`。例如 `2500 µm/s` 和 `3 mm/s` 同班时，后者胜出，班峰值显示 `3 mm/s`。物理峰值相同取采集号较小的固定成果。
