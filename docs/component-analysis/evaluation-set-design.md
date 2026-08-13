# 成分分析评测集设计（v0）

> 本文档记录 2026-08-04 关于「成分分析评测集」的设计讨论，供后续 session 接续。
> 状态：**设计阶段，未实现**。当前仅定 spec 与 4 条样例（来源 jwst0716）。

---

## 1. 背景与动机

**目标**：用科学家真实拟合轨迹作为 ground truth，评测（后续优化的）成分分析方法/工具。输入 = 某一轮的拟合产物（state），输出 = 下一轮决策（action）。

### 触发本工作的对比发现（jwst0716 vs jwst0731）

- jwst0716 = 有 VLM 的拟合批次；jwst0731 = VLM API 401 全断、走 `result.fits` 数值降级的批次。
- 以科学家确认结果为 ground truth，7 个交集星系（2185/2758/365/163/331/1639/1803）对比：**0716 错 2/7，0731 错 2/7，错率持平**。
- 关键发现：**VLM 在这批 JWST 数据上边际价值近零甚至为负**——形态判断多 uncertain；analyzer 反复报伴星系幻觉（365/1803/163/331，全被 catalog 否决）；0716 最终决策同样靠 BIC + catalog + 简并检测。
- 降级方案（memory: `galmcp-vlm-down-fallback-residual-analysis`）在无视觉条件下达到同等准确率；价值在鲁棒性与规避幻觉，不在更准。
- 降级方案两类失误的根因：1803 强制 face-on（q=1 固定）掩盖 bar 简并；1639 把致密点源核当普通 bulge——都是「中心是否点源状、盘是否 face-on」缺数值判据。

### 评测对象

「成分分析」= 看本轮结果 + 残差 → 决定下一轮该加/删什么成分、调什么参数、或锁定。目前由 agent / VLM 工具 / 数值降级方案执行，**输出是自由文本建议，尚不可评测**。评测集要把它变成可量化对象。

---

## 2. 基础设定：模仿学习（imitation learning）setting

本评测属模仿学习范式：用专家演示（科学家轨迹）评测决策策略。

- state→action 数据集 = 行为克隆（behavior cloning）数据集
- **已知陷阱 1：误差累积（compounding error）** —— 单步 90% 正确，串 10 步剩 0.9¹⁰ ≈ 35%；故单步好 ≠ 端到端好。
- **已知陷阱 2：分布漂移（distribution shift）** —— 方法偏离科学家动作后进入未见 state；应对靠 rollout 评测（让方法在自己的决策后果里跑）。
- **已知陷阱 3：路径非唯一** —— 同一 state 多个合理动作；医疗 AI 用「合理集合」+ 多专家共识。

---

## 3. Spec

### 3.1 action space（动作枚举）

- `ADD(component)`：component ∈ {bulge, bar, m1_fourier, companion, nucleus, edgeondisk}
- `REMOVE(component)`
- `FIX(param, value)`，如 `FIX(bar.n, 0.5)`
- `RELEASE(constraint)`，如释放同心约束
- `LOCK`（锁定，不再增删）

### 3.2 state bundle（输入字段，representation 留给被测方法）

- 本轮 `.lyric` + `.gssummary`（参数、BIC、per-band χ²）
- `result.fits` 各 HDU（HDU0=residual / HDU1=mask / HDU2=sigma / HDU3=model / HDU4=data）
- detect 阶段一结论（bar / lop 跨波段表）
- SExtractor catalog + segm
- 到本轮为止的 working_note

**不预设**把残差转成数值特征或 PNG：VLM 方法自己看图，数值方法自己读 HDU，label 不绑 representation，两类方法公平比较。

### 3.3 存储格式：单步条目

基本单元 = 单步条目 `(state_bundle, expert_action)`。一个星系 N 轮 → N 条单步条目（前 N−1 条 state→action，末条 state→LOCK）。**整条轨迹不是存储格式，是评测用法。**

### 3.4 label 分层

- **硬 label（终态）**：科学家确认的最终成分结构 + 每成分参数合规（不撞界、不简并）。
- **软 label（中间步）**：某 state 的「合理动作集合」，科学家动作是其中一元素，不是唯一答案。

---

## 4. 评测指标（双层，粗粒度）

**单步**：
- 方向正确率：ADD/REMOVE/LOCK 方向对不对
- 合理集合命中：动作落在合理集合内

**端到端（rollout，方法自主跑到收敛）**：
- 成分 F1（漏检/多检）
- 参数合规率（不撞界、不简并）
- 收敛轮次（效率）
- **拒绝率**（该 LOCK 时 LOCK 了没）—— 重点，大多数错误是过度添加

> error taxonomy（散落于各 `galfits-*` memory）不作为主指标，降级为可选诊断报告：被测方法在主指标犯错后，再用它分类错因。

---

## 5. 样例条目（来源 jwst0716，已读 4 个，各测一个核心决策点）

