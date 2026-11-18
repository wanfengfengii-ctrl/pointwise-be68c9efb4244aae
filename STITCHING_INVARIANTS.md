# 显式缝合交付：身份、方向与角位置不变量

本文件是 STITCHING.md 的实现说明与核账记录，不改变 README.md 的 V1 契约。

## 加工链

`SourceCatalog.build_stitched(source_revision, seams)` 只接受已保存的普通 V1 修订；
配对校验、商结构、邻域与定向全部在内存中完成后，才在一个事务里写入完整派生修订
（mesh + fields + stitch），并推进 current_revision。没有任何请求号/重放协议，
每次成功都追加一个新修订，空配对也算一次成功。派生修订可直接交给
`PlanService.create_plan`、`AnalysisService.execute`、`ResultReader.read_result`、
`ResultExporter.export_result`；这些入口的参数与返回外壳不变。

## 商身份（最小源 ID 命名）

- 顶点：并查集按 seam 的 `a↔c`、`b↔d` 合并原节点，商名取组内原节点 ID 字符串序最小者。
- 边：并查集合并 seam 两侧的原规范边，商 edge_id 取组内原规范端点对字典序最小者；
  edge_id 是原边身份，不是商端点对。
- 每条商边的规范方向是两个商端点 ID 从小到大。`edge_endpoints` 记录该方向；
  `face_edges` 的 sign 是面绕序相对它的 +1/-1。
- 未配对平行边不合并，保留独立身份：parallel 例中 `("A","C")` 与 `("X","Z")` 同
  端点但各自独立，积分分别为 2 和 5。

## 方向（分量锚定，面总量不取反）

- 顶点邻域用 link 多重图判定：每个顶点以“该顶点处的商边”为 link 顶点、每个入射面
  贡献一条 link 弧；要求 link 连通（单扇），且每点度为 1 或 2、恰有 0 或 2 个度为 1 的点
  ——即单个圆盘（环）或半圆盘（路径）。孤点、分离面扇、分支邻域均拒绝；两面围成的
  两边环是合法 2-cycle，不会因平行弧而误判。
- 定向：以面共享边为邻接构图，每个连通分量取面 ID 最小者保持原绕序，其余按
  “内部边两侧符号相反”传播。矛盾即拒绝（möbius）。
- 面翻转只改 `faces` 为 `(v0,v2,v1)`；`face_totals` 保持原值，不因翻转取反。
  各连通分量独立锚定（`test_component_anchors_are_independent`）。
- 逐面环流、边界账与内部账直接复用 V1 的 `circulation` / `balances`：内部边项保留，
  系数可为 0；parallel 例内部项 coefficient=0、value=0。

## 角位置（采样仍按源角序）

- `samples` 与 `requests` 仍引用原面 ID，weights 对应**源面原角顺序**。
- `StitchedMesh.sample_vertices(face)` 返回按源角序排列的商顶点；`sampling.py` 与
  `barycentric.py` 通过该访问器取点，因此翻面不会移动采样点、改变插值、顺序或重复次数。
- parallel 例 f2 源权重 (1/2,1/3,1/6) → 值 4/3、位置 (1/3,1/6,0)，不因 f2 翻面而变化。

## 字段与显式边查询

- 商顶点上所有原节点标量必须**精确相等**；商边上各原积分换到商规范方向后必须
  **精确相等**，不平均。冲突（node_conflict / edge_conflict）抛 ValueError，且不落库。
- `FieldBundle.edge_value(start, end, *, edge_id=None)`：
  - 普通 V1 两参数行为不变；给 edge_id 抛 ValueError。
  - V2 省略 edge_id：唯一候选返回有向积分；无边抛 KeyError；多平行候选抛 ValueError。
  - 提供 edge_id：按该独立边取值，反向取负；缺失边/端点不存在抛 KeyError；
    端点不属于该边或 start==end 抛 ValueError。
- 绑定仍由原 `bind_fields` / `RevisionRepository.read` 得到，无旁路计算器。

## 读取、导出与离库复核（不重做缝合）

- `read_source`（V2）返回 `mesh/fields/stitch`；`stitch` 含 source_revision、
  source_digest、source_input、seams、node_map、edge_map、face_map。
- `read_source` / `read_result` 只做**已保存关联的结构解析**：映射完整性、坐标一致、
  字段一致、面绕序一致、seam 两侧共享商边；不重新求商、不重新定向、不调用
  `build_stitched`。
- 成果 `binding.source_digest` 是派生内容摘要（含 stitch），`plan_digest` 固定派生输入；
  payload.kind=`stitched-analysis-v1`，convention=`stitched-surface-v1`。
- `check_export` 的 V2 分支只使用导出里的 `source_input`、`seams`、`node_map`、
  `edge_map`、`face_map` 独立重建商身份、link 邻域、定向、字段、源角采样与总账；
  不调用生产缝合构建器或执行器，不连接源库。
- 失败（非法配对/拓扑/字段冲突/故障钩子）整批回滚，不留派生修订、不推进 current，
  不改旧内容；成功后重开与更正上游均不改变已有派生修订、计划或成果。

## 独立小例核账（parallel）

源：f1=(A,B,C)、f2=(X,Y,Z)，坐标 A=X、B=Y、C=Z；配对 AB↔XY、BC↔YZ。

- 商顶点 {A,B,C}；面 {f1:(A,B,C), f2:(A,C,B)}（f2 翻面）。
- 商边 {AB, BC, AC, XZ}：AB/BC 为内部边，AC/XZ 为两条独立平行边界边。
- 积分：AB=1、BC=2、AC=2、XZ=5；面总量 f1=0、f2=-3。
- 环流 f1=1+2-2=1；f2=(翻面后)=2。
- 边界 = (-2)+(5)=3；内部 = 0+0=0；差额总账 = (1+2)-(-3)=6。
- f2 源权重 (1/2,1/3,1/6) → 值 4/3、位置 (1/3,1/6,0)。

reversed 用相反规范方向的原边（XY→YX、YZ→XZ 侧），配对积分按方向取负后仍相等，
结果与 parallel 完全一致（见 `tests/test_stitching.py` 与 `stitched_checks.py`）。

## 运行

```sh
python3.11 -B verify_all.py          # 原 9 项回归 + 新缝合回归 + stitched_checks 联动
python3.11 -B -m surface.results.stitched_demo   # 新旧链真实演示 + 离库复核
```

`.pointwise-runtime.json` 的 verify 指向 `verify_all.py`，demo 指向缝合演示；
`verify` 失败返回非零。
