# 卫津路校区静态地图最终覆盖审计（P3f）

## Summary（最终 authoritative reconciliation）

本节以 `data/weijinlu_map.json`、加载器、resolver、route provider 与 deterministic master audit 为准；下方保留的批次采集记录用于 provenance，不单独定义最终状态。

| 范围 | Included | DONE | BLOCKED | PARTIAL |
|---|---:|---:|---:|---:|
| 宿舍 / 学生公寓 | 37 | 37 | 0 | 0 |
| 编号教学楼 | 25 | 25 | 0 | 0 |
| 食堂 | 4 | 4 | 0 | 0 |
| 图书馆 / 教学辅助 | 6 | 6 | 0 | 0 |
| 学术 / 科研 / 工程建筑 | 13 | 13 | 0 | 0 |
| 公共 / 校园服务建筑 | 13 | 12 | 1（配楼） | 0 |
| 体育场 | 1 | 1 | 0 | 0 |
| landscape / 湖泊 | 7 | 7 | 0 | 0 |
| 校门 | 5 | 5 | 0 | 0 |
| 官方道路 linear features | 14 | 14 | 0 | 0 |
| 卫津河 water boundary | 1 | 1 | 0 | 0 |
| **合计** | **126** | **125** | **1** | **0** |

### Scope accounting

- `EXCLUDED / DEFERRED BY PRODUCT SCOPE`：网球场、篮球场、排球场、篮球馆、游泳馆、体育馆（6 项；不是 BLOCKED，也不进入正式 resolver POI）。
- `EXCLUDED BY USER SCOPE`：海棠、张太雷像、百年校庆纪念亭、牛顿苹果树、梦成真石（5 项；不计入 included inventory）。
- 唯一事实 BLOCKED：配楼。官方 inventory 身份成立，但名称泛化且当前证据不能唯一确定官方图所指物理对象；无正式 node、resolver 或猜测性路线。
- 道路与卫津河为 linear / boundary feature：不作为普通 point resolver 目的地；其 DONE 由代表性路段或边界语义、provenance 与 topology audit 确定。

### 最终语义回归

- 出版社 / 期刊中心：不同 resolver entity，共享 `weijinlu_building_19_east_annex` physical anchor。
- 体育部 / 体育场：不同 resolver entity，共享 `weijinlu_stadium_sports_area` physical anchor；体育场仅表示官方导览图的 representative 体育场。
- 北洋园：仅为卫津路内部 landscape，不等于北洋园校区。
- 求是亭：使用敬业湖北岸可达 viewing/access anchor，不创建穿湖道路。
- 最终 all-pairs walk/bike 审计曾发现北部 28–30 斋的 bike graph 缺少已存在鞍山西道路段的 mode facts；已以有限 AMap bicycling v4 验证补齐 34/43/44/33/28/29/30 至北部 junction 的 local bike access，以及 28–29、29–30 的相邻 bike facts。最终 110 个 point-like DONE entity 均 walk/bike 连通，异常阈值（ratio ≥ 2.5 或差值 ≥ 600m）为 0。

> 教学楼状态更正：第一教学楼与第十三教学楼现满足 deterministic DONE 条件（identity、alias、formal node、precision、provenance、POI access、local walk/bike access，以及到第九教学楼的 walk/bike shared-graph 连通性）。本节中的旧 PARTIAL 表述由此说明取代。

> 核心教学区 8 栋收口：第二、第三、第四、第六、第七、第十二、第十五、第二十教学楼均已满足同一 deterministic DONE 条件。其状态由 `test_core_teaching_batch_eight_buildings_meet_deterministic_done_criterion` 根据正式地图事实计算，不依赖人工保留的 PARTIAL 文案。

> 西部教学区 5 栋收口：第十一、第十六、第十八、第二十一、第二十三教学楼均已满足 deterministic DONE 条件。求是路—铭德道 shared segment 的 walk/bike 均为 228m；五栋均有本地 walk/bike access，并由 `test_west_teaching_batch_five_buildings_meet_deterministic_done_criterion` 自动回归。

> 东部／东南教学区 7 栋收口：第五、第八、第十、第十四、第十七、第二十四、第三十五教学楼均已满足 deterministic DONE 条件。补齐敬业道 east、太雷路 south 与东南 junction 的 shared bike facts，以及七栋的 local walk/bike access；由 `test_east_southeast_teaching_batch_seven_meet_deterministic_done_criterion` 自动回归。

> 南部最终批：第十九教学楼以 precise anchor 接入敬业道—花堤路 junction（walk/bike 247m）；第二十六教学楼以官方 A/B/C/D/E 建筑群的 representative approximate anchor 接入同一 shared junction（walk/bike 290m）。两者都不经过彼此的 POI access 中继；`test_final_south_batch_and_all_twenty_five_teaching_buildings_are_done` 覆盖其 resolver、walk/bike 路由和建筑群 alias 语义。

## 食堂、图书馆与教学辅助建筑（10 项收口）

| 地点 | 类别 | 状态 | precision | aliases | access / provenance |
|---|---|---|---|---|---|
| 学一食堂 | 食堂 | DONE | precise | 学一 | 北部宿舍区；接入求是路—铭德道，walk/bike 412m，AMap POI + local routes |
| 学三食堂 | 食堂 | DONE | precise | 学三 | 东南宿舍区；接入太雷路—敬业道 junction，176m |
| 学四食堂 | 食堂 | DONE | precise | 学四 | 东南宿舍区；接入太雷路 southeast，87m |
| 学五食堂 | 食堂 | DONE | precise | 学五 | 三村门／金晖路片区；接入铭德道 west，176m |
| 图书馆 | 图书馆 | DONE | approximate | — | 核心教学区；接入敬业道—花堤路 junction，146m |
| 科学图书馆 | 图书馆 | DONE | approximate | — | 敬业湖以南；接入敬业道—花堤路 junction，327m |
| 春水图书馆 | 图书馆 | DONE | precise | 北馆；春水馆 | 既有正式节点；保持至9教150m |
| 阶梯教室 | 教学辅助 | DONE | approximate | — | 接入花堤路 north，135m |
| 东阶梯教室 | 教学辅助 | DONE | approximate | — | 接入花堤路—益智道 junction，62m |
| 西阶梯教室 | 教学辅助 | DONE | approximate | — | 接入敬业道—花堤路 junction，151m |

