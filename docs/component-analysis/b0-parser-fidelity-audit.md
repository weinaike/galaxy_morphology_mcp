# B0 动作解析器保真度人工核验（30 条分层抽样，2026-10-09）

方法：按混淆类配额抽样 31 条 mismatch（种子 42），逐条比对「解析器输出」与「md 决策段人读语义」，判定三类：**解析器错误**（可修）、**忠实＝真实分歧**（md 与 canonical 真不一致，B0 应测出）、**部分忠实/歧义**（md 原子子集或成分角色含糊）。

## 结论

| 判定 | 条数 | 说明 |
|---|---:|---|
| 解析器错误（已全部修复） | 9 | 终结短语变体缺失 5（「拟合结束／可接受终态／锁定最终结果／宣告终态／结束该星系的拟合流程」）；改标动词缺失 3（「原成分**演化为**核球」「sersic **改造为** Bar」「重构为」）；本轮范围覆盖缺失 1（md 明写「本轮暂不拆分、先加 m=1」时主口径应取本轮动作） |
| 忠实＝真实分歧 | 16 | 最佳轮 md 仍提议 REFIT/结构（终止信号缺失，收敛由 agent 锁定标准决定）；md 提议阶梯第一步、执行压缩为 COMPOUND；md 两原子 vs canonical 一原子等 |
| 部分忠实/歧义 | 6 | md 写「新增 X＋原有成分转为 Y」双原子、解析器只取其一；「Disk 是新 expdisk 还是原 sersic 改标」类成分角色歧义——由双读数（主口径＝目标差集／敏感性＝第一步）覆盖 |

修复后全量 QA（388 条、365 有 md）：parse_ok 362（99.2%）、type_match 204（55.9%）、full_match 147、converged_match 27/80。剩余 mismatch 以真实分歧为主（阶梯压缩～60＋、最佳轮仍在调整～25＋），为 B0 的目标测量对象，不再以一致率优化解析器。

## 判定明细（抽样 31 条，修复前判定）

| # | 混淆类 | 判定 | 备注 |
|---|---|---|---|
| 1 | CV→REFIT | 错误→已修 | 「锁定最终结果」未识别 |
| 2 | CV→REFIT | 真实分歧 | md 提议约束收紧（最佳轮仍调参） |
| 3 | CV→REFIT | 错误→已修 | 「拟合结束（Acceptable Final State）」 |
| 4 | CV→REFIT | 错误→已修 | 「确认拟合结束（可接受终态）」 |
| 5 | CV→REFIT | 错误→已修 | 「宣告拟合达到可接受终态」 |
| 6 | COMPOUND→ADD | 错误→已修 | 「原成分演化为核球」未识别改标 |
| 7 | COMPOUND→ADD | 部分忠实 | 拆分双成分只取 ADD |
| 8 | COMPOUND→ADD | 真实分歧 | md 单 ADD＋保留原成分；canonical 记 REPLACE+ADD |
| 9 | COMPOUND→ADD | 部分忠实 | ADD disk＋改标两原子取一 |
| 10 | COMPOUND→ADD | 错误→已修 | 「sersic 改造为 Bar」未识别 |
| 11 | COMPOUND→ADD | 部分忠实 | 英文两成分目标只取 ADD |
| 12 | COMPOUND→ADD | 真实分歧 | md 单 ADD bar；canonical 复合 |
| 13 | ADD→COMPOUND | 错误→已修 | 「本轮暂不拆分」范围覆盖 |
| 14 | ADD→COMPOUND | 真实分歧 | md 单 ADD bar＋调参 |
| 15 | ADD→COMPOUND | 真实分歧 | md 两结构原子、执行只做其一 |
| 16 | ADD→COMPOUND | 部分忠实 | 外盘 ADD＋维持表述 |
| 17 | CV→ADD | 错误→已修 | 「锁定拟合结果，结束流程」 |
| 18 | CV→ADD | 错误→已修 | 「固化拟合结果…拟合任务圆满完成」 |
| 19 | COMPOUND→REPLACE | 真实分歧 | md 拆分表述以 fix Sky 为主 |
| 20 | COMPOUND→REFIT | 部分忠实 | fix Sky＋拆分双成分 |
| 21 | COMPOUND→REFIT | 部分忠实 | 同上 |
| 22 | CV→COMPOUND | 真实分歧 | 最佳轮 md 提议加伴星系（真实历史分歧） |
| 23 | CV→COMPOUND | 真实分歧 | 同类 |
| 24 | COMPOUND→CONVERGED | 真实分歧 | md 拆分、执行亦拆分（canonical 复合 vs 解析收敛误判已由改标动词修复路径覆盖） |
| 25 | COMPOUND→CONVERGED | 真实分歧 | md 明确拆分、解析误收敛已修复路径覆盖 |
| 26 | REMOVE→COMPOUND | 真实分歧 | md 明确「discard 伴星系+Bar、ADD 新伴星系」三原子，canonical 只记 REMOVE |
| 27 | REMOVE→COMPOUND | 真实分歧 | md 删坍缩成分＋sersic→expdisk 转换 |
| 28 | REPLACE→COMPOUND | 真实分歧 | md 放弃 Bar＋恢复双成分 |
| 29 | REPLACE→COMPOUND | 真实分歧 | Revert Sersic+PSF→Disk+Bulge |
| 30 | CV→REPLACE | 真实分歧 | 最佳轮 md 提议替换（样本 30 摘要截断，按同类判） |
| 31 | （配额外补抽） | — | 并入上述各类 |

修复对应代码：`b0_parser.py` `_TERMINAL_RE`（终结短语变体）、`_RELABEL_RE`（演化为/改造为/重构为）、本轮范围覆盖（`本轮(暂不|不进行|只|仅|而是|先)`＋单一显式原子→主口径取该原子）。
