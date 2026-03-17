"""Helper script to run the viuf e2e tests and capture output."""
import subprocess
import sys
import os

os.chdir(r"C:\Users\mgj\ee-model-library")
sys.path.insert(0, r"C:\Users\mgj\ee-model-library")

result = subprocess.run(
    [sys.executable, "-m", "pytest",
     "tests/test_integration/test_viuf_e2e.py",
     "-v", "--tb=long"],
    capture_output=True,
    text=True,
    cwd=r"C:\Users\mgj\ee-model-library",
)

print("=== STDOUT ===")
print(result.stdout)
print("=== STDERR ===")
print(result.stderr)
print("=== EXIT CODE:", result.returncode)