“图书馆”是独立 canonical node，未作为春水图书馆 alias；所有 access 的 walk/bike 距离均由对应开发期路线事实记录，运行时只使用本地静态 JSON。

西部 bike sanity 修复：鹏翔学生公寓至铭德道西段的本地接入已补入 AMap bicycling v4 的 97m 事实（步行 v3 为 96m），因此鹏翔学生公寓→学五食堂使用共享 `weijinlu_road_mingde_west`，静态 bike 距离为 273m；未建立鹏翔→学五食堂专用直连边。同时补入一斋（365m/367m）和三斋（526m/526m）至同一 shared road node 的开发期 walk/bike 本地接入，消除西部旧 access 链造成的 kilometre-scale 绕行。

## 学术 / 研究机构（第一批 7 项收口）

| 地点 | aliases | 状态 | precision | identity / anchor evidence | access / provenance |
|---|---|---|---|---|---|
| 远教学院 | 远程与继续教育学院 | DONE | precise | 官方 inventory；AMap 卫津路校区学院 POI | 246m 接入北部 shared road point（另保留已核验的铭德道 west 路线）；`amap_poi_verified` + local route |
| 国教学院 | 国际教育学院 | DONE | precise | 官方 inventory、青年湖东侧关系；AMap 国际教育学院 POI | 接入花堤路北段，70m；`amap_poi_verified` + local route |
| 应数中心 | 应用数学中心 | DONE | approximate | 官方图：青年湖北侧、25/26斋附近；避开与该关系冲突的南部 AMap 同名候选 | 114m 接入青年湖西侧 corridor，并经湖西南 junction 进入核心区 |
| 信息与网络中心 | — | DONE | precise | 官方 inventory、青年湖东侧关系；AMap 卫津路 POI | 接入花堤路北段，32m；`amap_poi_verified` + local route |
| 冯骥才文学艺术研究院 | — | DONE | precise | 官方 inventory、花堤路东侧关系；AMap 卫津路 POI | 接入花堤路北段，113m；`amap_poi_verified` + local route |
| 王学仲艺术研究所 | — | DONE | precise | 官方 inventory、6/7教及图书馆周边关系；AMap 卫津路 POI | 接入求是路—铭德道 junction，69m；`amap_poi_verified` + local route |
| 战略院 | — | DONE | approximate | 官方 inventory、3教/图书馆/西阶梯教室附近关系；以真实道路短接入约束 representative anchor | 接入敬业道—花堤路 junction，143m walk/bike；`tju_official_map_approximate` + local route |

以上机构均保持独立 canonical POI；机构自身的 access 直接进入 shared road skeleton，不把其他机构 POI 或 access 当作主干。北部应数中心通过 AMap 真实道路 steps 接入青年湖西侧 corridor，因此没有生成跨青年湖的直连边。

## 学术 / 科研 / 工程建筑（第二批 6 项收口）

| 地点 | aliases | 状态 | precision | identity / anchor evidence | access / provenance |
|---|---|---|---|---|---|
| 北洋科学楼 | — | DONE | approximate | 官方图：敬业湖以南、科学图书馆及19/26教附近；代表 anchor 由南部真实道路 steps 约束 | 78m 接入南部科研区共享路点，后接敬业道—花堤路 junction；`tju_official_map_approximate` + AMap route facts |
| 医学部教学楼 | — | DONE | precise | 官方 inventory、东部教学区关系；AMap 医学部 POI | 286m 接入太雷路南段；`amap_poi_verified` + local route |
| 综合实验楼 | — | DONE | approximate | 官方东部教学区关系（医学部、1/5/17教、旭东/太雷路）；代表 anchor 由真实 east route steps 约束 | 47m 接入东部实验区共享路点，再接太雷路南段 181m |
| 内燃机大楼 | — | DONE | precise | 官方 inventory；AMap 卫津路内燃机实验室候选仅作坐标 anchor evidence，canonical 保持官方楼名 | 304m/309m 接入太雷路南段；`amap_poi_verified` + local route |
| 电工队 | — | DONE | precise | 官方 inventory、12/13/20教及阶梯教室附近关系；AMap 电工队 POI | 135m 接入花堤路—益智道 junction；`amap_poi_verified` + local route |
| 3.5KV电站 | — | DONE | approximate | 官方地图 canonical 原样保留；电工队、12教、阶梯教室、15/20教 control anchors | 97m 接入花堤路—益智道 junction；`tju_official_map_approximate` + local route |

北洋科学楼至南部共享路点、综合实验楼至东部共享路点均使用独立 POI access；医学部教学楼与综合实验楼、电工队与 3.5KV 电站不会互相经对方 access 作为主干。北洋科学楼的图路径沿南部共享道路至敬业道—花堤路 junction，没有跨敬业湖的直连边。

## 公共 / 校园服务建筑（第一批 7 项收口）

| 地点 | aliases | 状态 | precision | identity / anchor evidence | access / provenance |
|---|---|---|---|---|---|
| 大学生活动中心 | 学生活动中心 | DONE | precise | 官方 inventory、青年湖西南关系；AMap 卫津路 POI | 62m 接入青年湖西侧 shared road；`amap_poi_verified` + local route |
| 工会 | — | DONE | precise | 官方 inventory、西部教学/湖区关系；AMap 卫津路工会 POI | 114m 接入花堤路北段；`amap_poi_verified` + local route |
| 员工之家 | — | DONE | approximate | 官方东部、花堤路及冯骥才研究院周边关系；附近 AMap staff/family-area POI 仅作 control anchor | 353m/384m 接入花堤路北段；`tju_official_map_approximate` + local route |
| 校友之家 | — | DONE | approximate | 官方西部偏南、爱晚湖/友谊湖及18/21/23教附近关系；AMap 路线 steps 约束 anchor | 39m 接入西部服务区共享路点，再接求是路—铭德道 junction |
| 留园 | — | DONE | approximate | 官方西部偏南、校友之家附近关系；独立 AMap 本地路线 anchor | 50m 接入求是路—铭德道 junction |
| 天南楼 | — | DONE | approximate | 官方东南、35教和学三/学四食堂及宿舍区附近关系；AMap steps 约束 anchor | 143m 接入太雷路—敬业道 junction |
| 校医院 | — | DONE | precise | 官方 inventory；AMap 卫津路校医院 POI | 301m/312m 接入北部 shared road point（另保留求是路—铭德道 route）；`amap_poi_verified` + local route |

