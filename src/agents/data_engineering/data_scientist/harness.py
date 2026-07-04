"""
data_engineering/data_scientist/harness.py
--------------------
Data Scientist Agent Harness subclassing the BaseAgentHarness.
Exposes sandboxed workspace shell execution.
"""

from src.agents.utils.harness_base import BaseAgentHarness

class DataScientistAgentHarness(BaseAgentHarness):
    """
    Initializes the Data Scientist sandbox execution environment.
    """

    def __init__(self, sandbox_name: str = "data-scientist-sandbox"):
        super().__init__(sandbox_name=sandbox_name)
        # Sandbox shell execution is supported natively via BaseAgentHarness
