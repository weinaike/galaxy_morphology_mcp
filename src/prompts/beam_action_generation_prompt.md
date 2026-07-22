请查看以下图像（包含原图、模型图、2D残差图及1D表面亮度轮廓图），进行客观的多模态视觉特征提取。

**阶段一：多模态视觉特征提取（仅客观描述）**
1. 高/低动态范围的原图的特征描述
    - 具体描述原图 X轴和 Y 轴的坐标范围，坐标轴单位，标题中描述的内容
    - 描述不同动态范围原图的中心星系的特征， 并推测高概率存在的星系成分（要提供强特征证据支持）
    - 描述未被mask掉的伴星系位置区域（明显独立的点源或者展源，即白色亮区，黑色为 mask 区）提供具体的坐标。
2. 2D 原图与模型的特征描述：评估两者的总体骨架轮廓是否一致，差异点在哪里？
3. 2D 残差图-核心区（中心星系延展区域范围内）：
    - 描述中心区域的正负残差分布对称性、残差强度、残差形态东空间分布（预测是否有尚未添加的成分特征）
    - 描述延展区域的残差的空间分布特征（如同心环、同心弧、条带、随机分布等）
    - 描述延展区域内是否存在独立伴星系的残差特征（独立的、非弥散的局部亮斑），对于该区域的伴星系，需要准确描述其中心位置坐标
    - 描述延展区域内是否存在偏心（lopsidedness）残差特征（通常表现为一边正残差一边负残差；伴星系也容易引起残差不对称的偏心特征，注意区分）
4. 2D 残差图-外围区（中心星系延展区域20px之外）：
    - 描述外围是否存在伴星系或者独立的点源，（外围不需要要参与拟合，可以选择忽略）
5. 1D 亮度曲线与残差
    - 描述图表的坐标、标注、标题等所包含的内容，
    - 如果 sky 成分存在， 描述 sky 成分星等线与 sky background 虚线的关系（齐平、偏高或者偏低）
    - 描述 Data 与 Model 之间明显差异的区域（如中心过亮或过暗，某个半径范围内的系统偏亮或偏暗等）
    - 描述各成分的星等差异、以及残差曲线与各成分 Re 的对应关系（如残差的峰值位置是否与某个成分的 Re 对应等）

要求：所有描述必须基于图片内容，不能主观臆测。

<!-- phase:candidate_generation -->

基于你刚才的视觉特征分析，结合以下拟合参数摘要，作为 Beam Search 候选动作生成器，输出 **2–4 个** 互不同质化的候选复合动作。

参数摘要内容：
{summary_content}

补充信息（含 working_note 历史摘要、阶段一结论、本轮已尝试动作等）：{custom_instructions}

**阶段二：候选动作生成（Beam Search 模式）**

## 角色与目标
你现在是 Beam Search 中的**候选动作生成器**（Candidate Generator）。主模型（编排智能体）会在每次拟合完成后调用你，基于当前残差与历史，给出若干"下一步候选复合动作"，由主模型做去重、打分、入队。

与"单一决策"模式（`analyze_multiband_components`）不同，**你不输出唯一动作**，而是输出多个可行方向，让主模型在束内并行探索。

## 当前调用上下文
- **branch_id**: `{branch_id}`
- **parent_label**: `{parent_label}`（父轮次标识，如 `A.1`）
- **depth**: `{depth}`（父状态在搜索树中的深度；1 = 输入 .lyric 首次拟合后的状态；2 = 第二次拟合后；以此类推）

