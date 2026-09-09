
要求严格遵循工作流开展星系拟合分析工作。
关注拟合盘、核球、棒、AGN核、偏心、伴星系和 Lens 等星系成分；Edge-on Disk 不在本次优化范围内，其他高级残差特征可以选择保留不拟合
图像分析与拟合执行只能使用 galmcp 中的工具。严禁使用4_5v_mcp的相关工具
系统规定的规范、`resolved_decision`、共享 verifier artifact 和 component_analysis 的证据是你需要遵循的，严禁私自更改。客户端 subagent 只能作为 `workflow_verify_best_round` 的适配层，不能自行产生落锁结论。

## v2 workflow bridge 规则

- 只有 `COMPONENT_ANALYSIS_WORKFLOW_PILOT=1` 显式开启时才消费本节的结构化闭环；未开启时保留旧 workflow 的行为，不把 proposal-only shadow 当作正式拟合闭环。
- 每轮必须使用 `build_workflow_round_manifest` 传入当前 feedme、实际 `run_galfit` 返回的 result FITS、summary、comparison PNG 和 working note 的明确路径。禁止用目录 glob 或文件名猜测当前轮次。
- `decision_artifact.resolved_decision` 是唯一机器动作来源；`raw_decision`、component_analysis Markdown 和 Working Note 只用于解释和审计。三者冲突时不能自行合并动作。
- Agent／VLM 只能在规则给出的 `candidate_actions` 内排序、解释证据冲突和提供结构化 `parameter_plan`；不得创建候选集合外的成分、动作、坐标或直接写入 feedme。
- `component_analysis` 返回的 Markdown 通过 `workflow_analysis_artifact` 与本轮 decision 绑定，只作为人类可读证据；该接口不得从 Markdown 解析第二个动作。
- 使用 `workflow_action_config` 通过 preflight 后生成新的 `_iterN` feedme；原始 feedme 不得修改。随后只调用现有 MCP `run_galfit`，不得在 shell 中运行 GALFIT。
- MCP 返回后立即调用 `record_workflow_fit_lifecycle`，记录 raw／resolved、candidate／executed action、fit result、refit verdict、PolicyState 和下一步。
- 每个实际执行的候选拟合都必须调用 `workflow_evaluate_refit`；只接受其结构化 `ACCEPT_REFIT`／`REJECT_REFIT`／`STOPPED_NEEDS_REVIEW` 结果，不从 Working Note 推断 refit verdict。
- 一轮最多执行一个结构动作；`PROPOSE_ADD`、`PROPOSE_REPLACE`、`PROPOSE_REMOVE` 和 `REFIT_PARAMETERS` 完成后必须回到下一轮分析。`PROPOSE_REMOVE` 默认 review-only，Edge-on Disk 不进入本次优化。
- `CONVERGED` 只是锁定候选，必须先调用 `workflow_verify_best_round`，再用 `workflow_lock_best_round` 提交可核验的 verifier artifact；`STOPPED_NEEDS_REVIEW` 停止自动成分循环，但已有有效拟合时仍完成报告并标记 `COMPLETED_WITH_REVIEW`、`UNLOCKED`，没有有效拟合时标记 `FAILED_NEEDS_REVIEW`。
## workflow

阶段一. 查看星系目录与原图分析
* **查看星系目录：** 确认所需的文件是存在的，包括 FITS 图像、掩膜文件、背景估计文件、feedme 文件等。
* **查看原始数据与图像：** 使用 render_original生成原图、view_original_image分析生成图、detect_bar_lopsidedness工具检测成分，综合分析确认星系的初始基本形态特征（星系类型与成份预测）。这将为后续的拟合提供初始猜测值。

阶段二. 结构搜索与动态校验 (Structure Search & Dynamic Validation)
*目标：确定星系包含哪些主要物理成分（盘、核球、侧视盘、棒、AGN核、偏心、伴星系等），不追求参数极致收敛。拟合过程采用`自下而上`，完成一个成分拟合后、再考虑增加新成分。* 
* 步骤1. 拟合与调参：使用现有 MCP `run_galfit`，执行星系拟合（要求输入配置文件绝对路径），
  - 请查看日志确认拟合是否正常。如果拟合异常，需要调初始值与调约束（调初始值优先级高于增加约束；非必要不加.cons(x,y除外)），重新拟合, 才能进入步骤2。
  - 拟合成功后保存 MCP 返回的实际路径，并用 `build_workflow_round_manifest` 建立本轮 manifest。
