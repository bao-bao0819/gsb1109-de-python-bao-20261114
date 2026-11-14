# OLE 目录项缺失清点报告

源文件: `tests/images/test-ole-file.doc`
目录流槽位总数: 8

## 分类汇总

- 根可达对象: 6 项
- 仅存于断开存储树: 0 项
- 多种可能归属: 0 项
- 空槽位: 2 项
- 无效槽位: 0 项

## 按目录项编号 (SID) 总表

| SID | 名称 | 类型 | 归类 | 父项 SID | 备注 |
| --- | --- | --- | --- | --- | --- |
| 0 | 'Root Entry' | root | 根可达对象 | - |  |
| 1 | '1Table' | stream | 根可达对象 | 0 |  |
| 2 | 'WordDocument' | stream | 根可达对象 | 0 |  |
| 3 | '\x05SummaryInformation' | stream | 根可达对象 | 0 |  |
| 4 | '\x05DocumentSummaryInformation' | stream | 根可达对象 | 0 |  |
| 5 | '\x01CompObj' | stream | 根可达对象 | 0 |  |
| 6 | '' | empty | 空槽位 | - |  |
| 7 | '' | empty | 空槽位 | - |  |

## 一、能从根走到的对象

- SID0 'Root Entry' (类型: root, 父项: -)
  - 证据路径: SID0
- SID1 '1Table' (类型: stream, 父项: 0)
  - 证据路径: SID0 -> SID1
- SID2 'WordDocument' (类型: stream, 父项: 0)
  - 证据路径: SID0 -> SID2
- SID3 '\x05SummaryInformation' (类型: stream, 父项: 0)
  - 证据路径: SID0 -> SID3
- SID4 '\x05DocumentSummaryInformation' (类型: stream, 父项: 0)
  - 证据路径: SID0 -> SID4
- SID5 '\x01CompObj' (类型: stream, 父项: 0)
  - 证据路径: SID0 -> SID5

## 二、只在断开存储树里出现的对象

(无)

## 三、有多种可能归属的对象

(无)

## 断开的存储树

(无)

## 绕回自身 / 重复引用的有限证据路径

(无)

## 重名分组 (重名不等于同一对象)

(无)

## 指针异常与空 / 无效槽位

- 空槽位: SID6, SID7
