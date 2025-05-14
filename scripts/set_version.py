# scripts/set_version.py
import sys
import toml

version = sys.argv[2]
pyproject = "pyproject.toml"

data = toml.load(pyproject)
data["project"]["version"] = version
with open(pyproject, "w") as f:
    toml.dump(data, f)

print(f"Set version to {version}")