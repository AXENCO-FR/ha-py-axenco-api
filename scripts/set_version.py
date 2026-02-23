# scripts/set_version.py
import sys
import re

version = sys.argv[1]
pyproject = "pyproject.toml"

# Lecture du fichier
with open(pyproject, "r", encoding="utf-8") as f:
    content = f.read()

# Remplacement de la version
content = re.sub(r'version\s*=\s*"[^"]*"', f'version = "{version}"', content, count=1)

# Écriture du fichier
with open(pyproject, "w", encoding="utf-8") as f:
    f.write(content)

print(f"Set version to {version}")
