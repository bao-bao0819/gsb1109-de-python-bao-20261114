# OLE 目录项缺失清点报告

源文件: `/Users/guo/Documents/GSB/1109/batches/de-python-bao-100-20261114/control/questions/GSB1109-20261114-BAO-DE-PY-030/retry-2/workspace/reports/demo-damaged.doc`
目录流槽位总数: 8

## 分类汇总

- 根可达对象: 2 项
- 仅存于断开存储树: 4 项
- 多种可能归属: 2 项
- 空槽位: 0 项
- 无效槽位: 0 项

## 按目录项编号 (SID) 总表

| SID | 名称 | 类型 | 归类 | 父项 SID | 备注 |
| --- | --- | --- | --- | --- | --- |
| 0 | 'Root Entry' | root | 根可达对象 | - |  |
| 1 | '1Table' | stream | 仅存于断开存储树 | 6 |  |
| 2 | 'WordDocument' | stream | 仅存于断开存储树 | 6 |  |
| 3 | '\x05SummaryInformation' | stream | 仅存于断开存储树 | 6 |  |
| 4 | '\x05DocumentSummaryInformation' | stream | 根可达对象 | 0 |  |
| 5 | '\x01CompObj' | stream | 多种可能归属 | 0,6 |  |
| 6 | 'OrphanStore' | storage | 仅存于断开存储树 | - | 目录区存在但无任何存储引用它 |
| 7 | 'LoopStore' | storage | 多种可能归属 | - | 卷入环或同一存储内的重复引用 |

## 一、能从根走到的对象

- SID0 'Root Entry' (类型: root, 父项: -)
  - 证据路径: SID0
- SID4 '\x05DocumentSummaryInformation' (类型: stream, 父项: 0)
  - 证据路径: SID0 -> SID4

## 二、只在断开存储树里出现的对象

- SID1 '1Table' (类型: stream, 父项: 6)
  - 证据路径: SID6 -> SID1
- SID2 'WordDocument' (类型: stream, 父项: 6)
  - 证据路径: SID6 -> SID2
- SID3 '\x05SummaryInformation' (类型: stream, 父项: 6)
  - 证据路径: SID6 -> SID3
- SID6 'OrphanStore' (类型: storage, 父项: -)
  - 说明: 目录区存在但无任何存储引用它

## 三、有多种可能归属的对象

- SID5 '\x01CompObj' (类型: stream, 父项: 0,6)
  - 证据路径: SID0 -> SID5
  - 证据路径: SID6 -> SID5
- SID7 'LoopStore' (类型: storage, 父项: -)
  - 证据路径: SID7 -> SID7
  - 说明: 卷入环或同一存储内的重复引用

## 断开的存储树

- 树头 SID6, 成员 SID: 1, 2, 3, 6
- 树头 SID7, 成员 SID: 7

## 绕回自身 / 重复引用的有限证据路径

- 归属存储 SID7, 回到 SID7: SID7 -> SID7

## 重名分组 (重名不等于同一对象)

(无)

## 指针异常与空 / 无效槽位

(无)
