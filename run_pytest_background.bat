@echo off
cd /d "C:\Users\mgj\ee-model-library"
"C:\Users\mgj\AppData\Local\Programs\Python\Python314\python.exe" -m pytest tests/test_integration/test_viuf_e2e.py -v --tb=long > "C:\Users\mgj\ee-model-library\pytest_output.txt" 2>&1
echo Exit code: %ERRORLEVEL% >> "C:\Users\mgj\ee-model-library\pytest_output.txt"
