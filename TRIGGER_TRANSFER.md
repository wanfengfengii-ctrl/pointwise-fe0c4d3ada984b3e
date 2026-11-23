# 触发前换槽续录

试验台有 A、B 两个可复用槽。换槽用于同一个活会话内已录前段、尚未触发的采集。一次采集最多成功换槽一次，也可以留在原槽触发。采集号、班次和规程沿用最初登记。普通录制和关源行为沿用 README。

## 设备动作和采录

在 `bench.session` 提供以下函数。调用方沿用现有连接和会话约定。

| 入口 | 可观察结果 |
| --- | --- |
| `prepare(session, prefix, multiplier)` | 按普通分槽规则分配一个新采集号并实写前段，返回 `(number, carrier)`。`carrier` 是原格式的 `SlotLease`。`prefix` 接受列表或元组，必须含至少一个真正的整数。倍率必须属于冻结规程。 |
| `move(session, number, expected, target, *, before_commit=None)` | `expected` 是当前槽凭据，`target` 是另一槽的名称。成功返回同会话同采集号的新 `SlotLease`。新槽成为唯一可触发实例，原槽可立即用于普通采集。原前段的正文及首次实写实例必须先获得保管。 |
| `trigger(session, number, carrier, suffix)` | 按当前凭据实际写一次后段，返回 `RegionReady`。`suffix` 采用与 `prefix` 相同的输入约定。反馈的 `source` 为 `RegionSource(carrier, 'waveform')`，`value` 是实写后段的整数元组。函数返回时反馈仍待投送。 |
| `capture.intake.deliver(session, feedback)` | 仍用原入口投送触发后的 `RegionReady`。仅初始前段与有效触发实例实写的后段可以闭合本号。完整件持久接管后归还实际触发的实例。同件反馈重投沿用已接管原件和确认，不影响任何后继占用。 |

目标忙时抛出 `SlotsFull`，原槽不变。`before_commit` 是可选的无参回调，在交接提交边界、成功生效之前调用。回调抛出的异常传给调用方。该中断与交接提交失败均保留原前段和原凭据的触发权，目标槽不能用于触发该采集。凭据错绑、过时、触发后换槽、第二次成功换槽请求、重复触发，以及非法输入或冲突反馈均抛出 `ValueError`，既有事实仍有效。普通写入入口拒绝改写这种待触发采集的活槽，普通反馈不能绕过触发和实写核验。原始出处按会话、采集号、槽名及占用实例核对，后继件的等值原码不具有原件资格。

## 签存与领取

继续使用 `handover.publish.publish(db, number)` 和 `review.readout.get(db, number)`。完整件实收前，`publish` 返回 `pending`，`get` 返回 `None`。实收后按前段、后段顺序拼接原码，用冻结倍率换算，保留原峰值和班表口径。沿用原首执事务。提交失败无新增成果或首执，已实收原件可按原号续签，成功重投复领。关闭设备会话并重开账库后仍可领取固定件。

这类采集的 `IssuedRecord.result.evidence` 提供 `trigger_segments`，值为 `((前段原码元组, 前段实际写入的 SlotLease), (后段原码元组, 后段实际写入的 SlotLease))`。签存和领取采用同一双段证据，`waveform` 为拼接后的原码元组，`multiplier` 为冻结倍率。领取时核对本号两段实写出处。普通采集的原出处字段和历史领取继续有效。

## 样例和核验

夹具 `transfer_fixture.py` 使用原有 motor-vibration 第 1 版规程和 day 班。第 41 次在 A 的首次占用录 `[2, 4]`，倍率 10，换至 B 后 A 立即用于第 42 次，恰好也写出 `[2, 4]`。第 41 次拒绝旧 A 凭据及改绑的同值反馈，只接受 B 实际触发写出的 `[-9]`，签出 `[20, 40, -90]` 和 `90 mm/s`。关源重开可领原首执和两段各自实例。另一次独立录制在目标忙及交接中断后仍从原槽实际触发，两段均来自原实例。

当前可运行 `python3 -B transfer_fixture.py baseline`，用同一输入验证普通 A、B 录制分别发号、分别签出 `[20, 40]` 和 `[-90]`。新增能力接通后，`python3 -B transfer_fixture.py verify` 先跑原回归，再核对换槽与失败续办，失败时非零退出。`python3 -B transfer_fixture.py demo` 打印停源重开读取的两段出处、换算值、峰值和首执。用现有 `.pointwise-runtime.json` 的 `verify` 和 `demo` 参数数组接入这两项。清单沿用 `version` 1、`kind` 为 `cli` 和命令参数数组列表 `setup`。
