"""
data_engineering/ai/harness.py
--------------------
AI Engineer Agent Harness subclassing the BaseAgentHarness.
Exposes sandboxed workspace shell execution.
"""

from src.agents.utils.harness_base import BaseAgentHarness

class AIEngineerAgentHarness(BaseAgentHarness):
    """
    Initializes the AI Engineer sandbox execution environment.
    """

    def __init__(self, sandbox_name: str = "ai-sandbox"):
        super().__init__(sandbox_name=sandbox_name)
        # Sandbox shell execution is supported natively via BaseAgentHarness
