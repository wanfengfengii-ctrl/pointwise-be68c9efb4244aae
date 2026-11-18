# 显式缝合派生修订

README.md 是完整 V1 契约；其普通源、计划、成果、编码及异常行为继续成立。
本文件定义新增能力，stitched_cases.py 仅提供输入和手算期望，
stitched_checks.py 是可运行的少量联动检查，不实现缝合。

## 入口与固定加工

新增 SourceCatalog.build_stitched(source_revision, seams)，返回与 put_source 相同形状的
{'revision_id': int, 'fingerprint': str}。source_revision 必须是本库已有普通 V1 修订；
缺失抛 KeyError，派生修订作为该参数时抛 ValueError。本次只缝合普通源，不递归派生。
每次成功新增一个完整派生修订并推进 current_revision，包括空配对；
不为该入口引入请求号或重放协议，不改写原修订或原请求绑定。

返回的新 revision_id 可直接传给现有 PlanService.create_plan(request_id, revision_id, samples)，
再经 AnalysisService.execute、ResultReader.read_result 和 ResultExporter.export_result 完成加工。
这些入口的参数及返回外壳不变。计划固定派生修订及其摘要，成功完成后重放不重算。
V2 的计划 convention 为 stitched-surface-v1，分析 payload.kind 为 stitched-analysis-v1；
旧计划、普通源和 ordinary-analysis-v1 成果继续沿用原口径。

## 配对、拓扑及方向

seams 为 list 或 tuple，每项为 ((a,b),(c,d))；两侧也是两个字符串组成的 list 或 tuple。
(a,b) 与 (c,d) 分别标识两条不同的原边界边，次序表达 a 对 c、b 对 d；
原边身份仍是规范端点对，不是某个面的边发生次数。配对端点坐标须逐一精确相等，
每条原边最多参与一次。无自动发现、坐标焊接、吸附或容差。

只有指定配对的边合并；由此识别的顶点可相同，但未配对边仍各自独立，允许平行边。
原本共享的普通边仍共享。每个商顶点以所属原节点 ID 的字符串序最小者命名；
每条商边以所属原边规范端点对的字典序最小者作 edge_id，它不是商端点对。
每条边的规范方向为其商端点 ID 从小到大。顶点坐标取原精确值，原面 ID 保持不变。

每面仍须有三个不同顶点；每边关联一或两个面。每个顶点的面邻域须为单个圆盘或半圆盘，
孤立点、分离面扇和其他非流形邻域均拒绝。允许两面围成的两边环邻域，
不能因链路中有平行弧而拒绝。无需禁止空间自交或不同实体的几何重叠。
全部关联面须能一致定向，否则拒绝；连通分量按面通过共同边相连定义。
各分量源面 ID 最小者保持原绕序，其余面据此定向，使每条内部边的两个符号相反。
不对普通 V1 修订自动定向；这些新检查只发生在 build_stitched。

## 字段与角位置

商顶点上的全部原节点标量须完全相等。每条原规范边积分换到商边规范方向后，
同一商边的各值须完全相等；不作平均。未配对平行边可有不同积分。
面总量保持原值，即使面翻转也不取反。
原逐面环流、边界账及差额消费新边身份和符号，内部边项仍保留，系数为零。

samples 仍引用原面 ID，权重始终对应源面的原角顺序；返回 samples 的 weights
也保留该顺序。翻面只能改变定向，不得移动采样点、改变插值、顺序或重复次数。
FieldBundle.edge_value(start,end,*,edge_id=None) 接收商顶点 ID；普通 V1 的两参数行为不变。
V2 中省略 edge_id 时，唯一候选返回有向积分，无边抛 KeyError，
多条平行候选抛 ValueError；提供 edge_id 时按指定独立边取值，反向取负。
缺失边或端点不存在抛 KeyError，端点不属于该边或 start==end 抛 ValueError。
绑定通过原 bind_fields 和 RevisionRepository.read 得到，不另设旁路计算器。

## 可观察记录

read_source 的普通返回值完全不变。V2 返回 revision_id、fingerprint、mesh、fields、stitch；
mesh 在原 nodes、faces、edges、face_edges 上增加 edge_endpoints。
edges 为升序 edge_id 列表；edge_endpoints 为 edge_id 到规范商端点元组的字典；
face_edges 中的 edge 为 edge_id，sign 为相对规范方向的 +1 或 -1。
faces 为定向后商节点元组：正向保持源角顺序，翻面取 (v0,v2,v1)。
fields 沿用三个字典名称，键分别为商节点、独立 edge_id 和原面 ID，值仍是 Fraction。