校友之家与留园分别具有 own access，并经西部共享路点／求是路—铭德道 junction 进入主图；员工之家、天南楼与大学生活动中心均未建立到9教的专用长边。青年湖、爱晚湖、友谊湖仅作为 topology obstacle 约束，未建立跨湖边。

### 青年湖西侧 → 核心教学区 shared corridor 修复

AMap walking v3 与 bicycling v4 对大学生活动中心→花堤路北段均返回 438m，并给出相同的三段 route steps：62m（活动中心接入）→67m（青年湖西侧南行段）→309m（青年湖西南至核心教学区）。正式地图据此新增 `weijinlu_road_youth_lake_west` 与 `weijinlu_junction_youth_lake_southwest`，并连接至既有 `weijinlu_road_huadi_north`。

同一 corridor 已由 31斋（202m）和应数中心（114m）接入。大学生活动中心→第二十教学楼的静态路径从原先经9教 access 的 1760m/1765m 改为 517m/517m；路径只经过 own access、青年湖西侧 shared road、青年湖西南 junction、花堤路北段与20教 access，不穿青年湖，也不经过9教专属 access。

## 编号教学楼 inventory correction

根据用户复核的天津大学卫津路官方导览地图，本轮教学楼 inventory 已更正：移除误列的“第二十八教学楼”，补入此前漏列的“第十三教学楼”。目标总数保持 25；本地正式地图新增 `weijinlu_building_13`，并未创建第二十八教学楼节点。

| 地点 | 状态 | precision | 位置 / access 事实 | provenance |
|---|---|---|---|---|
| 第一教学楼 | PARTIAL | approximate | 东部教学区、旭东路西侧、医学部教学楼南侧／第十七教学楼北侧；至太雷路 south shared node 的 local walk/bike access 均为 123m | 官方地图关系 + AMap walking/bicycling access |
| 第十三教学楼 | PARTIAL | approximate | 青年湖南侧，电工队东侧、花堤路西侧、第二教学楼北侧；经益智道／花堤路接入 Huadi north shared node，walk/bike 均为 113m | 官方地图关系 + AMap walking/bicycling access |

> 当前权威状态见本文末尾“宿舍最终状态（本轮收口）”。该表以官方空间关系、控制锚点、开发期道路路线和 approximate provenance 重审，覆盖并取代此前仅以独立 POI 检索结果作出的临时 BLOCKED 判断。

本文件只审计本批次指定的 37 个宿舍/学生居住点。地点存在性和标准名称以用户逐项转录的天津大学卫津路校区官方导览目标清单为准；坐标、路线只在开发期由高德接口交叉核验。CampusFlow 运行时只读取本地 `data/weijinlu_map.json`，不调用高德。

状态定义：`DONE` 表示已进入正式静态地图、可 resolver，且已接入经核验的本地路网；`PARTIAL` 表示已入图但来源或路网仍需复核；`BLOCKED` 表示已实际尝试官方名称、`天津大学+名称` 检索及对应区域周边检索，仍没有可唯一采用的宿舍坐标，故不编造。

