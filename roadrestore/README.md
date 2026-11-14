# 道路中心线还原 `roadrestore`

把被切碎、顺逆方向混杂的道路中心线，按**道路号 + 里程**还原成连续线路。
仅依赖 Python 标准库。

## 输入约定

GeoJSON `FeatureCollection`，每个要素是一条 `LineString`，属性至少包含：

| 含义 | 支持的属性名 |
| --- | --- |
| 道路号 | `road` / `road_id` / `road_no` / `road_code` / `dlbm` |
| 起点里程 | `m_start` / `start_m` / `mileage_start` / `qszh` / `start_zh` |
| 终点里程 | `m_end` / `end_m` / `mileage_end` / `zzzh` / `end_zh` |
| 线段 id | `id` / `seg_id` / `segment_id`（缺省按输入序号生成） |

里程倒着写（`m_start > m_end`）的线段会在内存中翻转为里程递增方向，
原始几何不修改，翻转记录在 `RoadSegment.reversed_geom`。

## 接线规则

1. 按道路号分组，组内先把所有线段统一成里程递增方向；
2. 仅当两端点平面距离 `<= endpoint_tolerance`，且后段起点里程
   不小于前段终点里程时才接续；
3. 两端相近但里程重叠/回退的，**不硬接**，记入 `manual_review`
   （`mileage_conflict`）；
4. 一个端点存在两条及以上里程合法的接续时视为岔口/汇入口歧义，
   主链**只合并到岔口之前**，记入 `manual_review`（`ambiguous_fork`），
   不按距离远近猜测；
5. 线路编号、输出顺序只由道路号与里程排序决定，**与输入顺序无关**，
   重排输入后线路号和待核对项完全对得上。

输出三类结果：

- `routes`：能确定的连续段，含拼接后的坐标与里程范围；
- `gaps`：无法接续的里程缺口（线路之间，以及线路内部里程跳变）；
- `manual_review`：里程方向冲突、岔口/汇入口等需人工判断的位置。

## 编程调用

```python
import json
from roadrestore import restore_from_features, result_as_dict

with open("lines.geojson", encoding="utf-8") as f:
    features = json.load(f)["features"]

result = restore_from_features(features, endpoint_tolerance=0.5)
report = result_as_dict(result)          # 普通字典，可直接 json.dumps
```

需要还原后的 LineString 要素集合时用 `result_as_geojson(result)`，
缺口与人工项放在 FeatureCollection 的 `properties` 里。

## 命令行

```bash
# 三类结果的 JSON 报告
python -m roadrestore examples/demo_centerlines.geojson -t 0.5

# 还原后的线路要素集合
python -m roadrestore examples/demo_centerlines.geojson --format geojson -o out.geojson
```

参数：`-t/--tolerance` 端点距离容差（与坐标同单位，默认 0），
`-o/--output` 输出路径，输入传 `-` 时从标准输入读取。

## 示例数据

`examples/demo_centerlines.geojson` 故意打乱了输入顺序，并包含：
里程反向的线段、一个双出路岔口、一段里程缺口、两条不同道路。
运行后可看到 5 条确定线路、1 个缺口、1 条岔口人工项。
