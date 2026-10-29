"""Bundle the shared backend without secrets, caches, or the native-only entrypoint."""
from pathlib import Path
import shutil
root = Path(__file__).resolve().parent
source = root.parent / 'src' / 'ai_inspector'
target = root / 'src' / 'ai_inspector'
if target.exists():
    shutil.rmtree(target)
shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'vendor'))
