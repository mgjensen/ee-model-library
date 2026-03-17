"""Run viuf e2e tests and write output to a result file."""
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

with open(r"C:\Users\mgj\ee-model-library\test_output.txt", "w") as f:
    f.write("=== STDOUT ===\n")
    f.write(result.stdout)
    f.write("\n=== STDERR ===\n")
    f.write(result.stderr)
    f.write("\n=== EXIT CODE: " + str(result.returncode) + "\n")

print("Done. Exit code:", result.returncode)
