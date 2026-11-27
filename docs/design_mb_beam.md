# 多波段（GalfitS）Beam Search 引擎 — 设计概要 v0.2

> 状态：设计已闭合，待动工（P0 起）。
> 背景：单波段 GALFIT beam search 引擎（`src/beam/`，分支 `restrict-disk-bulge-bar-agn`）
> 已稳定；本设计将该引擎移植到多波段 GalfitS `.lyric` 流程，替换
> `beam_search` 分支上 agent 驱动的旧多波段流程（易错、无 Engine）。
> 参考实现：`galfits/kilogas_mb/`（KILOGAS_110/13/216/25/325 共 6 星系，旧
> workflow_galfits.md 流程产物，兼作黄金回放集）。

## 0. 总体架构：共享核心 + 格式后端

- `src/beam/` 的格式无关模块保持共享：graph 骨架（状态图/队列/预算/
  stagnation/verdict 门控）、enqueue（老化/多样性/cells/class caps）、
  cells、signature、digest、survey（两轮 CoT 派发）、vlm_extract、
  note_export、physicality（阈值波段化）。
- 新建 `src/beam_mb/` 只装格式层：`lyric_doc.py`（解析器）、
  `transcribe_mb.py`（转录）、`candidate_schema_mb.py`（候选 schema）、
  mb prompts。
- graph.py / tools.py 的少数格式接缝（两处解析 seam、工件键、metric 键、
  PSF meta）加 `mode` 参数分流。
- **验收红线**：接缝抽象后 galfit 模式行为零变化（现有 247 个 beam 测试全绿）。
- 备选方案（beam_mb 全套复制 graph.py）会造成 ~1400 行分叉与双份维护，已否决。

## 1. 数据层：LyricDoc 轻量解析器

原则：**不碰 GalfitS 的 `read_config_file`**（构建模型对象、触发 jax 等重
加载；解析产物非结构化数据）。把 `check_lyric_file`（modify_lyric.py）已
验证的轻量 tokenizer 从"校验器"升级为"结构化发射器"：纯数据、单遍、
零重依赖。

### 1.1 词法层（复用 check_lyric_file 规则）

- 行正则 `^([A-Za-z])([a-z0-9]*)(\d+)\)\s*(.*)` → `(family, label, index, raw)`；
- 值解析 `ast.literal_eval`，失败回退纯字符串（与 galfits `parse_config_file`
  同语义，无 eval）；
- 保留文件顺序（P 块文件序 = 成分序）；
- 沿用分组规则（`P{label}{N>1}` 必须落在 `{F}{label}1` 块内）、重复键查错、
  five-tuple 语义校验（flag∈{0,1}、init∈[min,max]、min<max、全零槽豁免）、
  标识符 lint。

### 1.2 文档层（纯 dataclass）

```
LyricDoc
 ├─ region      R1 名字 / R2 [ra,dec] / R3 尺度
 ├─ images      {band_name: ImageBlock}        # 键 = I2（Ia2='decam_g'...）
 ├─ atlas       Aa1 名字, Aa2 波段标签表 ['a','b','c']
 ├─ components  [ComponentBlock]（按文件序）
 │     .label('a') .name(P1='disk') .type(P2='sersic')
 │     .rows{rownum: 原样值}                   # 五元组逐字保留，不做单位换算
 ├─ galaxies    {label: GalaxyBlock}           # Ga2 成分标签表 → P 标签
 └─ nuclei      {label: NucleusBlock}          # Na1 标签 ↔ Ga1 对应
```

块间引用（**字母标签 join，两个标签空间相互独立**）：

- `Aa2) ['a','b','c']` → **I 块标签**（atlas 装配哪些波段）；
- `Ga2) ['a','b','c']` → **P 块标签**（该星系由哪些成分构成）；
- `Ga1) a` ↔ `Na1`（星系与 AGN 的同标签绑定）；
- `Pa1) disk` — P*1 即结构名 = gssummary 参数名前缀（`disk_Re`），零歧义。

### 1.3 严格不变量（比 GalfitS 更严，硬校验，双侧拦截）

1. 每个 I 块标签必须被 Aa2 引用恰好一次（禁 dangling 波段）；
2. 每个 P 块标签必须被**恰好一个** Ga2 引用（禁 dangling 成分、禁跨星系共享）；
3. 每个 N 块标签必须与某个 Ga1 匹配（禁悬空 AGN）。

违反任何一条 = lyric 结构非法，`check_lyric_file` 与 LyricDoc 构造双双报错。
收益：转录的 fail-fast 围栏（add/remove 必须同步维护 Ga2 成员关系，半成品
转写直接构造失败）；库存来源唯一（G 块成员 ≡ 全部 P 块）；波段子集拟合被
显式禁止。

