import sys
import os

# Navigate to stock screener v2 directory
v2_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock screener v2")
if v2_dir not in sys.path:
    sys.path.insert(0, v2_dir)

os.chdir(v2_dir)

# Import unified master dashboard
import dashboard
