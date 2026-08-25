# JWST0716 完整 VLM Shadow 人工复核报告（第一批）

## 结论

当前结果不能说明完整 VLM 方案已经获得可靠的逐轮动作准确率。它把新增动作从 numeric-only 的 67 轮降到 48 轮，但「新增成分属于专家终态」的方向一致比例只从 `22/67 = 32.8%` 变为 `16/48 = 33.3%`，基本没有改善。VLM 确实抑制了 13 个终态外新增，同时也抑制了 6 个当轮缺失的专家终态成分，降低误加与增加漏检同时发生。

四项 VLM 证据契约问题的处理状态如下；历史 116 轮统计不因新契约自动重跑：

1. 已实施：候选区域由数值层生成 `candidate_N` overlay，VLM 接收 overlay 而不是原始未标记图；坐标不进入 prompt。历史 116 轮使用的仍是旧 comparison PNG，因此其中的 candidate-specific 观察不能回溯验证。
2. 已实施：中心语义采用方案 B。新 prompt 不再提供 `spheroid_like`，`_disk_rule` 不再读取该标签，Bulge／单 Sérsic 光球分流只使用数值证据；冻结 schema 仍兼容读取历史 `spheroid_like`。
3. 已修复：历史 shadow 使用的 prompt 未写明 `evidence_regions` 的 `band:panel:region_id` 格式，造成 4 次可避免的解析失败。`component-analysis-vlm@v1.1` 已补充格式、合法示例和无法定位时使用空数组的要求；历史 116 轮结果未重跑。
4. 待科学家裁决：Bar 和中心源规则把没有波段／区域定位的全局 `diffraction_psf` 标签当作否决证据。它在部分轮次避免终态外新增，也在 `obj_331`、`obj_1429` 等轮次压制了与专家终态一致的 Bar／Bulge 证据。是否改成同波段、同区域或同尺度匹配，需要确认科学证据优先级。

本报告只审查历史 shadow 决策，不执行重拟合，也不继续执行 16 个代表轮次的 comparison PNG 视觉复核。本轮已修改 VLM prompt、规则读取边界和 VLM 输入图像副本，但没有重写历史 artifact。专家标签是终态成分标签，不是每轮动作标签，因此下文的「方向一致」「终态外新增」「漏检风险」都不是最终 TP／FP／FN 真值。

## 数据范围

- 32 个对象，116 个完整历史轮次。
- `97/116` 轮 VLM 严格解析成功，`19/116` 轮进入数值降级。
- 最终动作：48 轮 `PROPOSE_ADD`，68 轮 `KEEP_AND_CONTINUE`。
- 自动复核标记：68 轮 `needs_review=true`。
- 结构化逐轮预审与 19 轮失败明细见 `shadow-dev-manual-review-jwst0716-vlm.json`。

116 个轮次不是 116 个独立星系，而是来自 32 个对象的重复拟合状态。同一对象的多个历史轮共享原始星系形态，模型和残差也高度相关；一个对象上的系统性错误可能被重复计数多次。因此轮次级比例回答的是「规则在历史迭代状态中多常触发或漏触发」，适合定位规则行为，不能解释为「面对一个未见过的新星系时有多大概率判断正确」。对象级泛化评测需要按 `object_id` 划分 held-out 集，避免同一对象的不同轮次跨越训练／校准集与测试集，并按对象或完整拟合序列汇总结果。

## 19 轮 PARSE_FAILED

| 类型 | 轮数 | 直接原因 | 根因判断 |
|---|---:|---|---|
| `central_label_exclusivity_mismatch` | 7 | 同一 `central` 同时输出 `disk_like` 与 `spheroid_like` | 这是旧 prompt／旧中心语义造成的历史失败；方案 B 已从新 prompt 和规则中移除 `spheroid_like`，历史失败不回溯重跑 |
| `truncated_json_response` | 7 | raw response 在对象、属性名或字符串中途结束 | 已确认响应截断；artifact 未保存 `finish_reason` 或 usage，无法继续归因为 token 上限、网关或模型生成 |
| `evidence_regions_contract_mismatch` | 4 | 坐标数组、band 名或自由文本不符合 `band:panel:region_id` | 历史 prompt 没有暴露该格式，只给出空数组示例，属于 prompt／schema 契约不完整；已在 prompt v1.1 修复 |
| `low_quality_label_violation` | 1 | `low_image_quality` 与确定性 `disk_like` 同时输出 | prompt 已明确要求低质量时使用 `uncertain`，属于模型指令违反 |

