# simulator_env/galfit_env.py
import os
import shutil
import asyncio
import re

# 导入底层的模拟器 API (保持路径与你的项目一致)
from src.tools.run_galfit import run_galfit

# 导入我们的基类和 Action 工具
from data_gen.reward import crop_residual_panel
from simulator_env.base_env import BaseSimulatorEnv
from simulator_env.galfit_actions import apply_action_to_feedme, calculate_ssim

class GalfitEnv(BaseSimulatorEnv):
    def __init__(self, base_project_dir: str, max_iter: int = 100):
        """
        初始化 GALFIT 环境
        :param base_project_dir: 比如 /media/zhongling/wyh/S4G_data/ (用于解析绝对路径)
        :param max_iter: GALFIT 的最大迭代步数 (-imax)
        """
        self.max_iter = max_iter
        self.base_dir = base_project_dir

    def _extract_metrics(self, summary_path: str) -> dict:
        """
        从 run_galfit 输出的 summary.md 中提取物理指标
        包含: chi2_nu (约化卡方), chi2 (绝对卡方), ndof (自由度)
        """
        # 设置极差的初始值作为惩罚兜底，防止文件读取失败时误判为好结果
        metrics = {
            "chi2_nu": 9999.0, 
            "chi2": 999999.0,
            "ndof": 0
        }
        
        if not summary_path or not os.path.exists(summary_path):
            return metrics
            
        try:
            with open(summary_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
                # 1. 提取约化卡方 Chi^2/nu (核心 Reward 依据)
                chi2_nu_match = re.search(r'Chi\^2/nu\s*=\s*([\d\.]+)', content)
                if chi2_nu_match:
                    metrics["chi2_nu"] = float(chi2_nu_match.group(1))
                    
                # 2. 提取绝对卡方 Chi^2
                chi2_match = re.search(r'Chi\^2\s*=\s*([\d\.]+)', content)
                if chi2_match:
                    metrics["chi2"] = float(chi2_match.group(1))
                    
                # 3. 提取自由度 ndof
                ndof_match = re.search(r'ndof\s*=\s*(\d+)', content)
                if ndof_match:
                    metrics["ndof"] = int(ndof_match.group(1))
                    
        except Exception as e:
            print(f"⚠️ 指标提取失败 {summary_path}: {e}")
            
        return metrics

    async def initialize(self, init_config_path: str, output_dir: str, galaxy_id: str = None) -> dict:
        """
        为一棵新的搜索树打底 (Node 0)，使用短小精悍的相对路径防溢出！
        """
        os.makedirs(output_dir, exist_ok=True)
        root_feedme = os.path.join(output_dir, "node_0_root.feedme")
        
        # 1. 确定原数据的绝对目录
        source_dir = os.path.abspath(os.path.dirname(init_config_path))
            
        # 2. 读取原文件内容
        with open(init_config_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        # 3. 开始执行相对路径替换
        out_lines = []
        for line in lines:
            stripped = line.strip()
            # 拦截 A, C, D, F, G 路径配置行
            if stripped.startswith(("A)", "C)", "D)", "F)", "G)")):
                prefix = stripped[:2]
                content = stripped[2:].strip()
                
                # 分离路径和注释
                if "#" in content:
                    path_str = content.split("#")[0].strip()
                    comment = "#" + content.split("#", 1)[1]
                else:
                    path_str = content
                    comment = ""
                    
                if path_str.lower() not in ["none", ""]:
                    # 组装出原始数据的绝对路径
                    abs_source_path = os.path.join(source_dir, path_str) if not os.path.isabs(path_str) else path_str
                    
                    # 🚀 核心：计算出从沙盒目录 (output_dir) 指向原数据的【相对路径】
                    rel_path = os.path.relpath(abs_source_path, start=output_dir)
                    
                    # 将短小精悍的相对路径写进去
                    line = f"{prefix} {rel_path}    {comment}\n"
                    
            elif stripped.startswith("B)"):
                line = "B) node_0_root.fits      # Output data image block\n"
                
            out_lines.append(line)
            
        # 4. 把改好的相对路径写进沙盒文件
        with open(root_feedme, 'w', encoding='utf-8') as f:
            f.writelines(out_lines)
            
        print(f"🔄 初始化环境: 运行基准节点 {root_feedme}")
        
        # 5. 调用底层的跑图脚本 (底层脚本依然需要绝对路径才能找到这个 feedme)
        result = await run_galfit(os.path.abspath(root_feedme), ["-imax", str(self.max_iter)])
        
        if result["status"] != "success":
            return {"status": "failed", "error": result.get("error", "Init crashed")}
            
        summary_path = result.get("summary_file")
        metrics = self._extract_metrics(summary_path)
        
        # ✅ 修正：在初始化返回前，强制将 node_0_root 的全图也裁剪成纯残差图
        raw_root_png = result.get("image_file")
        try:
            cropped_root_png = crop_residual_panel(raw_root_png)
        except Exception as e:
            print(f"    ⚠️ [初始化裁剪警告] 基准节点图像裁剪失败，降级使用全图: {e}")
            cropped_root_png = raw_root_png
        
        return {
            "status": "success",
            "feedme_path": root_feedme,
            "residual_path": cropped_root_png, # 确保第一代父节点传出的就是裁剪后的干净路径
            "summary_path": summary_path,
            "metrics": metrics
        }

    async def step(self, action: dict, current_feedme_path: str, current_png_path: str, output_dir: str, node_id: str, summary_path: str = None) -> dict:
        """
        核心步进函数（精准防漏账及边界保护版）：
        执行动作 -> 生成新配置 -> 拉起 GALFIT -> 滤渣 -> 解析指标 -> 返回状态
        """
        os.makedirs(output_dir, exist_ok=True)
        new_feedme_path = os.path.join(output_dir, f"{node_id}.feedme")
        
        # 1. 物理动作执行：修改配置
        success = apply_action_to_feedme(
            action=action,
            current_feedme_path=current_feedme_path,
            new_feedme_path=new_feedme_path,
            summary_path=summary_path
        )
        
        if not success:
            print(f"Feedme 动作应用失败: {new_feedme_path}")
            return {"status": "failed", "error": "Feedme 动作应用失败"}
            
        # 2. 拉起物理引擎
        result = await run_galfit(os.path.abspath(new_feedme_path), ["-imax", str(self.max_iter)])
        
        if result.get("status") != "success":
            print(f"GALFIT 崩溃 (进程未正常完结): {new_feedme_path}")
            return {"status": "failed", "error": result.get("error", "GALFIT Crashed")}
            
        new_png = result.get("image_file")
        new_summary = result.get("summary_file")
        
        # 3. 核心物理收敛安全审查：只有真正跑成功且图片落地，才允许进入主流程
        if new_png and os.path.exists(new_png):
            metrics = self._extract_metrics(new_summary)
            
            # ========================================================
            # 🚀 核心看门狗：在算 SSIM 之前，立刻裁剪出专注的残差区域！
            # ========================================================
            try:
                # 把当前的基准图裁好
                current_cropped_png = crop_residual_panel(current_png_path)
                # 把刚刚跑出的新图裁好
                new_cropped_png = crop_residual_panel(new_png)
                
                # 用裁好的纯残差图去算 SSIM，对变化极其敏感！
                ssim_score = calculate_ssim(current_cropped_png, new_cropped_png)
            except Exception as e:
                print(f"    ⚠️ [SSIM 裁剪警告] 裁剪失败，回退使用全图对比: {e}")
                ssim_score = calculate_ssim(current_png_path, new_png)
                new_cropped_png = new_png

            # 4. 纯残差高灵敏度 SSIM 物理滤渣
            if ssim_score > 0.999:  # 裁切去噪后，0.999是非常健康的拦截线
                return {
                    "status": "rejected_by_ssim",
                    "msg": f"SSIM score {ssim_score:.4f} too high (无实质改变)",
                    "metrics": metrics
                }
                
            return {
                "status": "success",
                "metrics": metrics,
                # 向上层传递裁切后的干净残差图，确保大模型看图绝对精准
                "residual_path": new_cropped_png, 
                "feedme_path": new_feedme_path,
                "summary_path": new_summary
            }
            
        else:
            # 🚀 修正：如果返回码不对或者图片根本没生成，将其定义为“实质性失败”
            # 直接截断，不给上层 pipeline 和后面的大模型埋坑
            error_details = f"image_exists={bool(new_png and os.path.exists(new_png))}"
            print(f"❌ GALFIT 拟合实质失败: {new_feedme_path} ({error_details})")
            return {
                "status": "failed", 
                "error": f"GALFIT 拟合实质失败: {error_details}"
            }