### ① obj2185 — 测「拒绝」

- 终态 label：`Disk`（单盘，n=1 固定，Re=0.156″）
- 轨迹：
  - R1 单盘 BIC=15969，残差纯随机 →
  - R2 固定 n=1 BIC=15959 →
  - R3 `ADD(m1)` BIC=16011（升）→ `REMOVE(m1)`
  - R4 `ADD(bulge)` BIC=16059 + b/a 撞界 → `REMOVE(bulge)`
  - `LOCK` R2
- 测点：方法敢不敢在 R3/R4 拒绝添加、直接 LOCK。

### ② obj365 — 测「抗伴星系幻觉」+「释放同心」

- 终态 label：`Disk(sersic_f+m1) + Bulge`（disk n=1, bulge n=3.92, m1 am=0.05）
- 轨迹：
  - R1 单盘 BIC=299556 →
  - R2 `ADD(m1)` BIC=296582 →
  - R3 `ADD(bulge)` + 同心 BIC=275026，但 Mag_disk==Mag_bulge 简并 →
  - R4 `RELEASE(concentric)` BIC=275035 → `LOCK`
- 测点：每轮 analyzer 都报「右下方明亮伴星系」（catalog 第二亮源已暗到 mag=25.33），方法要敢不 `ADD(companion)`；且要识别同心简并、主动 `RELEASE`。

### ③ obj163 — 测「加 bar」

- 终态 label：`Disk(sersic_f+m1) + Bulge + Bar(n=0.5固定)`，Re 层级 disk(0.68) > bar(0.41) > bulge(0.22)
- 轨迹：
  - R1 单盘 BIC=205668 →
  - R2 `ADD(m1)` BIC=205575 →
  - R3 `ADD(bulge)` 同心 → 简并 →
  - R5 `RELEASE(concentric)` BIC=199260 →
  - R9 `ADD(bar)` + `FIX(bar.n,0.5)` BIC=198309（ΔBIC=−239）→
  - R10 `ADD(companion)` 测试 → `REMOVE`
  - `LOCK` R9
- 测点：阶段一 detect 全波段未检出 bar，但 R9 实拟合 ΔBIC=−239 且 bar 参数物理（q=0.42 棒状）。方法要敢在 detect 阴性时仍试 `ADD(bar)` 并用 BIC + 参数合规仲裁。

### ④ obj331 — 测「删 bar」

- 终态 label：`Disk + Bulge`（disk n=1, bulge n=2.48），无 bar
- 轨迹：
  - R1 单盘 BIC=106221 →
  - R2 `ADD(bulge)` BIC=99899 →
  - R3 `ADD(bar)` BIC=99338，但 bar b/a=0.225 撞界、bulge 退化 →
  - R4 同心约束 BIC=100032，三成分 Mag 全等崩溃 →
  - R5 收紧 bar BIC=99625，仍撞界 →
  - R6 `REMOVE(bar)` BIC=99884 →
  - R7 `ADD(m1)` am=0 → `REMOVE(m1)` → `LOCK` R6
- 测点：detect 6/7 波段强检出 bar（强误导），但三次拟合都撞界/简并。方法要敢违背 detect、`REMOVE(bar)`，不被 detect 绑架。

---

## 6. 待决问题（下个 session 接续）

1. **ground truth 是单科学家还是多科学家**？多则可统计同一 state 的动作分布，界定合理集合；目前 0716 似乎是单轨迹。
2. **held-out 划分**：0716 剩余星系怎么切训练/验证/测试；是否跨场（jwst0731 及其他）扩充。
3. **是否把 4 样例落成 `eval_set_v0.jsonl`**（每行一条单步条目，一个星系 5–10 条）。
4. **单步条目的「合理集合」如何界定**：单轨迹下只能用「方向正确性」，多轨迹才能用集合命中率。
5. **评测集规模**：34 星系 × ~6 轮 ≈ 200 条，偏小；考虑人工构造对抗 state（如故意造缺 bulge 的残差）。
6. **降级方案的两条数值判据**是否先补进 memory/CLAUDE.md：① 中心成分 q>0.8 且 n 收敛到高值 → 用点源代理；② disk 撞 q 上界时先核对 detect 倾角，禁止无依据固定 face-on。

---

## 7. 关联

- 降级方案 memory：`galmcp-vlm-down-fallback-residual-analysis`（尚未固化进项目 CLAUDE.md）
- 0716 vs 0731 对比原始报告：
  - jwst0716：`{2185,2758,365,163,331,1639}/analysis_report*.md`；1803 仅 working_note（`jwst0716/1803/working_note.md`，iter10 无正式报告）
  - jwst0731：`{2185,2758,365,163,331,1639,1803}/analysis_report*.md`
- 104/1071/1118：0716 与 0731 均未完成（VLM 401 阻塞，降级方案建立前搁置），不可作为 ground truth。