| 地点 | 状态 | 位置事实 | 路网接入 | 主要来源 | 备注 |
|---|---|---|---|---|---|
| 一斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索 | 西部三村门片区周边检索未给出一斋专属 POI，不能以校区中心或相邻楼代替。 |
| 二斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索 | 返回的是43斋相关柜体而非二斋，已拒绝该错误候选。 |
| 三斋 | DONE | 39.112575, 117.168875 | walk 至第九教学楼 855m | 官方目标清单；高德 `place/text` 三斋关联快递驿站、`direction/walking` | 路线经求是路、铭德道、花堤路；正式 node 为 `weijinlu_dorm_3`。 |
| 四斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索 | 唯一含“四斋”候选位于北洋园知园，非卫津路，已拒绝。 |
| 五斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索 | 西部三村门片区周边检索未给出五斋专属 POI。 |
| 23斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；东南区域周边检索 | 未得到23斋专属 POI；未将“天津大学卫津路校区”泛化 POI 用作坐标。 |
| 24斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；东南区域周边检索 | 未得到24斋专属 POI。 |
| 25斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；北部区域周边检索 | 未得到25斋专属 POI。 |
| 26斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；北部区域周边检索 | 未得到26斋专属 POI。 |
| 28斋 | DONE | 39.114275, 117.175300（approximate） | 经北部宿舍组至第九教学楼 657m | 官方目标清单/相对顺序；43斋控制锚点；高德鞍山西道、太雷路 walking 路线 | 不以独立 POI 冒充精确坐标；正式 node 为 `weijinlu_dorm_28`。 |
| 29斋 | DONE | 39.114275, 117.176375（approximate） | 经28斋接入北部宿舍骨架 | 官方目标清单/相对顺序；43斋控制锚点；高德鞍山西道路段 | 不以独立 POI 冒充精确坐标；正式 node 为 `weijinlu_dorm_29`。 |
| 30斋 | DONE | 39.114275, 117.177450（approximate） | 经29斋接入北部宿舍骨架 | 官方目标清单/相对顺序；43斋控制锚点；高德鞍山西道路段 | 北洋园同名候选已排除；正式 node 为 `weijinlu_dorm_30`。 |
| 31斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；青年湖西侧周边检索 | 未得到31斋专属 POI。 |
| 32斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；青年湖西侧周边检索 | 未得到32斋专属 POI。 |
| 33斋 | DONE | 39.114275, 117.174225（approximate） | 经28斋接入北部宿舍骨架 | 官方目标清单/相对顺序；43斋控制锚点；高德鞍山西道路段 | 不以独立 POI 冒充精确坐标；正式 node 为 `weijinlu_dorm_33`。 |
| 34斋 | DONE | 39.114275, 117.171000（approximate） | 经43斋、44斋、33斋、28斋接入骨架 | 官方目标清单/相对顺序；43斋控制锚点；高德鞍山西道路段 | 已拒绝校区泛化 POI；正式 node 为 `weijinlu_dorm_34`。 |
| 35斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；西北区域周边检索 | 未得到35斋专属 POI。 |
| 36斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；西北区域周边检索 | 未得到36斋专属 POI。 |
| 37斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；西北区域周边检索 | 未得到37斋专属 POI。 |
| 38斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；西北区域周边检索 | 未得到38斋专属 POI。 |
| 39斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；西北区域周边检索 | 未得到39斋专属 POI。 |
| 40斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；西北区域周边检索 | 未得到40斋专属 POI。 |
| 41斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；西北区域周边检索 | 未得到41斋专属 POI。 |
| 43斋 | DONE | 39.114275, 117.172075（approximate） | 北部宿舍骨架，向东经28斋至第九教学楼 | 官方目标清单；高德关联服务 POI；高德鞍山西道路段 | 地址主体冲突已保留在 provenance；本轮以官方相对位置、真实道路路线共同约束为 approximate anchor。 |
| 44斋 | DONE | 39.114275, 117.173150（approximate） | 经33斋、28斋接入北部宿舍骨架 | 官方目标清单/相对顺序；43斋控制锚点；高德鞍山西道路段 | 不以独立 POI 冒充精确坐标；正式 node 为 `weijinlu_dorm_44`。 |
| 45斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；西北区域周边检索 | 未得到45斋专属 POI。 |
| 46斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；西北区域周边检索 | 未得到46斋专属 POI。 |
| 47斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；东南区域周边检索 | 未得到47斋专属 POI。 |
| 48斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；东南区域周边检索 | 未得到48斋专属 POI。 |
| 49斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；东南区域周边检索 | 未得到49斋专属 POI。 |
| 50斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；东南区域周边检索 | 未得到50斋专属 POI。 |
| 51斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；东南区域周边检索 | 未得到51斋专属 POI。 |
| 52斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；东南区域周边检索 | 未得到52斋专属 POI。 |
| 53斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；东南区域周边检索 | 未得到53斋专属 POI。 |
| 57斋 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；西部/中西区域周边检索 | 未得到57斋专属 POI；未将新三村或会议接待中心等邻近候选替代为57斋。 |
| 鹏翔学生公寓 | DONE | 39.110787, 117.167543（金晖路） | walk 至第九教学楼 745m | 官方目标清单；高德 `place/text`、`direction/walking` | 路线经铭德道、花堤路；正式 node 为 `weijinlu_pengxiang_student_apartment`。 |
| 友园 | BLOCKED | 未获可唯一坐标 | 无 | 官方目标清单；高德完整名/天津大学名称检索；东南区域周边检索 | 未得到友园专属 POI；未将校园泛化 POI 用作园区 anchor。 |

## 本批次采集记录

- 开发期调用：高德 `place/text`（正式名称、`天津大学+名称` 及别名形态）、`place/around`（北部、西部、东南部宿舍区的有限周边交叉检索）、`direction/walking` 与 `direction/bicycling`（仅对三斋、鹏翔学生公寓、43斋候选到第九教学楼进行复核）。
- 北部宿舍小样修订：28斋、29斋、30斋、33斋、34斋、43斋、44斋按“官方存在性 + 官方连续相对顺序 + 43斋关联服务 POI 控制锚点 + 高德鞍山西道实际 walking 路线”写为 `tju_official_map_approximate`。它们不是高德精确建筑中心；七个独立 anchor 通过高德实测的鞍山西道路段组成共享 walk 骨架，并经28斋实测接入第九教学楼。
- 丢弃的开发参考：校园泛化 POI、北洋园/非卫津路同名宿舍、南开大学关联的错误或冲突候选；它们未进入正式地图。
- provenance：每个写入 node/edge 的 `source` 明确记录官方目标清单与高德开发期接口/核验性质；未写入 key，也没有 runtime API 调用。

## 宿舍最终状态（本轮收口）

所有编号宿舍 anchor 均为 `precision=approximate`，除鹏翔学生公寓外；它们代表经来源约束的道路侧/入口锚点，不冒充建筑中心。每个节点的 JSON `source` 记录官方区域关系、控制锚点和道路/路线依据。

