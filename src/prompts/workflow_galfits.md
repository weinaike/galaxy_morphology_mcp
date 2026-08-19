
要求严格遵循工作流开展多波段(multi-band)星系拟合分析工作。

仅关注拟合盘、核球、侧视盘、棒、AGN核、偏心（Disk 上的 m=1 Fourier 模式）、伴星系这七种物理成分，仅可对这七种成分的残差添加模型成分拟合，其他残差特征可以选择保留不拟合。其中偏心（Fourier m=1）的添加由阶段一 detect_galfits_bar_lopsidedness 的检出结果驱动：lopsidedness 检出时，应作为最高优先级修正项参与阶段二的结构迭代（在增加其他成分之前先加 m=1）；未检出时进入阶段三再由 fourier_mode_analysis 做残差二次确认。
图像分析与拟合执行只能使用 galmcp 中的工具，不能直接 shell 执行 GalfitS 命令。所有 GalfitS 拟合必须使用 `--fit_method ES`。严禁使用4_5v_mcp的相关工具。
必须建立 todos 并独立完成所有阶段，直到 Image-SED 联合拟合成功。

## workflow

阶段一. 查看星系目录与原图分析
* **查看星系目录：** 确认所需的文件是存在的，包括 FITS 图像、掩膜文件、背景估计文件、lyric配置文件等。
* **查看原始数据与图像：** 使用 render_original, view_original_image, detect_galfits_bar_lopsidedness 工具分析原图，确认星系的基本形态特征（如是否存在明显的核球、盘、棒结构等）。这将为后续的拟合提供初始猜测值。
* **detect_galfits_bar_lopsidedness 结果解读与固化：**
    - 工具返回结构为 `{"results": [{band, bar:{detected, pa_deg, b_over_a}, lopsidedness:{detected, mag, phase_deg}}, ...]}`，按波段列表。
    - **跨波段 OR-logic**：任一波段 `bar.detected=True` → 认定 Bar 存在；任一波段 `lopsidedness.detected=True` → 认定偏心存在，作为阶段二高优先级添加 Fourier m=1 的依据。
    - **PA 取值规则**：Bar 的 PA 优先取蓝端波段（如 F115W）的返回值；红端（F200W/F444W）作参考。
    - **偏心添加决策**：任一波段 `lopsidedness.detected=True` → 在 `working_note.md` 头部标记 "m=1 Fourier 高优先级"，阶段二每次调用 `analyze_multiband_components` 前需提示该标签，确保在 Disk 已建立后第一时间将 Disk 的 `Pa2) sersic` 改为 `sersic_f`。
    - **写入 working_note.md 头部**：将每波段 bar/lop 检测结论、PA、b/a、A1、phi1 固化到 `working_note.md`，供后续所有迭代轮次的 `analyze_multiband_components` 读取。

阶段二. 结构搜索与动态校验 (Structure Search & Dynamic Validation)
*目标：确定星系包含哪些主要物理成分（核球、盘、棒、点源等），不追求参数极致收敛。拟合过程采用`自下而上`，完成一个成分拟合后、再考虑增加新成分。* 
* 步骤1. **拟合与调参：** 调用 `run_galfits_image_fitting`，完成一次多波段的形态拟合（要求输入配置文件绝对路径）。
    - 请查看日志确认拟合是否产出可用于分析的 summary 与对比图。如果工具调用失败或未产出有效结果，需要调初始值与调约束（调初始值优先级高于增加约束；非必要不加.cons）后重新拟合。（本步骤只调参数和约束、不改变成分，不改变拟合区域。）
* 步骤2. **成分调整：** 对每一次成功产出 summary 与对比图的拟合，必须使用 `analyze_multiband_components` 分析当前模型的成分配置，获取成分增删和参数调整策略；严格遵循策略，修改配置，执行步骤1。
    - 每次调用 `analyze_multiband_components` 前，需要创建或者更新当前星系目录下的 `working_note.md`, 将上一轮次的`成分分析要点`、`拟合目标`和`拟合进展`形成摘要加到文件末尾。
    - 每次修改了.lyric文件，需要调用`check_lyric_file`检查lyric文件的格式是否正确，如果不正确，根据提示修复其中的错误

**严格要求：步骤1（拟合）和步骤2（成分分析）必须严格交替执行，不允许跳过任何一步。** 每次调用 `run_galfits_image_fitting` 完成拟合后，无论拟合结果好坏、无论参数变化大小，都必须立即调用 `analyze_multiband_components` 进行成分分析。只有成分分析工具的返回结果才能决定下一步动作——是继续调参、增删成分、还是进入步骤3。

重复执行上述1-2步骤，直到步骤2的 `analyze_multiband_components`工具 给出无需调整5类物理成分(盘、核球、棒、AGN、伴星系)，且当前参数已经合理收敛后，才能进入步骤3. 