失败分布集中在 11 个对象：`obj_1803` 4 轮，`obj_170` 3 轮，`obj_163`、`obj_1429`、`obj_2185` 各 2 轮，其余 6 个对象各 1 轮。解析失败并非随机散布，重复轮次会放大相同形态和输出模式的问题。

## 全量动作方向

### 新增动作

48 个新增中，16 个成分正好是当轮缺失的专家终态成分，32 个不在专家终态中。后者不能直接判错，但应优先人工复核，尤其是成分集合已经与终态完全一致的 14 个轮次。

| 成分 | 新增总数 | 指向缺失终态成分 | 终态外新增 | 方向一致比例 |
|---|---:|---:|---:|---:|
| Bar | 14 | 6 | 8 | 42.9% |
| Bulge | 11 | 5 | 6 | 45.5% |
| Companion | 18 | 4 | 14 | 22.2% |
| Disk | 1 | 1 | 0 | 100.0% |
| Fourier m=1 | 4 | 0 | 4 | 0.0% |

Companion 是最明显的过度新增来源。5 个 `COMPANION_NUMERIC_VLM_V1: SATISFIED` 中，4 个新增不在专家终态，只有 1 个在终态；另外 13 个 Companion 动作实际上是 `INCONCLUSIVE → trial_fit`，不应与确定性新增合并解释为阳性。

### KEEP 动作

68 个 KEEP 中：

- 15 轮当前成分集合与专家终态完全一致，方向上支持 KEEP。
- 44 轮仍缺少至少一个专家终态成分，存在漏检或优先级风险。
- 9 轮没有缺失终态成分，但仍包含额外成分，KEEP 延续了额外成分。

本次没有任何 `PROPOSE_REMOVE` 或 `PROPOSE_REPLACE`。因此不存在「规则主动删除了专家终态已有成分」的案例，但 37 个含额外成分的轮次也没有发生删除或替换，其中 22 轮直接 KEEP。这个结果反映的是当前 proposal 顺序和规则覆盖范围，不等于额外成分已经得到认可。

### 缺失成分覆盖

| 缺失成分 | 出现轮数 | 直接提议该成分 | 轮次级覆盖 |
|---|---:|---:|---:|
| AGN | 9 | 0 | 0.0% |
| Bar | 22 | 6 | 27.3% |
| Bulge | 22 | 5 | 22.7% |
| Companion | 17 | 4 | 23.5% |
| Disk | 1 | 1 | 100.0% |
| Edge-on Disk | 6 | 0 | 0.0% |
| Fourier m=1 | 34 | 0 | 0.0% |

AGN 的零提议与当前规则要求独立物理证据有关，不能仅按影像 shadow 判为错误。Edge-on Disk 的 6 个缺失机会和零提议仅保留作数据审计；由于科学家团队尚未统一该成分的物理定义和建模边界，本报告不据此评价规则，也不优化 `q < 0.17` 门。Fourier m=1 的 34 个缺失机会没有一次直接命中，而 4 次新增全部发生在专家终态不含 Fourier m=1 的 `obj_2758`，说明当前 m=1 阈值／混淆判定的方向性需要专项复核。

## VLM 的实际作用

与 numeric-only 相比，21 轮动作变化中有 19 轮从新增变为 KEEP，另 2 轮仍为新增但更换了成分或 candidate。19 次抑制新增可拆为：

- 13 次抑制终态外新增，方向上有利。
- 6 次抑制当轮缺失的专家终态成分，方向上不利。

因此 VLM 的主要效果是提高保守性，而不是稳定提高方向一致性。它对不同标签的行为也不均衡：

| central 标签 | 出现轮数 | 专家终态含对应结构 | 终态外 |
|---|---:|---:|---:|
| `disk_like` | 37 | 37 | 0 |
| `spiral_arm` | 4 | 4 | 0 |
| `edge_on_disk` | 24 | 6 | 18 |
| `spheroid_like` | 18 | 12 | 6 |
| `central_compact_excess` | 12 | 8 | 4 |
| `peanut_x` | 1 | 0 | 1 |

这仍然只是终态方向对照，不是形态标签真值。`spheroid_like` 行是历史兼容标签统计，不代表新方案会继续使用；`edge_on_disk` 行保留作完整性记录，不纳入本轮 VLM 性能或优化结论；唯一 `peanut_x` 出现在终态无 Bar 的轮次，仍值得复核。