### 1.4 按类型的行语义表（TYPE_ROW_MAP）

P 行含义随 P2 类型漂移，文档层 rows 原样保留，访问器层按类型分派：

| P2 类型 | P5 | P6 | P7 | P8 | 额外行 |
|---|---|---|---|---|---|
| sersic | Re | n | ang(sky-PA) | axrat | — |
| sersic_f | Re | n | ang | axrat | **P17–P24**（见 §11） |
| ferrer | **Rout** | alpha | ang | axrat | P25 beta |
| edgeondisk | rs | hs | ang | （无 P8） | — |
| GauRing | r0 | sig | ang | axrat | — |
| （sersic_rf / sersic_bf 同 sersic_f，均在本分支解空间外） | | | | | |

所有类型共享 SED 公共行 P9–P16、P26–P32。

### 1.5 视图层（按需、惰性）

| 视图 | 来源 | 用途 |
|---|---|---|
| inventory 视图 | LyricDoc（名/序/标签/波段/vary）⋈ `parse_gssummary`（free+fixed 两段） | record_fit 库存 seam（纯 dict join） |
| 触界视图 | gssummary 拟合值 vs 块内 five-tuple 带；来源 = 与父 lyric 原始带 diff | 整体替代 cons_decode.py |
| 几何视图 | 惰性加载各波段 FITS WCS（缓存） | px↔arcsec 唯一触点（astropy 隔离于此） |

现有 `parse_gssummary / parse_component_types / parse_image_infos_from_lyric`
被吸收进 LyricDoc，不并存第五套。序列化 = 逆过程：未修改的行逐字回写，
diff 天然最小。

## 2. 单位契约（本设计的支柱）

**库存 canonical 单位 = arcsec**（结构参数跨波段共享，与 lyric Pa5 /
gssummary `disk_Re` 同帧）；**VLM 的世界里只有 px，pixscale 永不出现**。

| 环节 | 方向 | 实现路径 |
|---|---|---|
| digest → VLM | arcsec → 每波段 px | 读每轮 `component_attributes.txt`（run_galfits 已生成，零新换算）+ 每波段 FWHM_px + 中心相对 Δx/Δy px |
| VLM 候选 → 引擎 | **px + `panel_band` 标签 → arcsec** | `re_pix2arcsec(re_px, 该波段FITS)` → Pa5；`pixel2arcsec_offset` → Pa3/Pa4 |

- 候选位置字段用**相对星系中心的 Δx/Δy（px）**：lyric Pa3/Pa4 本就是相对
  R2 中心的 arcsec 偏移，中心为全波段共注册点，语义同构；
- PA 例外：VLM 按罗盘直读 **sky-PA**，原样进 Pa7，零换算（`detect_galfits_
  bar_lopsidedness` 的 phase_deg 同约定直拷 Pa23）；
- digest 不出现 pixscale：VLM 需要的同帧参照物全部预换算成 px（每波段
  FWHM_px、父状态各成分每波段 px），所有比较同波段 px 对 px；
- 等/不等 pixscale 数据集对 VLM 完全同构（JWST drizzle 免费兼容）；
- 面板硬性要求：**1:1 像素渲染、不重采样、每波段面板带罗盘**；
- 入口哨兵：转录前粗检 VLM px（re_px×pixscale 合理性、偏移幅度），离谱值
  在转录层拦下，不污染拟合轮。

mech 阈值口径（arcsec 化）：

- Re 下限 = `0.1 × max_bands(FWHM_arcsec)`（最保守）；
- bar_identity 4px → 参考波段 arcsec 等价；
- 同心 2px → min pixscale 波段的 2px。

## 3. 库存与指标

- 库存条目：`{name(P1), type(P2), label, galaxy, re_arcsec, n, q, pa_sky,
  dx/dy_arcsec, mags{band}, agn_flag}`（AGN 即 psf 的 mb 化身，见 §4）；
- metric：直接用 gssummary **全局 BIC**（k 已含），备选 reduced chisq；
  不做 BIC_eff 重算（A_psf 概念不同源）；per-band chisq 存档供诊断；
- graph meta：`{bands: {band: {pixscale, fwhm_px, region}}}`。

## 4. 候选契约（candidate_schema_mb）

- JSON 契约与单波段同构（survey.py / vlm_extract 原样复用）；
- 参数字段：`re_px / dx_px / dy_px + panel_band(必填) / q / pa_sky_deg /
  mag_init(可选，广播各波段)`；
- ops：skeleton / add / remove / tune / reclassify。**convert 不需要**：
  GalfitS 中 singlesersic 与 disk 同为 sersic，single→multi 转换 =
  “n 五元组改 [1, 0.5, 2, 0.1, 0]（n=1 fixed）+ 改名”，比 galfit 模式简单
  （无 expdisk Rs=Re/1.68 转换）；