* 步骤3. 物理意义分析 与 奥卡姆剃刀原则 过滤不物理的情况
    - 物理意义分析：严格遵循“物理意义分析与策略”，逐条分析成分参数是否符合物理意义。
        - 如果出现不物理的情况，需要调整成分类型，回到`步骤2`重新拟合。
    - “奥卡姆剃刀原则”：控制成分的复杂的，如无必要、勿增实体。
        - 删除成分后，需要重新走一遍步骤1-2，探索其他可能性。 
        - 如果已无新的可能，进入阶段三

阶段三. 结果分析与报告撰写
* 锁定最佳结果（选择模型中参数符合物理意义且参数收敛的最优轮次）。并给出其对应的形态学物理意义（如：成分 A 代表经典的盘结构，成分 B 代表致密的核心星团）。
* **偏心成分（Fourier m=1）评估**：科学目标关心偏心的影响。
    - 如果最佳结果的 Disk 成分已经是 `sersic_f`（阶段一 lop 检出后于阶段二已添加），跳过本步。
    - 如果阶段一 lop 未检出但仍有疑虑：调用 `fourier_mode_analysis`，输入图为最佳轮次 **F200W 波段**的对比 PNG（原图/模型/残差），分析是否存在 m=1 傅里叶模式可修正的偏心非对称残差。工具返回 recommend_fourier=yes → 把 Disk 的 `Pa2) sersic` 改为 `sersic_f` 并设置 `Pa21) 1` 等参数（详见 component_specification_galfits.md），回到阶段二步骤 1-2 重新拟合。
    - m=1 Fourier 成分的保留/移除判据遵循 `analyze_multiband_components` 的策略输出，只有证明 Fourier 成分导致拟合不合理（参数发散、物理不成立）时才删除。
* 使用 `write_file` 工具将分析结论写入当前星系目录：`analysis_report_xxx.md`。
* **报告内容包含：**
    * **生成时间：** 日期和时分秒。
    * **预处理信息：** 掩膜说明、背景设定依据。
    * **迭代过程流水账：** 每轮新增/删除成分的依据（多模态视觉判断记录）、参数发散与回退记录。
    * **最佳结果详情：** 最终采用的参数表、物理意义解读。
    * **附件索引：** 最佳轮次的目录路径、最终的 `lyric` 文件、最终的拟合对比图（原图/模型/残差）路径。
    * **json格式化输出：** 在文档最后格式化输出轮次信息，便于规则提取，自动化处理。格式如下：
    ```json
    {"best_turn":"<最佳轮次的目录名>","components":["<最优轮次包含的哪些物理成分>"]    }
    ```
    其中 best_turn 的值为 output/ 下最佳轮次子目录的名称（如 20260414T093323.c1993a48）。
    物理成分类型：[Disk,Bulge,Bar,AGN,Fourier,Companion]

阶段四. SED拟合
SED拟合通常需要基于最优的Image拟合（在阶段三中已经确认）：
* 调用`run_galfits_sed_fitting`进行SED fitting。注意：`run_galfits_sed_fitting`使用的配置文件是最优的Image fitting使用的配置文件，同时，由于SED拟合需要基于image fitting得到的星系成分参数进行质量估计，需要指定最优image fitting对应的输出目录（通常是output目录下的某个子目录，例如20260414T093323.c1993a48）
    - SED拟合时不能传入约束文件，因为它基于单个成分进行拟合，且空间参数被固定
    - SED拟合只需要成功拟合一次即可
    - 若SED拟合失败，需要分析原因并重新SED拟合
    - SED拟合成功后，会生成一个新的配置文件，该文件是Image-SED联合拟合的输入文件

当SED拟合成功后，针对新生成的配置文件，需要检查其中每个成分（比如Px，x=a,b,c, ...）的Px9, Px11, Px12, Px14中五元组中的初值是否落在该五元组中指定的范围内，若不在该范围内，输出警告信息并结束，否则进入阶段五    

阶段五. Image-SED联合拟合
* 调用`run_galfits_image_sed_fitting`对image和SED进行一次联合拟合，输入配置文件是SED拟合成功后生成的配置文件            
    - 如果最佳的image-fitting使用了--parconstrain，那么run_galfits_image_sed_fitting也需要加载同一个约束文件
    - Image-SED拟合只需要成功一次即可
    - 若Image-SED拟合失败，需要分析原因并重新Image-SED拟合
    - Image-SED联合拟合成功后，标志着当前星系的拟合任务完成

当Image-SED联合拟合成功后，需要对比生成的png图像与Image拟合中最佳一轮中生成的png图像，如果两张图像的星系成分存在较大差异，输出对应的警告信息。

    
## 待分析星系

{argument}
