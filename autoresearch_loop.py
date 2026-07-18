#!/usr/bin/env python3
"""
autoresearch_loop.py

Autonomous loop that optimizes screener/scoring.py using LLMs.
Uses Gemini API as primary, and Groq as fallback.
Target Fitness Metric = Expectancy - |Max Drawdown|
"""
import os
import sys
import json
import subprocess
import time
from dotenv import load_dotenv
import requests

# Load API keys
load_dotenv()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

TARGET_FILE = os.path.join("screener", "scoring.py")
HISTORY_FILE = "autoresearch_history.json"
MAX_ITERATIONS = 1

# Ensure Git is initialized
if not os.path.isdir(".git"):
    subprocess.run(["git", "init"], check=True)
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit before autoresearch"], check=True)

def run_backtest_and_get_metrics():
    """Runs the backtest with --json flag and returns the metrics dictionary."""
    print("Running backtest...", flush=True)
    try:
        # Run a faster test window to iterate quickly (e.g., 2024 to early 2025)
        result = subprocess.run(
            [sys.executable, "run_backtest.py", "--start", "2024-01-01", "--end", "2025-01-01", "--json"],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Parse the last line as JSON (or search for it)
        output_lines = result.stdout.strip().split('\n')
        for line in reversed(output_lines):
            try:
                metrics = json.loads(line)
                if "expectancy_pct" in metrics:
                    return metrics
            except json.JSONDecodeError:
                continue
        print(f"Failed to find JSON in output:\n{result.stdout}")
        return None
    except subprocess.CalledProcessError as e:
        print(f"Backtest failed: {e}\n{e.stderr}\n{e.stdout}")
        return None

def compute_fitness(metrics):
    """Calculates Expectancy penalized by Max Drawdown."""
    if not metrics:
        return -9999.0
    expectancy = metrics.get('expectancy_pct', 0) or 0
    drawdown = abs(metrics.get('max_drawdown_pct', 0) or 0)
    
    # Penalize max drawdown heavily if it exceeds 15%
    penalty = drawdown * 1.5 if drawdown > 15 else drawdown
    return expectancy - penalty

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return []

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

def prompt_llm(current_code, history_text, baseline_fitness, error_trace=None):
    prompt = f"""You are an expert quantitative trading agent for a Swing Trading Screener.
Your goal is to optimize the scoring and ranking logic in `screener/scoring.py`.
The objective function you are trying to MAXIMIZE is: Fitness = (Expectancy % - |Max Drawdown| %).
Current Baseline Fitness: {baseline_fitness:.2f}

"""
    if error_trace:
        prompt += f"YOUR PREVIOUS CODE CRASHED WITH THIS ERROR:\n{error_trace}\nFix the error and try again.\n\n"
        
    prompt += f"""Here is the history of previous experiments and their fitness scores (do not repeat failed ideas):
{history_text}

Here is the current working code for `scoring.py`:
```python
{current_code}
```

Propose a new, modified version of the entire `scoring.py` file. 
You can adjust weights, add new mathematical combinations of existing indicators (like RSI, ATR, volume), or change the ranking heuristics.
Output ONLY valid Python code inside a single ```python code block. No explanation.
"""
    # Try Gemini API first (using standard REST)
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={GEMINI_API_KEY}"
        headers = {'Content-Type': 'application/json'}
        data = {
            "contents": [{"parts":[{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7}
        }
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return text
    except Exception as e:
        print(f"Gemini API failed: {e}. Falling back to Groq...")
        
    # Fallback to Groq API
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
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Groq API also failed: {e}")
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

def main():
    print("Starting Autoresearch Loop for scoring.py...")
    
    if not GEMINI_API_KEY and not GROQ_API_KEY:
        print("ERROR: Neither GEMINI_API_KEY nor GROQ_API_KEY found in environment.")
        sys.exit(1)
    
    history = load_history()
    
    baseline_metrics = run_backtest_and_get_metrics()
    if baseline_metrics is None:
        print("Failed to run baseline backtest. Check run_backtest.py")
        sys.exit(1)
        
    baseline_fitness = compute_fitness(baseline_metrics)
    print(f"Initial Baseline Fitness: {baseline_fitness:.2f}")
    
    error_trace = None

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"\n--- Iteration {iteration}/{MAX_ITERATIONS} ---")
        
        with open(TARGET_FILE, "r") as f:
            current_code = f.read()
            
        history_text = ""
        for idx, h in enumerate(history[-10:]): # keep last 10
            history_text += f"Run {idx}: Fitness={h['fitness']:.2f}, Note: {h.get('note', '')}\n"
            
        print("Prompting LLM for new strategy...")
        llm_out = prompt_llm(current_code, history_text, baseline_fitness, error_trace)
        
        new_code = extract_code(llm_out)
        if not new_code:
            print("Failed to get valid code from LLMs. Retrying...")
            continue
            
        # Overwrite file
        with open(TARGET_FILE, "w") as f:
            f.write(new_code)
            
        print("Running backtest with new strategy...")
        metrics = run_backtest_and_get_metrics()
        
        if metrics is None:
            # Code likely crashed, we need to rollback
            print("Backtest failed (Syntax/Runtime Error). Rolling back...")
            subprocess.run(["git", "checkout", TARGET_FILE], check=True)
            error_trace = "Backtest execution crashed. Ensure your code is valid Python and doesn't throw exceptions."
            continue
            
        error_trace = None
        fitness = compute_fitness(metrics)
        expectancy = metrics.get('expectancy_pct', 0)
        drawdown = metrics.get('max_drawdown_pct', 0)
        
        print(f"New Strategy Result -> Expectancy: {expectancy:.2f}%, Drawdown: {drawdown:.2f}% | Fitness: {fitness:.2f}")
        
        if fitness > baseline_fitness:
            print(f"🎉 New best strategy found! (+{fitness - baseline_fitness:.2f} fitness)")
            subprocess.run(["git", "add", TARGET_FILE], check=True)
            subprocess.run(["git", "commit", "-m", f"agent: improved scoring. Fitness: {fitness:.2f}"], check=True)
            baseline_fitness = fitness
            history.append({"fitness": fitness, "note": "Improved baseline!"})
        else:
            print(f"Strategy did not improve baseline. Rolling back...")
            subprocess.run(["git", "checkout", TARGET_FILE], check=True)
            history.append({"fitness": fitness, "note": "Failed to beat baseline."})
            
        save_history(history)
        time.sleep(2) # rate limit buffer

if __name__ == "__main__":
    main()