- reclassify 类型强制表：bar→n=0.5 fixed；disk→n=1 fixed；lens→n<0.5 free；
  bulge→n free（沿用单波段“创建即自由”）；
- **AGN = N 块**（galfit psf 的 mb 化身，解空间表对照 `psf` ↔ `N 块`）：
  - 准入规则波段化：Re < 0.2 px **每波段** → 强制换 N 块；0.2–0.5 px →
    竞争变体（2D 残差更好才接受）；≥ 0.5 px **任一波段** → 保 sersic；
  - 转录层内置反简并配置（image-only Ia15=0）：Na10 固定 42/43、Na26 唯一
    流量自由度、Na11 固定 `[1,0,4,0.1,0]` + Na18=4（稀疏波长覆盖）、
    Aa1 标识符合规、Ga2 不含 N 标签、Na27 两五元组格式；
- 解空间与单波段现分支一致：disk / bulge / bar / agn / singlesersic；
  禁 lens / outerdisk / edgedisk / companion / sersic_rf / sersic_bf
  ——`check_lyric_file` 侧加结构拒绝（等价 galfit 侧 check_feedme_file
  的分支拒绝）；`sersic_f` 仅允许主 disk（见 §11）。

## 5. 转录层 transcribe_mb（最大件）

1. 父 LyricDoc + gssummary（free+fixed 两段）热启动回填：结构参数 arcsec
   原样、每波段 logNorm 原样；含 sersic_f 附加参数（`<name>_r_in/r_out/
   alpha_rc/theta_out/am/theta_m/i_arm`）与 N 块参数，统一规则无特判
   （**严禁 `--readsummary`**，只解析自由参数段）；
2. **默认边界集写进五元组**（继承单波段纪律：候选只准声明初值，任何
   cons_bounds 一律忽略，引导靠初值、失范靠 mech 后验）：
   - re `[floor_arcsec, 半区域弧秒]`；n `[0.1,8]`（bar n=0.5 固定写
     非退化区间）；q `[0.05,1]`；中心 K≤1 时 ±2px→arcsec 窗口；
3. add：新字母标签 P 块 + 追加所属 Ga2；remove：删块 + 删 Ga2 项——
   不变量随写随验；SED 模板行（P9–P16, P26–P32）从兄弟成分克隆；
4. 同心约束 = `iter{n}.constrain` 模板（xcen/ycen 成对；AGN 用
   `xcen_agn`/`ycen_agn`；P*3/P*4 subordinate 置 vary=0，disk 锚点
   vary=1；伴星系豁免——本分支无 companion）；
5. 输出过 `check_lyric_file`（含严格不变量）作为 apply 前门。

## 6. 诊断层

- **触界扫描**：gssummary 拟合值 vs 块内五元组带（|拟合值−边界| ≤ 2% 带）；
  来源判定 = 与父 lyric 原始带 diff；因禁候选边界，所有带均 original
  provenance → 触界一律 note 级；豁免沿用（q=1 域边、Re 下限 pin、
  bar n 先验、m=1 阶数固定）。cons_decode.py 整体退役；
- **mech 检查**（physicality.py 共享，阈值 arcsec 化）：re_chain / onion /
  degeneracy / mu0 / flux_share / re_ratio / bar_identity / 存在性下限
  全部按结构参数照跑；**flux_share 与 μ0 用参考波段口径**（digest 声明，
  默认 VLM 主读波段；A_psf 代理取该波段）。

## 7. VLM 层与 prompt

- 新 `beam_survey_prompt_galfits.md` + `beam_action_generation_prompt_
  galfits.md`：从 `beam_action_generation_prompt.md` 蒸馏规则集，对齐单波段
  的 JSON 契约、Phase 1.5 mech 表权威性、修复菜单、审查条款结构；
  新增单位契约条款（px+波段标签、禁换算、sky-PA 罗盘）；
- 单波段的深度分段候选数、修复菜单触发审查、全局/局部状态使用规则等
  机制性条款平移；
- survey.py 两轮 CoT、重试、请求转储原样复用。

## 8. 工作流文档：workflow_galfits.md 原文件改写（不新建、非 s1 模式）

判定：**不能直接用**。其主循环（六维打分 g / §去重与排序“禁用规则去重” /
R1R2 手工账本 / Markdown 候选契约 / working_note 真源 / 数值规则委托）正是
引擎已机械化的职责，且“禁用规则去重”与引擎 cells/caps 正面冲突。

改写方案：

- **替换**：步骤 1 主循环 → 引擎工具编排（beam_init / survey_round /
  apply_candidate / beam_record_fit / beam_status / beam_export_note）；
- **删除**：六维打分、语义去重、R1/R2 手工环检测、working_note 模板、
  global/local description 手工生成规范（digest.py 代之）；