stitch 完整包含以下字段：
- source_revision：原修订 ID；source_digest：原修订 fingerprint。
- source_input：原完整 {'mesh': 原普通网格记录, 'fields': 原字段记录}。
- seams：外层为 list，提交顺序不变，每项及其两侧转为 tuple。
- node_map：每个原节点到商节点的字典。
- edge_map：每个原规范边到 {'edge': edge_id, 'sign': +1或-1}；
  sign 表示原规范方向相对商边规范方向。
- face_map：每个原面到 {'sign': +1或-1, 'corners': 三元tuple}；
  corners[i] 是源第 i 个角在定向后 faces 中的位置。

派生内容摘要为 canonical_digest({'mesh': mesh, 'fields': fields, 'stitch': stitch})。
stitch.source_digest 须等于 canonical_digest(stitch.source_input)。
分析结果沿用原 binding、requests、samples、circulations、face_totals、balance 的含义，
input 为该派生内容，包括完整 stitch。结果 binding.source_digest 是派生摘要，
binding.revision_id 是派生修订，plan_digest 固定该输入及原顺序请求。
V2 其余 binding 成员与 V1 相同，摘要采用原 canonical_digest。
无需指定内部存储布局或新增表名；上述是公共返回及导出记录，不要求将其原样存成单块。

## 发布、历史与离库复核

源修订、配对、商关联、全部转换字段和来源映射共同发布；
非法配对、拓扑不合法或字段冲突抛 ValueError，数据库及故障钩子异常原样传播。
失败不留派生修订、不推进当前引用，也不改旧内容。
沿用 source_after_revision、source_after_head 阶段，故障钩子在相应写入后、提交前触发。
后续分析仍遵守已有 publication_after_result/publication_after_receipt 的整批提交语义。

输入、配对及返回容器修改，上游更正或关闭重开，均不得改变已有派生修订、计划或成果。
read_source/read_result/export_result 不重新求商、判定方向或转移字段；可解析已保存关联，
不可调用 build_stitched 来补算。旧 V1 数据库可直接打开，不回填或改写旧成果。
成果读取导出继续只访问已保存成果，不访问源/计划表；损坏明确报错，不回退重算。

check_export 扩展到 V2：仅用离库导出中保存的源输入、配对及映射，独立核对商身份、
邻域、方向、字段、源角采样和环流总账；不得调用生产缝合构建器或执行器，也不连接源库。
构建时可全量重建，复用并查集、图遍历、精确编码、原计算器和事务；
不要求增量、并发、进程恢复、自动网格修复或新的服务部署。

## 手算与交付验证

parallel 两个三角面仅配对 AB↔XY、BC↔YZ。商结构为 3 顶点、4 边、2 面。
AC 与 XZ 未配对且同端点，积分分别为 2 和 5。f1 保持，f2 翻面：
环流为 {'f1': Fraction(1), 'f2': Fraction(2)}，边界为 3，内部为 0，
面总量分别为 0、-3，差额总账为 6。
f2 源权重 (1/2,1/3,1/6) 的样本为 4/3，位置 (1/3,1/6,0)。
reversed 用相反规范方向的原边，配对积分为 1 和 -1，仍须得到相同结果。
cylinder 的六面带成功，商结构为 6 顶点、12 边；mobius 的端缝反转，
局部邻域合法但全局方向矛盾，须拒绝。pinched 在 A 有两个分离面扇，
即使 seams 为空且边度均合法也须拒绝。node_conflict、edge_conflict 分别为精确字段冲突。

从仓库根执行 python3.11 -B stitched_checks.py --fixtures，
只验证全部原始夹具可由 V1 接收；没有要求初始项目已经支持 V2。
实现后 python3.11 -B stitched_checks.py 实际贯穿构建、原计划执行和导出核账。
该检查只覆盖小例，不能替代完整公开契约验证。

保留全部原测试字节，继续运行 README.md 中的完整回归。
新增验证须覆盖无配对成功、非法/重复/非边界配对、拓扑拒绝、字段冲突、
非对称及重复采样、不同连通分量锚定、旧源与派生源共存、容器隔离、
发布失败重试、旧库及派生修订磁盘重开和离库核账。
演示须实际保存派生修订，分析平行边和翻面样本，更正上游、关闭重开后导出复核。
交付简短身份、方向及角位置不变量说明和独立小例核账。
沿现有 .pointwise-runtime.json 更新 verify/demo 以核验新旧链及上述真实演示；
继续使用 README.md 已说明的清单格式，检查失败须返回非零。
