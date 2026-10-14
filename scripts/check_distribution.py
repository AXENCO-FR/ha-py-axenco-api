"""Check built artifacts without importing the source package."""

import sys
import tarfile
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile


def main(wheel: Path, sdist: Path) -> None:
    """Fail if Python metadata or public typing files are missing."""
    required = {"models.py", "exceptions.py", "const.py", "__init__.py", "py.typed"}
    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert all(f"pyaxencoapi/{name}" in names for name in required)
        (metadata_name,) = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        assert metadata["Requires-Python"] == ">=3.14"
        assert "Programming Language :: Python :: 3.14" in metadata.get_all(
            "Classifier", []
        )
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        assert all(
            any(name.endswith(f"/src/pyaxencoapi/{file}") for name in names)
            for file in required
        )
        assert any(name.endswith("/tests/typing/public_models.py") for name in names)
        assert any(name.endswith("/scripts/check_distribution.py") for name in names)
    print("Wheel and sdist: Python >=3.14 metadata and public typed files verified")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