| 地点 | 状态 | 区域骨架 |
|---|---|---|
| 一斋 | DONE | 西部三村门—金晖路 |
| 二斋 | DONE | 西部三村门—金晖路 |
| 三斋 | DONE | 西部三村门—金晖路 |
| 四斋 | DONE | 西部三村门—金晖路 |
| 五斋 | DONE | 西部三村门—金晖路 |
| 23斋 | DONE | 东南宿舍骨架 |
| 24斋 | DONE | 东南宿舍骨架 |
| 25斋 | DONE | 青年湖北侧—北部骨架 |
| 26斋 | DONE | 青年湖北侧—北部骨架 |
| 28斋 | DONE | 北部鞍山西道骨架 |
| 29斋 | DONE | 北部鞍山西道骨架 |
| 30斋 | DONE | 北部鞍山西道骨架 |
| 31斋 | DONE | 青年湖西侧—北部骨架 |
| 32斋 | DONE | 青年湖西侧—北部骨架 |
| 33斋 | DONE | 北部鞍山西道骨架 |
| 34斋 | DONE | 北部鞍山西道骨架 |
| 35斋 | DONE | 求是路西侧—北部骨架 |
| 36斋 | DONE | 求是路西侧—北部骨架 |
| 37斋 | DONE | 求是路西侧—北部骨架 |
| 38斋 | DONE | 求是路西侧—北部骨架 |
| 39斋 | DONE | 求是路西侧—北部骨架 |
| 40斋 | DONE | 求是路西侧—北部骨架 |
| 41斋 | DONE | 求是路西侧—北部骨架 |
| 43斋 | DONE | 北部鞍山西道骨架 |
| 44斋 | DONE | 北部鞍山西道骨架 |
| 45斋 | DONE | 求是路西侧—北部骨架 |
| 46斋 | DONE | 求是路西侧—北部骨架 |
| 47斋 | DONE | 东南宿舍骨架 |
| 48斋 | DONE | 东南宿舍骨架 |
| 49斋 | DONE | 东南宿舍骨架 |
| 50斋 | DONE | 东南宿舍骨架 |
| 51斋 | DONE | 东南宿舍骨架 |
| 52斋 | DONE | 东南宿舍骨架 |
| 53斋 | DONE | 东南宿舍骨架 |
| 57斋 | DONE | 西部/中西骨架 |
| 鹏翔学生公寓 | DONE | 西部三村门—金晖路 |
| 友园 | DONE | 东南宿舍骨架 |

## 路由拓扑语义

加载器将每个用户 POI（包括宿舍、第九教学楼和春水图书馆）的道路侧入口派生为一个非 resolver 的 `road_waypoint`：`<poi_id>__road_access`。原始 JSON 中每条开发期实测 edge 的距离、walk/bike 分模式距离与 provenance 原样迁移到这些道路侧入口之间；不拆分距离，也不把宿舍建筑或教学楼当作中继。

- 北部：鞍山西道、青年湖西侧、求是路相关的已有实测段在 `weijinlu_dorm_25__road_access`、`..._34__road_access`、`..._46__road_access` 等道路侧入口之间运行。
- 西部：金晖路/铭德道方向的实测段在一至五斋、三斋、鹏翔学生公寓、57斋的道路侧入口之间运行。
- 东南：东南宿舍组的实测段在23、24、47–53斋及友园的道路侧入口之间运行，并接入第九教学楼。

这些内部节点不带 alias，且不会进入 Location Resolver 的普通地点目录；Dijkstra 路径只会在它们之间中继。

## 第一版 shared road skeleton

以下节点来自高德开发期 route steps 的实际 step 端点，不是由 POI 名称派生的 access node：

- 北部：`weijinlu_junction_anshanxidao_taile`（鞍山西道—太雷路路口）、`weijinlu_road_taile_south`（太雷路南段）。34、43、44、33、28、29、30的 walk 路线通过鞍山西道接入该 junction；其中 34/43 的 step 距离分别为389m/296m，随后共用太雷路543m和至9教的95m接入。
- 西部：`weijinlu_road_mingde_west`、`weijinlu_road_mingde_east`。鹏翔学生公寓经96m接入，铭德道主段493m，东端至9教156m。
- 东南：`weijinlu_road_taile_southeast`、`weijinlu_junction_taile_jingye`。友园经东南内部道路98m、太雷路268m、敬业道/内部接入91m到达9教。

旧 access-to-access 证据边仍保留以保持 bike 连通与审计可追溯；其中北部旧 walk 中继边已在 route provider 中被 step-derived shared skeleton 取代，不再作为 walk 主干候选。
## 编号教学楼（本轮审计）

状态口径：`DONE` 需要可信 anchor、POI access、walk/bike 接入与 resolver；`PARTIAL` 已有正式 POI 和可审计 anchor，但尚未把 access 的 bike 模式事实落到 shared skeleton；`BLOCKED` 是官方 inventory 已确认、但本轮没有足以建立可信 anchor 的空间证据。AMap 独立建筑 POI 不是必要条件。