* 步骤2. **成分调整：** 使用`component_analysis`生成 Working Note 的人类可读分析；机器动作必须来自经过 schema 校验的 `resolved_decision`，再调用 `workflow_action_preflight` 和 `workflow_action_config`。
    - 每次调用`component_analysis`前，需要创建或者更新前当前星系目录下的 `working_note.md`, 将上一轮次的`成分分析要点`、`拟合目标`和`拟合进展`形成摘要加到文件末尾。
    - 针对同一个源多轮拟合， 所以要求woking_note 所记录的`拟合目标`要求有连续性，基于新的判断或者证据，目标调整需要说明。要求保持拟合目标的持续性和一致性，避免频繁大幅调整目标导致分析的混乱。

重复执行上述1-2步骤，直到结构化决策返回 `CONVERGED`，并且当前参数、残差、物理条件和 verifier 审计均满足后，才能锁定最佳轮。没有动作但仍有 review 标记时，不得伪装成收敛。

* 步骤3. 物理意义分析 与 奥卡姆剃刀原则 过滤不物理的情况
    - 物理意义分析：严格遵循"物理意义分析与策略"，逐条分析成分参数是否符合物理意义。
        - 如果出现不物理的情况，需要调整成分类型，回到`步骤2`重新拟合。
    - “奥卡姆剃刀原则”：控制成分的复杂的，如无必要、勿增实体。（主要针对伴星系/独立的 AGN, 不能针对星系的Disk/Bulge/Bar这些主成分使用奥卡姆剃刀。这些主成分主要考虑残差因素在残差质量。而非 BIC）
        - `PROPOSE_REMOVE` 在真实局部残差事实和 remove pilot 通过前只能记录为 review-only，不得自动删除。
    - 如果已无新的可能，进入步骤4。

* 步骤4. 锁定最佳结果与物理意义解读
    - 锁定最佳结果
        - 依据<最优轮次锁定的标准>进行轮次选择，要求分析 “成分条件”、“拟合条件”、“物理条件”、“参数条件”、“校验条件”和“指标条件”，选择候选最优轮次。
        - 需要详细说明显选择理由，并给出其对应的形态学物理意义（如：成分 A 代表经典的盘结构，成分 B 代表致密的核心星团）。
        - **正式落锁前，必须调用 `workflow_verify_best_round`** 对候选轮及结构化 lifecycle 证据做独立审计；**`FAIL` 或 `INCONCLUSIVE` 严禁落锁**。只有 verifier artifact 的 `verdict=PASS` 且 `lockable=true` 时，才能调用 `workflow_lock_best_round`。

阶段三. 科学目标校准与报告撰写
* 科学目标校准：科学目标关心偏心的影响，如果最佳结果不包含Fourier成分，调用 fourier_mode_analysis 对残差图进行分析判断是否需要采用 fourier mode 对偏心非对称残差拟合。如果已经包含Fourier成分，则跳过该步骤。
    - 只能使用 1 阶 Fourier 模式进行拟合补偿。且只能作用于 Disk 成分 或者 是单 Sersic 模型（如果没有 Disk 成分）。
    - F1 成分的 amplitude 大于 0.02，且拟合质量没有恶化 就可以保留；更新最佳轮次为带 F1 的最新轮次。否则放弃 F1 成分，保持原来的最佳轮次。

* 报告撰写：使用当前客户端可用的文件写入能力保存报告；文件名和路径必须写入 lifecycle 或 handoff 证据，不能假定存在名为 `write_file` 的工具。
* **报告内容包含：**
    * **预处理信息：** 掩膜说明、背景设定依据。
    * **迭代过程流水账：** 每轮 raw／resolved action、numeric／VLM evidence 引用、参数发散与回退记录、refit verdict、PolicyState 和 `needs_review`。
    * **最佳结果锁定分析与结果：** 最佳结果的锁定理由、最终采用的参数表、物理意义解读。
    * **附件索引：** 最佳轮次的目录路径、最终的 `feedme` 文件、最终的拟合对比图（原图/模型/残差）路径。
    * **json格式化输出：** 在文档最后格式化输出轮次信息，便于规则提取，自动化处理。格式如下：
    ```json
    {"best_turn":"<最佳轮次的目录名>","components":["<最优轮次包含的哪些物理成分>"],"galaxy_type":"edge-on/face-on/elliptical"}
    ```
    其中 best_turn 的值为 archives/ 下最佳轮次子目录的名称（如 20260414T093323.c1993a48）。
    物理成分类型：[Disk,Bulge,Bar,Nucleus,Companion,Fourier,SingleSersic,Lens]
    galaxy_type:可选的是正向盘、侧向盘、椭圆星系三种。即：[edge-on,face-on,elliptical],如果盘星系的 Disk成分的 q<0.3，则认定为侧向盘
    

    
## 待分析星系

{argument}
