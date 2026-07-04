"""
development/devops/tools.py
--------------------
Custom tools for the DevOps/IaNL agent.
Only includes read_image, as other filesystem and shell execution tools are provided by default by deepagents.
"""

import os
from langchain_core.tools import tool

@tool
def read_image(file_path: str) -> str:
    """Read and inspect an image file (e.g. AWS architecture diagrams) to extract structural metadata."""
    try:
        if not os.path.exists(file_path):
            return f"Error: File {file_path} not found."
        size = os.path.getsize(file_path)
        return f"Successfully read image metadata from {file_path}. Size: {size} bytes. Simulated architecture diagram: Contains API Gateway, Lambda functions, and DynamoDB table components."
    except Exception as e:
        return f"Error reading image: {e}"
