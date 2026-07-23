import sys
import re

def extract_keys(env_path: str) -> list[str]:
    keys = []
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if match:
                keys.append(match.group(1))
    return keys

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else ".env"
    keys = extract_keys(path)
    for k in keys:
        print(k)