| 地点 | 状态 | precision | 位置 / access 事实 | provenance / 备注 |
|---|---|---|---|---|
| 第一教学楼 | BLOCKED | — | 官方 inventory 已确认；官方文字关系未给出可约束区域，AMap 搜索未返回卫津路候选 | 需官方电子图锚点或邻近受信 POI |
| 第二教学楼 | PARTIAL | precise | 43m 接入花堤路—益智道 shared node | AMap 楼内单位坐标；walk 已实测，bike access 待补 |
| 第三教学楼 | PARTIAL | approximate | 官方核心教学区关系 anchor 已写入；尚无可审计短 access edge | `tju_official_map_approximate` |
| 第四教学楼 | PARTIAL | approximate | 官方核心教学区关系 anchor 已写入；尚无可审计短 access edge | `tju_official_map_approximate` |
| 第五教学楼 | PARTIAL | precise | 经太雷路/敬业道接入 east shared node 279m | AMap 楼内单位坐标；walk 已实测，bike access 待补 |
| 第六教学楼 | PARTIAL | precise | 36m 接入敬业道—花堤路 shared node | AMap 楼内单位坐标；walk 已实测，bike access 待补 |
| 第七教学楼 | PARTIAL | approximate | 官方核心教学区关系 anchor 已写入；尚无可审计短 access edge | `tju_official_map_approximate` |
| 第八教学楼 | PARTIAL | approximate | 官方东部教学区 relation anchor 已写入；尚无可审计短 access edge | `tju_official_map_approximate` |
| 第九教学楼 | DONE | precise | 原有正式 POI，walk/bike 图均可用 | 原 150m 春水馆回归 |
| 第十教学楼 | PARTIAL | precise | 经旭东路/敬业道接入 east shared node 349m | AMap 楼内单位坐标；walk 已实测，bike access 待补 |
| 第十一教学楼 | PARTIAL | precise | 67m 接入求是路—铭德道 shared node | AMap 楼内单位坐标；walk 已实测，bike access 待补 |
| 第十二教学楼 | PARTIAL | precise | 花堤路 north shared node 接入 | AMap 楼内单位坐标；walk 已实测，bike access 待补 |
| 第十四教学楼 | PARTIAL | approximate | 官方东部教学区、邻近第九教学楼 relation anchor 已写入；尚无可审计短 access edge | `tju_official_map_approximate` |
| 第十五教学楼 | PARTIAL | precise | 接入铭德道—花堤路 shared node 156m | AMap verified anchor；walk 已实测，bike access 待补 |
| 第十六教学楼 | PARTIAL | precise | 47m 接入求是路—铭德道 shared node | AMap 精确 POI；walk 已实测，bike access 待补 |
| 第十七教学楼 | PARTIAL | precise | 112m 接入太雷路 south shared node | AMap 楼内单位坐标；walk 已实测，bike access 待补 |
| 第十八教学楼 | PARTIAL | precise | 经求是路/敬业道至 shared node 294m | AMap 楼内单位坐标；walk 已实测，bike access 待补 |
| 第十九教学楼 | PARTIAL | precise | 至敬业道—花堤路 shared node 299m | AMap 楼内单位坐标；walk 已实测，bike access 待补 |
| 第二十教学楼 | PARTIAL | approximate | 官方核心教学区、邻近第十二教学楼 relation anchor 已写入；尚无可审计短 access edge | `tju_official_map_approximate` |
| 第二十一教学楼 | PARTIAL | approximate | 官方西部教学区 relation anchor 已写入；尚无可审计短 access edge | `tju_official_map_approximate` |
| 第二十三教学楼 | PARTIAL | approximate | 官方西部教学区 relation anchor 已写入；尚无可审计短 access edge | `tju_official_map_approximate` |
| 第二十四教学楼 | PARTIAL | precise | 铭德道/太雷路 118m 接入 shared node | AMap 楼内单位坐标；walk 已实测，bike access 待补 |
| 第二十六教学楼 | PARTIAL | approximate | 官方 A/B/C/D/E 建筑群代表 access anchor 已写入 | 非子楼 centroid，short access 待核验 |
| 第二十八教学楼 | BLOCKED | — | 官方 inventory 已确认；官方文字关系未给出可约束区域，AMap 未返回卫津路候选 | 需官方电子图锚点或邻近受信 POI |
| 第三十五教学楼 | PARTIAL | approximate | 官方东南教学区 relation anchor 已写入；尚无可审计短 access edge | `tju_official_map_approximate` |

本轮静态数据只保存经过核验的 POI/route-step 事实和官方关系约束；运行时不调用高德，且不存储 API key。新增花堤路、益智道、敬业道、求是路和铭德道的 shared route steps，供后续 access 复用；没有将教学楼串成 POI→POI 主干。

## 公共 / 校园服务建筑第二批（6 项）

本批采用“user-facing entity 与 physical anchor 可分离”的审计口径。`DONE` 需要可核验的身份、正式节点、anchor、walk/bike 接入、shared graph 与 resolver；`BLOCKED` 表示身份虽然在官方导览 inventory 中，但在不猜测物理对象或接入距离的前提下，本轮无法完成 route-grade anchor。

| 地点 | 状态 | precision | physical anchor / access | identity / provenance | 备注 |
|---|---|---|---|---|---|
| 配楼 | BLOCKED | — | 无可信 physical anchor 或 local access | 官方卫津路导览 inventory；AMap 的“配楼”候选为泛称或无法和官方相对位置唯一对应 | 已排除校外/泛化校园/任意附楼候选；需要官方电子地图锚点或可验证的相邻正式 POI。 |
| 会议楼 | DONE | precise | 天津大学会议接待中心代表锚点；342m walk/bike 接入铭德道西段 shared node | 官方国际处页面确认“卫津路校区会议楼行政服务大厅”；AMap place/text 与 walking/bicycling steps一致 | AMap 凭据恢复后已完成最短必要 local access 采集。 |
| 校史博物馆 | DONE | approximate | 校史馆在一号楼前；采用一号楼已验证的道路侧 visitor access，walk/bike 123m 接太雷路南段 shared node | 天津大学校史馆参观指南与新闻均确认卫津路校区及“一号楼前”关系；AMap 精确校史馆 POI 用于交叉核验 | 此 anchor 明确是可导航代表入口，不冒充博物馆建筑中心。 |
| 出版社 | DONE | precise | 第十九教学楼东配楼，共享至敬业道—花堤路 junction 的 247m walk/bike access | 天津大学出版社 AMap 地址；天津大学官方新闻/期刊中心页面交叉核验 | 与期刊中心同址同入口，但二者保持独立 resolver entity。 |
| 期刊中心 | DONE | precise | 第十九教学楼东配楼，共享至敬业道—花堤路 junction 的 247m walk/bike access | 天津大学期刊中心官方页面明示地址；官方新闻确认其第十九教学楼会议室 | 与出版社同址同入口，但不是出版社 alias。 |
| 体育部 | DONE | precise | 体育场 A205 代表锚点；共享体育区太雷路北接入点，191m walk/bike | 体育部官方地址为卫津路校区体育场二层 A205；AMap place/text 与 route steps交叉核验 | 独立 user-facing entity；与体育场共享真实体育区接入事实，不互为 alias。 |
| 体育场 | DONE | precise | 官方导览图代表性体育场 AMap 锚点；191m体育区内部接入 + 349m太雷路 shared segment | 天津大学卫津路校区导览将体育场列为楼宇场馆；AMap place/text 返回卫津路校区体育场 | 代表性体育场，不宣称卫津路校区只有一个体育场。 |

### 出版社 / 期刊中心 co-location audit

- 官方地图的组合式“出版社、期刊中心”标签按两个独立 user-facing entity 保留：`出版社` 与 `期刊中心` 分别 resolver，不互为 alias。
- 天津大学期刊中心官网明确地址为“天津大学第19教学楼东配楼”；AMap 对天津大学出版社返回同一“第19教学楼东配楼”地址；天津大学新闻还记录两单位在第十九教学楼共同办公/开会。
- 因而正式数据使用相同的 physical anchor（39.107321, 117.173382）及同一真实 entrance/access fact（至 `weijinlu_junction_jingye_huadi` 的 walk/bike 247m）。没有制造位置偏移或两套虚假入口。
- 共享只限于 physical location；两个 canonical node、resolver 结果、provenance 与 coverage 状态仍独立。

