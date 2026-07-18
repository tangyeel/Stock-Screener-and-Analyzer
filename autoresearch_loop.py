#!/usr/bin/env python3
"""
autoresearch_loop.py

Autonomous loop that optimizes screener/scoring.py using LLMs.
Uses Gemini API as primary, and Groq as fallback.
Evaluates strategies using Train/Test (Out-of-Sample) splits.
Target Fitness Metric = Expectancy - |Max Drawdown|
"""
import os
import sys
import json
import subprocess
import time
import re
import argparse
from datetime import datetime
from dotenv import load_dotenv
import requests

# Load API keys
load_dotenv()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

TARGET_FILE = os.path.join("screener", "scoring.py")
HISTORY_FILE = "autoresearch_history.json"
STATUS_FILE = os.path.join("run_logging", "autoresearch_status.json")

# Ensure Git is initialized
if not os.path.isdir(".git"):
    subprocess.run(["git", "init"], check=True)
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit before autoresearch"], check=True)

def update_status(is_running: bool, current_iteration: int, total_iterations: int, message: str = ""):
    """Write current execution status to file for Streamlit UI polling."""
    os.makedirs("run_logging", exist_ok=True)
    status_data = {
        "is_running": is_running,
        "pid": os.getpid(),
        "current_iteration": current_iteration,
        "total_iterations": total_iterations,
        "message": message,
        "updated_at": datetime.now().isoformat()
    }
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status_data, f, indent=2)
    except Exception as e:
        pass

def run_backtest_and_get_metrics(start_date: str, end_date: str):
    """Runs the backtest with --json flag and returns the metrics dictionary."""
    print(f"Running backtest from {start_date} to {end_date}...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "run_backtest.py", "--start", start_date, "--end", end_date, "--json"],
            capture_output=True,
            text=True,
            check=True
        )
        
        output_lines = result.stdout.strip().split('\n')
        for line in reversed(output_lines):
            try:
                metrics = json.loads(line)
                if "expectancy_pct" in metrics:
                    return metrics
            except json.JSONDecodeError:
                continue
        print(f"Failed to find JSON in output:\n{result.stdout}", flush=True)
        return None
    except subprocess.CalledProcessError as e:
        print(f"Backtest failed: {e}\n{e.stderr}\n{e.stdout}", flush=True)
        return None

def compute_fitness(metrics):
    """Calculates Expectancy penalized by Max Drawdown."""
    if not metrics:
        return -9999.0
    
    if metrics.get("warning") or metrics.get("total_triggered", 0) == 0:
        return -9999.0

    expectancy = metrics.get('expectancy_pct', 0) or 0
    drawdown = abs(metrics.get('max_drawdown_pct', 0) or 0)
    
    penalty = drawdown * 1.5 if drawdown > 15 else drawdown
    return expectancy - penalty

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

