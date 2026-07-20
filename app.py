import sys
import os
import subprocess

# Auto-install missing packages if necessary
required_packages = ["pandas", "numpy", "plotly", "openpyxl", "yfinance", "requests"]
missing = []
for pkg in required_packages:
    try:
        __import__(pkg)
    except ImportError:
        missing.append(pkg)

if missing:
    print(f"[APP SETUP] Auto-installing missing packages: {missing}...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install"] + missing, check=False)
    except Exception as e:
        print("[APP SETUP ERROR]", e)

# Navigate to stock screener v2 directory
v2_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock screener v2")
if v2_dir not in sys.path:
    sys.path.insert(0, v2_dir)

os.chdir(v2_dir)

# Import unified master dashboard
import dashboard
