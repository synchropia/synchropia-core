"""
data_engineering/mlops/harness.py
--------------------
MLOps Agent Harness subclassing the BaseAgentHarness.
Exposes sandboxed workspace shell execution.
"""

from src.agents.utils.harness_base import BaseAgentHarness

class MLOpsAgentHarness(BaseAgentHarness):
    """
    Initializes the MLOps sandbox execution environment.
    """

    def __init__(self, sandbox_name: str = "mlops-sandbox"):
        super().__init__(sandbox_name=sandbox_name)
        # Sandbox shell execution is supported natively via BaseAgentHarness
