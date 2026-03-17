@echo off
cd /d "C:\Users\mgj\ee-model-library"
"C:\Users\mgj\AppData\Local\Programs\Python\Python314\python.exe" -m pytest tests/test_integration/test_viuf_e2e.py -v --tb=long > test_output.txt 2>&1
echo EXIT_CODE: %ERRORLEVEL% >> test_output.txt
