"""Copy the calculation library into the integration.

Home Assistant installs requirements from PyPI, and solXpect_meets_python isn't
published there, so the integration carries a copy of its calcs/ package.

Usage (both repos side by side):
    python scripts/sync_library.py [path/to/solXpect_meets_python]
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "custom_components" / "solxpect_sites" / "solxpect"
FILES = ["__init__.py", "PanelArray.py", "Inverter.py", "Site.py", "SolarPowerPlant.py",
         "forecast.py", "app_import.py", "sunposition.py"]


def main():
    library = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "solXpect_meets_python"
    source = library / "calcs"
    commit = subprocess.run(["git", "-C", str(library), "log", "-1", "--format=%H"],
                            capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(library), "status", "--porcelain", "calcs"],
                           capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        sys.exit("calcs/ has uncommitted changes in the library repo; commit them first")

    TARGET.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copyfile(source / name, TARGET / name)
    (TARGET / "SOURCE").write_text(
        "Copied from https://github.com/blablubbbb/solXpect_meets_python (calcs/)\n"
        f"commit {commit}\n"
        "Don't edit here; change the library and run scripts/sync_library.py\n",
        encoding="utf-8")
    print(f"copied {len(FILES)} files from {library} @ {commit[:7]}")


if __name__ == "__main__":
    main()