def prompt_llm(current_code, history_text, baseline_fitness, error_trace=None):
    prompt = f"""You are an expert quantitative trading researcher optimizing a swing trading screener.
Your goal is to optimize the scoring and ranking logic in `screener/scoring.py` to maximize strategy expectancy while minimizing drawdown.
Target Fitness Metric = Expectancy % - |Max Drawdown| %.
Current Baseline Fitness: {baseline_fitness:.2f}

GUIDELINES FOR GENERATING STRONG STRATEGIES:
1. Combine indicators smoothly (Relative Strength RS, Volume-to-20D Volume ratio, Base Tightness/CoV, Proximity to 52w High, Sector RS, Delivery Slope, and RSI).
2. Avoid binary or cliff-like thresholds that eliminate candidates abruptly; use smooth scaling (e.g. min(vol_ratio, 3.0), normalized RSI bounds 50-75).
3. Ensure both `score_candidate(row)` and `rank_and_select(candidates, max_picks)` are present with identical function signatures.

"""
    if error_trace:
        prompt += f"YOUR PREVIOUS ATTEMPT CRASHED WITH THIS ERROR:\n{error_trace}\nFix the error and try again.\n\n"
        
    prompt += f"""History of prior trials (do not repeat failed or overfitted ideas):
{history_text}

Current working `scoring.py`:
```python
{current_code}
```

Propose a complete, improved replacement for `scoring.py`.
You MUST put a comment at the very top of your file in this exact format:
# STRATEGY NOTE: [Concise one-line summary of what you modified]

Output ONLY valid Python code inside a single ```python code block. No markdown explanation outside the code block.
"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {'Content-Type': 'application/json'}
        data = {
            "contents": [{"parts":[{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7}
        }
        response = requests.post(url, headers=headers, json=data, timeout=120)
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return text
    except Exception as e:
        print(f"Gemini API failed: {e}. Falling back to Groq...", flush=True)
        
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "llama3-70b-8192",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7
        }
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Groq API also failed: {e}", flush=True)
        return None

def extract_code(llm_output):
    if not llm_output: return None
    if "```python" in llm_output:
        code = llm_output.split("```python")[1].split("```")[0]
        return code.strip()
    if "```" in llm_output:
         code = llm_output.split("```")[1].split("```")[0]
         return code.strip()
    return llm_output.strip()

def extract_strategy_note(code):
    match = re.search(r"#\s*STRATEGY NOTE:\s*(.*)", code, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "Optimized ranking logic parameters."

def ensure_valid_scoring_file():
    """Self-heal target file if it is empty or missing."""
    if not os.path.exists(TARGET_FILE) or os.path.getsize(TARGET_FILE) == 0:
        print("screener/scoring.py was empty or missing. Self-healing via git checkout...", flush=True)
        subprocess.run(["git", "checkout", "HEAD", "--", TARGET_FILE], check=True)

def main():
    parser = argparse.ArgumentParser(description="Autoresearch LLM Strategy Optimizer")
    parser.add_argument("--iterations", type=int, default=1, help="Number of research iterations to execute")
    args = parser.parse_args()

    ensure_valid_scoring_file()

    num_iterations = args.iterations
    print(f"Starting Autoresearch Loop for {num_iterations} iteration(s)...", flush=True)
    
    if not GEMINI_API_KEY and not GROQ_API_KEY:
        print("ERROR: Neither GEMINI_API_KEY nor GROQ_API_KEY found in environment.", flush=True)
        update_status(False, 0, num_iterations, "Missing API keys")
        sys.exit(1)
    
    history = load_history()
    start_gen = len(history) + 1
    
    update_status(True, 1, num_iterations, "Evaluating initial baseline...")

    print("Evaluating baseline on Train set...", flush=True)
    train_baseline_metrics = run_backtest_and_get_metrics("2026-04-01", "2026-06-01")
    if train_baseline_metrics is None:
        print("Failed to run baseline backtest on Train set. Check database prices.", flush=True)
        update_status(False, 0, num_iterations, "Baseline backtest failed")
        sys.exit(1)
        
    baseline_fitness = compute_fitness(train_baseline_metrics)
    print(f"Initial Baseline Fitness: {baseline_fitness:.2f}", flush=True)
    
    error_trace = None

    try:
        for i in range(num_iterations):
            current_gen = start_gen + i
            update_status(True, i + 1, num_iterations, f"Running Generation {current_gen}...")
            print(f"\n--- Generation {current_gen} (Run {i + 1}/{num_iterations}) ---", flush=True)
            
            with open(TARGET_FILE, "r") as f:
                current_code = f.read()
                
            history_text = ""
            for h in history[-10:]:
                history_text += f"Gen {h['generation']}: Fitness={h['score']:.2f}, Status={h['status']}, Note: {h['strategy_notes']}\n"
                
            print("Prompting LLM for new strategy...", flush=True)
            llm_out = prompt_llm(current_code, history_text, baseline_fitness, error_trace)
            
            new_code = extract_code(llm_out)
            if not new_code:
                print("Failed to get valid code from LLMs. Retrying...", flush=True)
                continue
                
            note = extract_strategy_note(new_code)
            print(f"Proposed Strategy Note: '{note}'", flush=True)
                
            with open(TARGET_FILE, "w") as f:
                f.write(new_code)
                
            print("Evaluating proposed strategy on Train window...", flush=True)
            train_metrics = run_backtest_and_get_metrics("2026-04-01", "2026-06-01")
            
            if train_metrics is None or train_metrics.get("warning"):
                print("Proposed code crashed or returned 0 trades on Train set. Rolling back...", flush=True)
                subprocess.run(["git", "checkout", TARGET_FILE], check=True)
                error_trace = "The code failed to execute properly or generated 0 trades on the Train set."
                
                history.append({
                    "generation": current_gen,
                    "score": -9999.0,
                    "pnl_pct": 0.0,
                    "win_rate": 0.0,
                    "max_drawdown_pct": 0.0,
                    "trades_count": 0,
                    "status": "failed",
                    "strategy_notes": f"[FAILED] {note}"
                })
                save_history(history)
                continue
                
            error_trace = None
            train_fitness = compute_fitness(train_metrics)
            print(f"Train set -> Expectancy: {train_metrics.get('expectancy_pct'):.2f}%, DD: {train_metrics.get('max_drawdown_pct'):.2f}% | Fitness: {train_fitness:.2f}", flush=True)
            
            if train_fitness > baseline_fitness:
                print("Evaluating proposed strategy on Out-of-Sample Test set to verify robustness...", flush=True)
                test_metrics = run_backtest_and_get_metrics("2026-06-01", "2026-07-16")
                
                if test_metrics is None or test_metrics.get("warning"):
                    print("Strategy crashed on OOS Test set. Rejecting...", flush=True)
                    subprocess.run(["git", "checkout", TARGET_FILE], check=True)
                    history.append({
                        "generation": current_gen,
                        "score": train_fitness,
                        "pnl_pct": train_metrics.get("total_return_pct", 0),
                        "win_rate": train_metrics.get("win_rate", 0),
                        "max_drawdown_pct": train_metrics.get("max_drawdown_pct", 0),
                        "trades_count": train_metrics.get("total_triggered", 0),
                        "status": "look-ahead",
                        "strategy_notes": f"[CAUSAL REJECT] Crashed on OOS test set. Note: {note}"
                    })
                    save_history(history)
                    continue
                    
                test_fitness = compute_fitness(test_metrics)
                test_expectancy = test_metrics.get("expectancy_pct", 0)
                
                print(f"Test set -> Expectancy: {test_expectancy:.2f}%, DD: {test_metrics.get('max_drawdown_pct'):.2f}% | Fitness: {test_fitness:.2f}", flush=True)
                
                if test_expectancy <= 0 or test_fitness < baseline_fitness:
                    print("[REJECT] Overfitting / Look-ahead detected. Rolling back...", flush=True)
                    subprocess.run(["git", "checkout", TARGET_FILE], check=True)
                    
                    history.append({
                        "generation": current_gen,
                        "score": train_fitness,
                        "pnl_pct": train_metrics.get("total_return_pct", 0),
                        "win_rate": train_metrics.get("win_rate", 0),
                        "max_drawdown_pct": train_metrics.get("max_drawdown_pct", 0),
                        "trades_count": train_metrics.get("total_triggered", 0),
                        "status": "look-ahead",
                        "strategy_notes": f"[CAUSAL REJECT] Overfitted/Look-ahead (Test expectancy: {test_expectancy:.2f}%). Note: {note}"
                    })
                else:
                    print(f"[SUCCESS] New best strategy accepted! (+{train_fitness - baseline_fitness:.2f} fitness)", flush=True)
                    subprocess.run(["git", "add", TARGET_FILE], check=True)
                    subprocess.run(["git", "commit", "-m", f"agent: gen {current_gen} improved scoring. Fitness: {train_fitness:.2f}"], check=True)
                    
                    baseline_fitness = train_fitness
                    history.append({
                        "generation": current_gen,
                        "score": train_fitness,
                        "pnl_pct": train_metrics.get("total_return_pct", 0),
                        "win_rate": train_metrics.get("win_rate", 0),
                        "max_drawdown_pct": train_metrics.get("max_drawdown_pct", 0),
                        "trades_count": train_metrics.get("total_triggered", 0),
                        "status": "accepted",
                        "strategy_notes": note
                    })
            else:
                print("Strategy did not beat train baseline. Rolling back...", flush=True)
                subprocess.run(["git", "checkout", TARGET_FILE], check=True)
                
                history.append({
                    "generation": current_gen,
                    "score": train_fitness,
                    "pnl_pct": train_metrics.get("total_return_pct", 0),
                    "win_rate": train_metrics.get("win_rate", 0),
                    "max_drawdown_pct": train_metrics.get("max_drawdown_pct", 0),
                    "trades_count": train_metrics.get("total_triggered", 0),
                    "status": "discarded",
                    "strategy_notes": note
                })
                
            save_history(history)
            time.sleep(2)
            
    finally:
        update_status(False, num_iterations, num_iterations, "Completed")

if __name__ == "__main__":
    main()
