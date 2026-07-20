import sys
import os
import subprocess

# Auto-install missing packages if necessary
required_packages = ["pandas", "numpy", "plotly", "openpyxl", "yfinance", "requests", "gradio"]
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

# Import unified master dashboard engine
import dashboard

# Gradio interface for Hugging Face Spaces (100% FREE Tier)
try:
    import gradio as gr
    def get_live_status():
        msg, _ = dashboard.generate_detailed_status_and_menu()
        return f"<div style='font-family:sans-serif; padding:20px; background:#111827; color:#FFF; border-radius:10px;'>{msg}</div>"

    demo = gr.Interface(
        fn=get_live_status,
        inputs=[],
        outputs="html",
        title="🤖 V2 Stock Screener & Trading System",
        description="System is active 24/7 in the cloud! Paper trader, Daily 4:00 PM auto-screener, and Telegram Bot listener are running live in the background."
    )
    
    if __name__ == "__main__":
        demo.launch(server_name="0.0.0.0", server_port=7860)
except Exception as g_err:
    print("Gradio launch notice:", g_err)
