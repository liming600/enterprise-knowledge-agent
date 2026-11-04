"""Capture a reproducible local unittest report without storing environment variables."""
from pathlib import Path
import subprocess,sys
root=Path(__file__).resolve().parents[1]
result=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace')
(root/'examples/test-results.txt').write_text('Command: python -m unittest discover -s tests -v\n\n'+result.stdout,encoding='utf-8')
print(result.stdout,end='');sys.exit(result.returncode)