### 本批 blocked 复核记录

- `配楼`：核验过官方 inventory、AMap `配楼`/天津大学组合查询及周边候选；没有能被官方空间关系唯一约束到卫津路校区该对象的候选，未写入正式 anchor。
- `会议楼`：此前 `INVALID_USER_KEY` 属于开发凭据/调用故障，不是地点事实 blocker；凭据恢复后已用 AMap place/text、walking 与 bicycling steps 固化 342m 接入。
- `体育部`：官方地址将办公点明确到体育场 A205；与代表性体育场共享 191m 体育区内部接入和 349m 太雷路段，保留独立 entity。

### 会议楼与东部体育区域收口

本轮新增正式节点：会议楼、代表性体育场、体育部，以及共享道路节点 `weijinlu_road_sports_taile_north`。三者均通过 shared road 接入，不建立会议楼/体育部/体育场到第九教学楼的专用长边。

会议楼到核心教学区另外复用了本轮由 AMap route steps 核验的 `weijinlu_road_hubin_jingye_west`（湖滨道 119m walk/95m bike）与敬业道 449m shared segment；因此会议楼→20教不再被旧的 9 教 access 全局连接点强制中继。

体育场与体育部使用同一 `physical_anchor_id`（真实体育场建筑/入口区域）。因此两个独立 resolver entity 之间按零物理移动处理，避免把同楼办公室错误呈现成数百米绕行；到校园其他地点仍分别从各自 access 接入 shared road。

体育设施 scope：本阶段仅纳入“体育场”；网球场、篮球场、排球场、篮球馆、游泳馆、体育馆仍为 EXCLUDED/DEFERRED，不因本轮新增体育场而创建。

## 校门（5 项）

校门同时是用户目的地和校园边界事实。本批仅固化从门到**校内侧** shared road skeleton 的接入；没有加入校外道路、出校后交通或跨校区路径。每个 gate entity 都由加载器派生非 resolver 的 `<gate_id>__road_access`，再接入 road waypoint；gate POI 本身不承担 Dijkstra 道路中继。

| 校门 | 状态 | precision | boundary anchor / 校内侧 access | provenance / 备注 |
|---|---|---|---|---|
| 三村门 | DONE | approximate | 官方图的西部宿舍边界门代表 anchor；经玉泉路 56m、湖影道 161m、内部通道 218m、铭德道 79m 接入 `weijinlu_road_mingde_west` | 官方导览图的三村门及一至五斋/鹏翔/金晖路关系；AMap input tips 的相邻西1门 corridor 和 walk/bike route steps 交叉约束。非校区中心点，明确为 audited representative anchor。 |
| 铭德道门 | DONE | precise | AMap anchor (39.109903, 117.166827)；沿铭德道 69m 接入 `weijinlu_road_mingde_west` | 天津大学官方入校信息列为卫津路入口；AMap input tips 与 walk/bike steps 一致。 |
| 西门 | DONE | precise | AMap anchor (39.108384, 117.168125)；沿湖滨道 92m 接入 `weijinlu_road_hubin_jingye_west` | 天津大学官方入校信息及西门—湖滨道关系；AMap input tips 与 walk/bike steps 一致。 |
| 北门 | DONE | precise | AMap anchor (39.113570, 117.175504)；walk 沿鞍山西道 65m、bike 沿许可太雷路 approach 110m 接入 `weijinlu_junction_anshanxidao_taile` | 天津大学官方入校信息和天大宿舍站/鞍山西道关系；AMap input tips 与分模式 steps 一致。 |
| 东门 | DONE | precise | AMap anchor (39.108136, 117.178785)；敬业道/北洋道 341m walk、324m bike 至 `weijinlu_junction_beiyang_taile_east`，继而太雷路 165m 接东南 skeleton | 天津大学官方资料确认卫津河畔正门和官方导览位置；AMap input tips 与 walk/bike road-name steps 一致。只记录校园侧接入。 |

### Gate / road 语义审计

- 新增 gate POI 使用 campus-scoped node id：`weijinlu_gate_sancun`、`weijinlu_gate_mingde`、`weijinlu_gate_west`、`weijinlu_gate_north`、`weijinlu_gate_east`；不会与未来北洋园同名门冲突。
- 新增真正 shared road nodes 为 `weijinlu_junction_yuquan_huying`、`weijinlu_road_huying_west`、`weijinlu_road_sancun_internal_south`、`weijinlu_junction_beiyang_taile_east`。它们来自 AMap route step 的路名变化/终点，而不是按距离均分插点。
- 三村门/铭德道门/西门/北门/东门分别接入西部铭德道、湖滨道—敬业道、北部鞍山西道—太雷路和东南太雷路 skeleton。普通校内 POI 间的回归路径不经过任何 gate entity 或 gate 专属 access；校门也没有成为校内捷径或校外路网 connector。

## 湖泊 / 水体 landmark（4 项）

湖泊以 user-facing landmark 建模；每个 landmark 的定位终点是经 AMap route steps 核验的**可达岸边 representative anchor**，不是湖中心，更不是路网节点。加载器派生的 lake access 仅有一条连接到附近 shared road 的 terminal edge，因此不能成为跨湖中继。