`central=diffraction_psf` 在 22 个成功解析轮次出现，其中 20 轮最终 KEEP，14 轮当时仍缺终态成分。它触发 10 次中心形态冲突和 4 次 Bar 衍射冲突，是 VLM 改变动作的主要机制。当前标签没有绑定具体 band、panel 和 Bar 尺度，因此相同规则既能在 `obj_1502` 避免终态外 Bar，也会在 `obj_331` 压制终态内 Bar。

## 代表性轮次预审

第一批选择 16 轮，覆盖四类解析失败、方向一致新增、终态外新增、VLM 有利／不利抑制和规则优先级冲突；原选取的 Edge-on 案例只作范围外记录。按用户最新指示，不再继续 comparison PNG 视觉复核；以下分类仅基于 numeric evidence、VLM evidence、rule trace、decision artifact 和专家终态标签。

| 轮次 | 动作 | 预审分类 | 关键判断 |
|---|---|---|---|
| `obj_170` initial | Add Bulge | provisional TP | 四个高 SNR 波段中心源可分辨，且 Bulge 属缺失终态成分 |
| `obj_1071` iter2 | Add Bar | provisional TP | F277W、F150W 通过强等照度与显式 PSF 方向门 |
| `obj_1639` iter2 | Add Disk | provisional TP | N1／N2／N3 与 `disk_like` 同时成立 |
| `obj_1429` initial | KEEP | provisional FN | 中心过量和 F410M resolved 被全局 diffraction 标签压制 |
| `obj_28` initial | KEEP | out of scope | Edge-on Disk 科学定义未统一，不评价 q 门或动作正确性 |
| `obj_331` iter2 | KEEP | provisional FN | F115W 强 Bar 数值证据被未定位的 diffraction 标签压制 |
| `obj_1502` iter5 | KEEP | provisional TN | 终态方向正确，但与四个 PSF-cleared Bar 数值波段存在冲突 |
| `obj_163` iter5 | KEEP | provisional TN | 当前集合精确，数值层无新增结构支持；解析失败不改变方向 |
| `obj_2185` iter2 | KEEP | evidence insufficient | 多项派生特征不可用，VLM 又违反低质量标签规则 |
| `obj_1825` iter3 | Add Companion trial | evidence insufficient | 七波段候选存在，但 VLM 没有观察 candidate_9 |
| `obj_1429` iter2 | Add Companion trial | high FP risk | 当前集合精确，只有弱候选，VLM 原始标签为 uncertain |
| `obj_1803` iter10 | Add Companion trial | high FP risk | 当前集合精确，七波段候选 SNR 均偏弱，raw response 截断 |
| `obj_1071` iter5 | Add Companion | high FP risk | VLM 无 candidate 映射却把全部六个候选标为独立源 |
| `obj_1398` iter4 | Add Bar | high FP risk | 所有 Bar profile 的 `psf_veto=null`，动作依赖唯一低置信 peanut 标签 |
| `obj_2758` iter1 | Add Fourier m=1 | high FP risk | A1 过阈值且无混淆，但终态无 m=1，仍需视觉与重拟合验证 |
| `obj_1833` iter1 | Add Bulge | competing explanation | 中心 resolved 证据真实，但终态是 Bar＋Companion＋m=1，可能是规则优先级导致的替代解释 |

逐轮数值摘要、VLM 评价、规则使用评价和分类依据均记录在结构化 review JSON 中。

## 当前判断

新方案已有三个有效特征：数值证据可追溯，VLM 失败能够受控降级，新 VLM 输入已由数值层 candidate overlay 提供可验证的位置映射。历史结果仍受旧 candidate 映射缺失、旧中心标签契约和全局 diffraction 标签影响；后者是当前唯一需要科学家裁决的证据优先级问题。

这些结果不足以支持调整正式 workflow 或阈值，也不应把 `48 PROPOSE_ADD` 直接作为候选发现数。更合理的解释是：32 个直接新增、13 个弱候选 trial fit、3 个 PARSE_FAILED 后的数值重试新增；其中 Companion 和终态缺失但 KEEP 是后续评测优先级最高的两组。

## 复核范围说明

按用户最新指示，本阶段不再复核 16 个代表轮次的 comparison PNG。结构化 JSON 中的 `visual_support=not_assessed` 表示该证据维度未纳入本次范围，不是剩余阻塞；报告不把 VLM 输出等同于人工看图结论。所有 provisional 分类的限制来自终态标签不是逐轮动作标签，以及没有通过重拟合验证新增成分。
