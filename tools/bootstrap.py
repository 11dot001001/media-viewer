"""Install changed dependencies once, then launch using the managed runtime."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
os.chdir(root)
requirements = root / 'requirements.txt'
stamp = root / '.runtime' / 'requirements.sha256'
digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
if not stamp.exists() or stamp.read_text() != digest:
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(requirements)], check=True)
    stamp.write_text(digest)
raise SystemExit(subprocess.call([sys.executable, '-m', 'mediaviewer', *sys.argv[1:]]))
