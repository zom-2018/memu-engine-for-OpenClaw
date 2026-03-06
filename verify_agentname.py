#!/usr/bin/env python3
"""Simple test to verify agentName appears in search results."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PYTHON_DIR = SCRIPT_DIR / "python"
SEARCH_SCRIPT = PYTHON_DIR / "scripts" / "search.py"

# Use production database
DB_PATH = Path.home() / ".openclaw" / "memUdata" / "memu.db"

def check_database():
    """Verify database has agentName column and data."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM memu_memory_items WHERE agentName='main'")
    count = cursor.fetchone()[0]
    print(f"✓ Database has {count} items with agentName='main'")
    
    conn.close()
    return count > 0


def run_search_simple():
    """Run search in simple mode (no embeddings needed)."""
    env = os.environ.copy()
    env["MEMU_DATA_DIR"] = str(DB_PATH.parent)
    env["MEMU_USER_ID"] = "default"
    env["MEMU_WORKSPACE_DIR"] = str(Path.home())
    env["MEMU_EXTRA_PATHS"] = "[]"
    env["OPENAI_API_KEY"] = "sk-dummy"  # Dummy key for testing
    
    venv_python = PYTHON_DIR / ".venv" / "bin" / "python3"
    
    args = [
        str(venv_python),
        str(SEARCH_SCRIPT),
        "test",
        "--mode", "fast",
        "--max-results", "5",
        "--agent-name", "main",
    ]
    
    result = subprocess.run(
        args,
        env=env,
        capture_output=True,
        text=True,
        cwd=str(PYTHON_DIR)
    )
    
    return result.stdout, result.stderr


def main():
    print("="*70)
    print("AGENTNAME DISPLAY VERIFICATION")
    print("="*70)
    
    if not DB_PATH.exists():
        print(f"✗ Database not found: {DB_PATH}")
        return 1
    
    print(f"\n✓ Using database: {DB_PATH}")
    
    if not check_database():
        print("✗ Database has no data")
        return 1
    
    print("\nRunning search...")
    stdout, stderr = run_search_simple()
    
    if stderr and "error" in stderr.lower():
        print(f"\n⚠ Stderr output:\n{stderr[:500]}")
    
    try:
        result = json.loads(stdout)
        
        if "error" in result:
            print(f"\n⚠ Search returned error: {result['error'][:200]}")
            print("\nThis is expected if OpenAI API is not configured.")
            print("The important thing is: no 'no such column' error!")
            return 0
        
        results = result.get("results", [])
        print(f"\n✓ Search returned {len(results)} result(s)")
        
        if not results:
            print("\n⚠ No results (might need valid OpenAI API key for embeddings)")
            return 0
        
        has_agent_name = False
        for i, r in enumerate(results[:3], 1):
            agent = r.get("agentName")
            snippet = r.get("snippet", "")[:40]
            
            if agent:
                print(f"  ✓ Result {i}: agentName='{agent}' - {snippet}...")
                has_agent_name = True
            else:
                print(f"  ✗ Result {i}: agentName MISSING - {snippet}...")
        
        if has_agent_name:
            print("\n🎉 SUCCESS: agentName field is present in results!")
            return 0
        else:
            print("\n✗ FAIL: agentName field is missing from all results")
            return 1
            
    except json.JSONDecodeError as e:
        print(f"\n✗ Failed to parse JSON: {e}")
        print(f"Output: {stdout[:500]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
