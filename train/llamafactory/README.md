# base SFT 训练（LLaMA-Factory，单轮多模态）

把 E7 系列 trajectory 转成 LLaMA-Factory 多模态 SFT 数据，训练 base 版模型（纯模仿学习，无 reward）。

- 结构：**单轮**（1 张父节点残差合成图 + 文字历史），与 E7 生成 / MCP main / 当前 pipeline 推理**零错配**。
- assistant 目标：完整 CoT + ```json``` 规格块（节点的 `full_response`）。
- reward、最终测评、多轮（UI-S1 风格）不在本阶段，见 `训练数据生成方案.md`。

---

## 1. 生成 SFT 数据（在 galaxy_morphology_mcp 根目录）

先划分测评集（若还没做），得到 `test_galaxies.json`：
```bash
python -m data_gen.extract_training_data --input output/<E7_full_strategy_folder>/
python -m data_gen.split_sft_train_test  --input-dir output/<E7_full_strategy_folder>/
```

再转成 LLaMA-Factory 格式（排除测评星系，按物理星系切 train/val）：
```bash
python -m data_gen.convert_sft_to_llamafactory \
    --input-dir     output/<E7_full_strategy_folder>/ \
    --test-galaxies output/<E7_full_strategy_folder>/test_galaxies.json \
    --out-dir       train/llamafactory/data \
    --max-steps 15 --val-ratio 0.01
```
产出：`galaxy_sft_train.jsonl` / `galaxy_sft_val.jsonl` / `convert_report.json`

> **残差图路径**：样本 `images` 用的是 trajectory 里的绝对路径。若训练机上路径变了，用
> `--image-root-from /旧前缀 --image-root-to /新前缀` 做前缀重映射。训练时这些图必须在本地可读。

**核对 `convert_report.json`**：
- `emitted` = 实际样本数；`skip_test_galaxy` = 被排除的测评星系样本数
- `fallback_spec` 应接近 0（否则说明很多节点缺 full_response）
- `skip_no_image` / `missing_summary` / `parse_component_fail` 应很小

## 2. 注册数据集

把 `dataset_info.json` 和两个 jsonl 放到同一目录（本仓库为 `train/llamafactory/data/`；
需要时把 `dataset_info.json` 从 `train/llamafactory/` 复制进去），训练时用 `--dataset_dir` 指过去；
或直接把三者拷进 LLaMA-Factory 的 `data/` 目录。

## 3. 训练

```bash
# 装环境（参考 UI-S1/README；LLaMA-Factory 另装）
# git clone https://github.com/hiyouga/LLaMA-Factory && cd LLaMA-Factory && pip install -e .

# 冒烟（先确认数据能加载、<image> 对齐、loss 正常）
llamafactory-cli train /path/to/qwen2_5vl_full_sft.yaml \
    dataset_dir=/path/to/train/llamafactory/data max_steps=5

# 正式训练
llamafactory-cli train /path/to/qwen2_5vl_full_sft.yaml \
    dataset_dir=/path/to/train/llamafactory/data
```

配置见 `qwen2_5vl_full_sft.yaml`：全参 SFT，显存紧张时按注释切 LoRA；`image_max_pixels` /
`cutoff_len` 控制 seq_len；`freeze_vision_tower: true` 省显存。

## 4. 合并/导出（LoRA 时）

```bash
llamafactory-cli export ...   # 参考 LLaMA-Factory 文档；全参训练无需合并
```

---

## 备注
- 版本兼容：`dataset_info.json` 用 `role/content/user/assistant/system` 的 `tags` 覆盖块（LLaMA-Factory
  OpenAI-format 用法）。若你的 LLaMA-Factory 版本偏老不认，可把数据转成经典 sharegpt（conversations/from/value/
  human/gpt）并去掉 tags 块。
- `deepspeed` 路径 `examples/deepspeed/ds_z3_offload_config.json` 相对 LLaMA-Factory 根目录；按实际调整。
- 多轮版（UI-S1 风格，≤2 图 + 全动作）将来另写独立转换脚本，读同一批 trajectory，不影响本流程。