- **保留**：步骤 0.4 确定性首拟；depth=1 开局规则（lop→sersic_f 切换、
  bar→bulge/bar）；§候选生成的诊断式原则；§通用失败→修正模式（进
  prompt）；§非物理结果恢复协议（并入修复菜单）；步骤 3 锁定收尾；
  **步骤 4 物理意义/奥卡姆分析与阶段三~五（SED 拟合、联合拟合）——beam
  引擎只管 Phase 1 图像拟合，这些段落原样保留**。

## 9. 阶段计划

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | 接缝抽象（mode 分流） | galfit 零变化，247 测试全绿 |
| P1 | LyricDoc + 严格不变量 + TYPE_ROW_MAP | 解析 kilogas_mb 6 星系全部 lyric/迭代文件 |
| P2 | record_fit / beam_init 的 mb 路径 | 6 星系旧 run 全部入图，BIC 与 gssummary 一致 |
| P3 | transcribe_mb + constrain + 校验门 | **黄金回放**：父 lyric+gssummary+手写原语逐轮复现 `_iter2.._iter11`（6 星系天然 golden set） |
| P4 | candidate_schema_mb + prompts + survey 挂接 | 单测 + 模拟 VLM 输出过全链 |
| P5 | mech 波段化 + 全流程试跑 | 6 星系 baseline 对比旧 agent 结果 |
| P6 | workflow_galfits.md 引擎化改写 + 锁定准则 | — |

## 10. 已锁定决策（2026-10-31 讨论定案）

1. bulge n **创建即自由**（沿用单波段 austerity 结论；初值按残差证据
   0.5–8；GalfitS 指南的“固定 4 三级释放”不采用）；
2. mb 解空间**与单波段现分支一致**（disk/bulge/bar/agn/singlesersic），
   lens/outerdisk/edgedisk/companion 以后再放开；
3. flux_share / μ0 **参考波段口径**；
4. F1 语义：**mb 只有 sersic_f 能承载 m=1，且 sersic_f 只对应 main disk**
   （详见 §11；实现排 P4 之后，初期禁用）；
5. 架构 = **共享核心 + 格式后端**（非全套复制）。

## 11. sersic_f 专项（F1 的 mb 形态）

源码确证（GalfitS/src/galfits/gsutils.py:1238-1259）：sersic_f = sersic
（P3–P8）+ **8 行 P17–P24**，跳过 P25（ferrer beta 槽），SED 公共行
P26–P32 不变。

| 行 | 参数 | 语义 |
|---|---|---|
| P17 | r_in | 傅里叶调制区内半径（arcsec） |
| P18 | r_out | 调制区外半径（arcsec） |
| P19 | alpha_rc | 调制包络径向指数 |
| P20 | theta_out | 外半径处方位角参考（缠绕相位） |
| P21 | m | 傅里叶阶数——**纯值非五元组，GalfitS 硬编码 vary=False** |
| P22 | am | 模振幅（galfit F1 幅度对应物；保留门限 >0.02 同口径） |
| P23 | theta_m | 模相位（sky-PA；lop phase_deg 直拷） |
| P24 | i_arm | 臂倾角（0–90°） |

- disk→sersic_f 切换 = P2 改类型 + **插入** P17–P24 八行；初值
  `am≈0.05`（vary=1，带 [0,0.5]）、`theta_m` 抄开局 lop phase_deg（检出时）、
  `r_in≈0.2×Re_disk`、`r_out≈0.6×Re_disk`、`m=1` 固定纯值；
- 对应单波段 7+1 保留槽：一次保留的 sersic_f 切换拟合；保留判据 =
  BIC 对比 + am > 0.02；反向切换 = 删八行 + P2 改回；
- check_lyric_file 增补：① sersic_f 时 P17–P24 必须齐（缺行会在
  GalfitS 读配置时 KeyError）；② P21 必须纯整数；
- mech 侧 note：am 触 [0,0.5] 带界（尤其下界 0）≈ F1 振幅归零语义；
- `sersic_rf` / `sersic_bf`（环/棒傅里叶变体）共享 P17–P24 语义，均按
  解空间越界拒绝，TYPE_ROW_MAP 登记备查。

## 12. 遗留事项

- F1（sersic_f 切换）的 enqueue_mb 保留槽实现细节（P4 后设计）；
- 阶段三~五（SED / 联合拟合）与 beam 引擎的衔接（Ia15 相位切换是否入
  搜索空间）——暂不在范围内，workflow 相应段落保留 agent 驱动；
- 开局轮（round-0 opening）的 mb 版 prompt 细则（skeleton 候选的 px+
  panel_band 表述、sersic_f 切换开局规则）随 P4 一并蒸馏。