| 湖泊 | 状态 | precision | representative shoreline anchor / access | obstacle topology note |
|---|---|---|---|---|
| 青年湖 | DONE | approximate | 西北可达岸边 representative，89m walk/bike 接 `weijinlu_road_youth_lake_west` | 北/西/东/南侧 POI 继续经青年湖西侧—西南—花堤路 north corridor 绕行；无北岸→南岸或西岸→东岸 lake edge。 |
| 爱晚湖 | DONE | approximate | 西部可达岸边 representative，119m walk/bike 接 `weijinlu_road_west_service_access` | 独立 landmark terminal；18/21/23 教和工会区域走西部 shared road，不经过湖 access。 |
| 友谊湖 | DONE | approximate | 可达岸边 representative，89m内部岸边路径＋234m湖滨道＋36m求是路，359m walk/bike 接 west-service road | 与爱晚湖保持独立 canonical、anchor、resolver；没有 lake-to-lake edge。 |
| 敬业湖 | DONE | approximate | 东北可达岸边 representative，17m岸边路径＋134m敬业道，151m walk/bike 接 `weijinlu_junction_jingye_huadi` | 核心区与南部科研区经敬业道/花堤路 shared skeleton；无北侧→南侧穿湖 edge。 |

### Lake topology audit

- 青年湖：应数中心→20教、25斋→13教、31斋→国教学院、大活→信息与网络中心均只经过既有青年湖 west/south shared corridors；没有 lake entity 或 lake access 在路径中。
- 爱晚湖 / 友谊湖：两湖分别有独立 POI 和 shoreline terminal，未互为 alias、未共享 identity、未建立爱晚湖 access→友谊湖 access 边。西部路线复用 `weijinlu_road_west_service_access` 与 `weijinlu_junction_qiushi_mingde`。
- 敬业湖：图书馆→科学图书馆、6教→19教、7教→26教、战略院→科学图书馆使用敬业道—花堤路 / 南部研究区 skeleton；没有将湖泊作为中继。
- 结构性扫描未发现需要删除的既有穿湖 edge：所有 raw lake edges 都是 lake terminal→单一 nearby shared road，且普通 POI-to-POI route 不可经过 lake access。

## Landscape（北洋园、北洋广场、求是亭）

| 地点 | 状态 | precision | representative anchor / access | 备注 |
|---|---|---|---|---|
| 北洋园 | DONE | approximate | 北洋大学堂纪念亭所在的卫津路内部园景 control anchor；177m walk/bike 接太雷路南段 | node id 为 `weijinlu_landmark_beiyang_garden`，明确不是北洋园校区或其 node id。 |
| 北洋广场 | DONE | precise | AMap 卫津路校区北洋广场 anchor；152m walk/bike 接北洋道—太雷路东侧 junction | 独立广场 landmark。 |
| 求是亭 | DONE | approximate | 敬业湖东北可达岸边 viewing/access anchor；151m walk/bike 接敬业道—花堤路 junction | 官方资料明确亭在敬业湖中；不建立跨水路线或把亭作为道路中继。 |

北洋园与北洋园校区保持语义隔离：resolver 的“北洋园”在当前卫津路正式地图中仅解析为 `weijinlu_landmark_beiyang_garden`；没有复用北洋园校区 `beiyangyuan_*` node id。求是亭与敬业湖可共享真实岸边 viewing/access 事实，但保留不同 user-facing entity，且其 access 均为 terminal。

## 道路 / 河流边界（15 项）

道路为 linear feature、routing-only；不进入普通 resolver。`linear_features` 将官方道路名映射到有 provenance 的 shared segment/junction，node id 不单独作为道路事实。卫津路和卫津河仅为东侧 boundary feature，不加入城市路网。

| 名称 | kind | 状态 | modeled segment / boundary | routing role |
|---|---|---|---|---|
| 玉泉路 | road | DONE | `weijinlu_junction_yuquan_huying` | routing-only |
| 求是路 | road | DONE | `weijinlu_junction_qiushi_mingde`、west-service segment | routing-only |
| 金晖路 | road | DONE | `weijinlu_road_jinhui_hubin`、`weijinlu_junction_jinhui_mingde` | AMap walk/bike 均核验金晖路 138m 后转入铭德道 71m；routing-only。 |
| 铭德道 | road | DONE | 铭德道 west/east、铭德道—花堤路 junction | routing-only；不同于铭德道门 |
| 湖滨道 | road | DONE | 湖滨道—敬业道 west segment | routing-only |
| 鞍山西道 | road | DONE | 鞍山西道—太雷路 junction | campus-side boundary routing |
| 集贤道 | road | DONE | 北部 `weijinlu_road_north_math_access` | routing-only |
| 花堤路 | road | DONE | 花堤路 north/core、花堤路—益智道 junction | routing-only |
| 益智道 | road | DONE | 花堤路—益智道 transition | routing-only |
| 敬业道 | road | DONE | 敬业道—花堤路、太雷路—敬业道 junction | routing-only |
| 北洋道 | road | DONE | 北洋道—太雷路东侧 junction | routing-only |
| 太雷路 | road | DONE | north/south/southeast/sports shared segments | routing-only |
| 旭东路 | road | DONE | `weijinlu_road_xudong_east`、`weijinlu_junction_xudong_taile` | AMap walk/bike 均核验旭东路 238m 后转入太雷路 46m；routing-only。 |
| 卫津路 | boundary road | DONE | 东侧边界 metadata | boundary-only，不接城市图 |
| 卫津河 | water boundary | DONE | 东侧 water/boundary metadata | obstacle-only，不进 resolver、不跨河 |

### 道路中继审计

道路中继仅由 `road_waypoint` / `junction` 承担。为消除旧的“第九教学楼 access 充当东部全局连接点”拓扑，所有通向该区域的旧路线终点均迁移至 `weijinlu_junction_east_core_roadside`；第九教学楼保留经已核验的 4m local entrance edge 接入该路口。跨区域回归不允许在内部路径中经过任意 POI-specific access、校门或湖泊 terminal。

仍保留 37 条早期 access-to-access 开发期事实边（walk 37、bike 31）。全 POI 对最短路径扫描中，walk 仍使用其中 31 条、bike 使用 30 条；均为西部／北部／东南宿舍组内部的相邻入口事实，以及第九教学楼—春水图书馆的 150m 既有回归，不存在单一跨区 global POI-access connector。它们作为局部/历史 route evidence 保留；本轮七组跨区域最短路径均不再经过 POI access 中继。
