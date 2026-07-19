import os
import sys
import platform
import subprocess
from typing import List, Any
from langchain_core.tools import tool

@tool
def inspect_a_file(path: str) -> str:
    """Reads and returns the content of the file at the given path as a string.
    Handles file not found and decoding errors gracefully.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return content
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except UnicodeDecodeError:
        return f"Error: Could not decode file (not UTF-8): {path}"
    except Exception as e:
        return f"Error reading file {path}: {e}"

@tool
def write_code_to_file(path: str, code: str) -> str:
    """Write the provided code string to the specified file path, creating parent directories if needed.
    If the code contains class or function definitions, append an if __name__ == "__main__": block to run the function(s) or instantiate the class and invoke its methods.
    """
    import re
    try:
        class_names = re.findall(r"^class\s+(\w+)\s*\(", code, re.MULTILINE)
        func_names = re.findall(r"^def\s+(\w+)\s*\(", code, re.MULTILINE)
        main_block = ""
        if class_names or func_names:
            main_block += '\n\nif __name__ == "__main__":\n'
            for cname in class_names:
                main_block += f"    obj = {cname}()\n"
                # Find methods
                method_matches = re.findall(rf"^\s+def\s+(\w+)\s*\(", code, re.MULTILINE)
                for m in method_matches:
                    if m != "__init__":
                        main_block += f"    obj.{m}()\n"
            for fname in func_names:
                main_block += f"    {fname}()\n"
        
        final_code = code.rstrip() + main_block
        parent_dir = os.path.dirname(path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(final_code)
        return f"Successfully wrote code to {path}\n"
    except Exception as e:
        return f"Error writing code to {path}: {e}"

@tool
def refactoring_code(refactored_code: str, error_file_path: str) -> str:
    """Overwrite the specified file with the provided refactored code."""
    try:
        with open(error_file_path, "w", encoding="utf-8") as file:
            file.write(refactored_code)
        return f"Successfully wrote refactored code to {error_file_path}"
    except Exception as e:
        return f"Error writing to {error_file_path}: {e}"

@tool
def extract_content_within_a_file(path: str) -> str:
    """Extract and return the content of a file at the specified path."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return content
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except UnicodeDecodeError:
        return f"Error: Could not decode file (not UTF-8): {path}"
    except Exception as e:
        return f"Error reading file {path}: {e}"

@tool
def look_for_directory(path: str) -> Any:
    """List all directories within the given path."""
    try:
        dirs = [
            name for name in os.listdir(path) if os.path.isdir(os.path.join(path, name))
        ]
        return dirs
    except Exception as e:
        return f"Error: {e}"

@tool
def current_directory() -> str:
    """Get the current working directory path."""
    return os.getcwd()

@tool
def change_directory(path: str) -> str:
    """Change the current working directory to the specified path."""
    try:
        os.chdir(path)
        return os.getcwd()
    except Exception as e:
        return f"Error: {e}"

@tool
def navigate_entire_codebase_given_path(path: str) -> List[str]:
    """Recursively navigate and return list of relative paths of all files and directories in the given path, skipping cache/hidden files."""
    skip_dirs = {"__pycache__", ".git", ".venv", ".cache", "node_modules", ".next"}
    skip_files = {".DS_Store"}
    skip_exts = {".pyc", ".pyo", ".map"}
    file_list = []
    
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for name in dirs:
            file_list.append(os.path.relpath(os.path.join(root, name), path))
        for name in files:
            if (
                name not in skip_files
                and not name.startswith(".")
                and not any(name.endswith(ext) for ext in skip_exts)
            ):
                file_list.append(os.path.relpath(os.path.join(root, name), path))
    return file_list

@tool
def execute_python_code(path: str) -> str:
    """Runs the python file at the specified path and returns stdout and stderr logs."""
    try:
        process = subprocess.run(
            [sys.executable, path],
            capture_output=True,
            text=True,
            timeout=30,
            errors="replace"
        )
        output = process.stdout
        if process.stderr:
            output += f"\n[stderr]\n{process.stderr}"
        return output
    except subprocess.TimeoutExpired:
        return "Error: Python execution timed out after 30 seconds."
    except Exception as e:
        return f"Exception occurred while executing {path}: {e}"

@tool
def windows_powershell_command(command: str) -> str:
    """Run a command in a windows PowerShell session and return the output."""
    try:
        powershell_exe = "powershell.exe"
        if platform.system() != "Windows":
            powershell_exe = "pwsh"
        
        result = subprocess.run(
            [powershell_exe, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=60,
            errors="replace"
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        return output
    except subprocess.TimeoutExpired:
        return "Error: PowerShell command timed out after 60 seconds."
    except Exception as e:
        return f"Error executing command: {e}"

CUSTOM_FILESYSTEM_TOOLS = [
    inspect_a_file,
    write_code_to_file,
    refactoring_code,
    extract_content_within_a_file,
    look_for_directory,
    current_directory,
    change_directory,
    navigate_entire_codebase_given_path,
    execute_python_code,
    windows_powershell_command
]
