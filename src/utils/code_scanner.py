import os
from pathlib import Path
from src.utils.logger import logger

def get_dir_code_contents(dir_path: str) -> str:
    """
    Recursively walks the directory path starting at `dir_path` and gathers
    the contents of text/code files into a single structured markdown string.
    Excludes common non-code, metadata, and dependency directories.
    """
    if not dir_path:
        return ""
        
    path = Path(dir_path).resolve()
    if not path.is_dir():
        logger.warning(f"Provided path is not a directory: {dir_path}")
        return ""

    exclude_dirs = {
        '.git', '.github', '__pycache__', 'node_modules', '.venv', 'venv', 
        'env', '.pytest_cache', '.mypy_cache', '.idea', '.vscode', 'dist', 
        'build', '.ruff_cache', '.ipynb_checkpoints', '.next'
    }
    exclude_files = {
        '.DS_Store', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 
        'uv.lock', 'poetry.lock'
    }
    
    code_contents = []
    total_chars = 0
    MAX_TOTAL_CHARS = 200_000  # Cap total characters at ~50K tokens to fit local context limits
    
    for root, dirs, files in os.walk(path):
        # Filter directories in-place to avoid walking into excluded ones
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            if file in exclude_files or file.endswith('.map'):
                continue
                
            file_path = Path(root) / file
            
            # Skip files larger than 100KB to avoid excessive token sizes
            try:
                if file_path.stat().st_size > 100 * 1024:
                    continue
            except Exception:
                continue
                
            try:
                relative_path = file_path.relative_to(path)
                # Try reading file content
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                    
                # Skip binary files if they contain null bytes
                if "\x00" in content:
                    continue
                
                content_len = len(content)
                if total_chars + content_len > MAX_TOTAL_CHARS:
                    # Append remaining budget snippet or message and stop
                    remaining_budget = MAX_TOTAL_CHARS - total_chars
                    if remaining_budget > 100:
                        code_contents.append(
                            f"File: {relative_path.as_posix()} [Truncated]\n"
                            f"```\n"
                            f"{content[:remaining_budget]}\n"
                            f"... [Truncated to fit context limits]\n"
                            f"```\n"
                        )
                    code_contents.append("\n... [Further files omitted to fit within local model context size limits] ...\n")
                    return "\n".join(code_contents)
                    
                code_contents.append(
                    f"File: {relative_path.as_posix()}\n"
                    f"```\n"
                    f"{content}\n"
                    f"```\n"
                )
                total_chars += content_len
            except Exception as e:
                # Silently skip files that fail to read (e.g. permission error, etc.)
                pass

    return "\n".join(code_contents)