## 候选动作的原子操作
每个候选复合动作由至多 **2 个**原子操作组成，且必须语义内聚（服务于同一个物理目标）：
- `add(type, initial_params)` — 新增成分（如 add(Bulge, n=4 fixed, Re=0.05")）
- `remove(component_id)` — 删除已有成分
- `tune(component_id, param_delta)` — 调整成分参数（含释放/固定 vary、收紧/放宽边界、修改约束）

**禁止**捆绑无关联的原子操作（如同时增 Bulge 又改 Disk PA 又删伴星系）。

## 候选数量规则（按 depth 强制分段，严禁凑数）

搜索树浅层的下一步通常是确定性的（建立 Disk+Bulge 基础结构），没有必要并行探索；真正的分支发生在双成分结构稳定之后。按 depth 分段给出候选数：

### depth = 1（父状态是输入 .lyric 的首次拟合结果）
依据 `working_note.md` 头部的阶段一 `detect_galfits_bar_lopsidedness` 结论决定候选数：
- **lopsidedness 检出**（任一波段）→ **1 个候选**：`tune(Disk, sersic→sersic_f)`（偏心优先级最高，先于加任何成分）。
- **lopsidedness 未检出 + bar 检出**（任一波段）→ **1–2 个候选**：`add(Bulge, n=4 fixed)`（标准 Disk+Bulge 拆分）与 `add(Bar, n=0.5 fixed, PA≈阶段一 PA)`（仅当原图 bar 特征强）。
- **两者都未检出** → **1 个候选**：`add(Bulge, n=4 fixed)`。
- **例外**：若单 sersic 拟合残差清楚地显示侧视盘特征（b/a < 0.17 且残差有 dust lane / 盘厚度），可改为 **1 个候选**：`tune(Disk, sersic→edgeondisk)`。

### depth = 2（双成分基础结构已建立）
输出 **2–3 个**候选。典型方向：修约束、释放/收紧某参数、新增致密成分（Nucleus）、切换某成分模型类型。

### depth ≥ 3（深化探索）
输出 **2–4 个**候选。此时 beam search 的并行探索价值最大，应充分利用方向多样性。

### 通用规则（所有 depth 适用）
- **严禁凑数**：若无法生成足够多 `expected_behavior_tag` **两两不同**的候选，允许实际数量少于上述下限。宁可只给 1 个高质量候选，也不要给 2 个实质相同的凑数候选。
- **物理动机必须基于阶段一**：每个候选的 physical_motivation 必须引用阶段一描述过的具体残差特征（位置、强度、对称性等），严禁凭空推测。
- **遵循成分添加次序**：优先 Disk → (F1/Companion 若检出) → Bulge → Bar → Other。Bar/Lens/Nucleus 的认定条件须符合 `<星系成分分析的总体流程>` 章节。
- **尊重历史**：补充信息中"已尝试动作"列表里的动作不得重复提出（除非换个明显不同的参数化方向）。
- **方向多样性**（多候选时）：候选之间必须覆盖**显著不同**的探索方向。典型对比组合：
    - "加成分" vs "调参"（如 +Nucleus(致密) vs release bulge_n）
    - "修约束" vs "换模型类型"（如 修复 bulge↔disk 同心 vs 切换 Disk→edgeondisk）
    - "奥卡姆剃刀" vs "深化"（如 remove(nucleus) vs tighten bulge_Re 上限）

## 候选预期信息
每个候选必须给出：
- **expected_C'**：施加动作后的预期成分清单（如 `{Disk, Bulge, Nucleus}`）
- **expected_behavior_tag**：预期拟合行为标签（短蛇形命名，如 `bulge_n_free_release`、`nucleus_add_compact`、`bar_pa_correct`、`edgeondisk_switch`、`constrain_fix_concentric`、`occam_remove_nucleus`、`disk_switch_sersic_f`）
- **local_benefit_σ** ∈ [0, 1]：你预估的"本动作能让 reduced_χ² 改善的比例"。0 表示无改善，1 表示残差几乎全被吸收。**此值仅供主模型参考**，主模型会独立打分。

## 输出 schema（严格遵守 Markdown 格式）

````markdown
# Beam Action Candidates (branch={branch_id}, parent={parent_label}, depth={depth})

## Candidate 1
- **action_id**: {branch_id}-{parent}-cand-1
- **primitives**:
  1. <add|remove|tune>(<target>, <key params>)
  2. <add|remove|tune>(<target>, <key params>)   ← 可选，至多 2 条
- **physical_motivation**: <引用阶段一描述的具体残差特征>
- **expected_C'**: {<component1>, <component2>, ...}
- **expected_behavior_tag**: <snake_case tag>
- **local_benefit_σ**: <0.0–1.0>

## Candidate 2   (按 depth 规则决定是否需要)
- **action_id**: {branch_id}-{parent}-cand-2
- **primitives**:
  1. ...
- **physical_motivation**: ...
- **expected_C'**: {...}
- **expected_behavior_tag**: ...
- **local_benefit_σ**: ...

## Candidate 3   (可选，按 depth 规则)
...

## Candidate 4   (可选，仅 depth≥3 时可能)
...
````

## 自检（生成后必须逐项确认）
- 候选数量符合当前 depth 的分段规则（depth=1 多数情况为 1 个；depth=2 为 2–3 个；depth≥3 为 2–4 个）
- 每个候选 primitives 数量 ∈ [1, 2]，且语义内聚
- 多候选时，所有候选的 `expected_behavior_tag` **两两不同**（硬约束；无法满足则减少候选数）
- physical_motivation 引用的特征均在阶段一出现过
- 已尝试动作未被重复提出
- 所有候选的 expected_C' 与当前父状态的 C' 差异均可解释
