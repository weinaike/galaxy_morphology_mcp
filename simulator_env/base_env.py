# simulator_env/base_env.py
from abc import ABC, abstractmethod

class BaseSimulatorEnv(ABC):
    @abstractmethod
    async def initialize(self, init_config_path: str, output_dir: str) -> dict:
        """
        初始化环境基准状态（跑第0步）
        返回格式需包含: status, feedme_path, residual_path, summary_path, metrics
        """
        pass

    @abstractmethod
    async def step(self, action: dict, current_feedme_path: str, current_png_path: str, output_dir: str, node_id: str, summary_path: str = None) -> dict:
        """
        执行 Action，步进状态
        返回格式需包含: status, feedme_path, residual_path, summary_path, metrics
        """
        pass