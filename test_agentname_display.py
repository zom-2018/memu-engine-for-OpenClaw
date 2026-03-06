#!/usr/bin/env python3
"""Test agentName display in memory search results."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PYTHON_DIR = SCRIPT_DIR / "python"
SEARCH_SCRIPT = PYTHON_DIR / "scripts" / "search.py"

def setup_test_db():
    """Create test database with multi-agent memories."""
    temp_dir = tempfile.mkdtemp(prefix="memu_search_test_")
    db_path = os.path.join(temp_dir, "memu.db")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE memu_memory_items (
            id TEXT PRIMARY KEY,
            user_id TEXT DEFAULT 'default',
            agentName TEXT DEFAULT 'main',
            memory_type TEXT,
            summary TEXT,
            resource_id TEXT,
            happened_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE memu_memory_categories (
            id TEXT PRIMARY KEY,
            user_id TEXT DEFAULT 'default',
            agentName TEXT DEFAULT 'main',
            name TEXT,
            description TEXT,
            summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    test_items = [
        ("item1", "default", "main", "conversation", "JWT authentication implementation using bcrypt", None),
        ("item2", "default", "prometheus", "conversation", "Database migration strategy for PostgreSQL", None),
        ("item3", "default", "librarian", "conversation", "React hooks best practices from official docs", None),
        ("item4", "default", "main", "conversation", "Python async/await patterns", None),
    ]
    
    cursor.executemany(
        "INSERT INTO memu_memory_items (id, user_id, agentName, memory_type, summary, resource_id) VALUES (?, ?, ?, ?, ?, ?)",
        test_items
    )
    
    test_categories = [
        ("cat1", "default", "main", "Authentication", "Auth-related memories", "JWT, OAuth, sessions"),
        ("cat2", "default", "prometheus", "Database", "Database design and migration", "PostgreSQL, migrations, schema"),
    ]
    
    cursor.executemany(
        "INSERT INTO memu_memory_categories (id, user_id, agentName, name, description, summary) VALUES (?, ?, ?, ?, ?, ?)",
        test_categories
    )
    
    conn.commit()
    conn.close()
    
    return temp_dir, db_path


def run_search(db_path, query, agent_name=None, allow_cross_agent=False):
    """Run search.py and return parsed results."""
    env = os.environ.copy()
    env["MEMU_DATA_DIR"] = os.path.dirname(db_path)
    env["MEMU_USER_ID"] = "default"
    env["MEMU_WORKSPACE_DIR"] = "/tmp"
    env["MEMU_EXTRA_PATHS"] = "[]"
    
    venv_python = PYTHON_DIR / ".venv" / "bin" / "python3"
    python_exe = str(venv_python) if venv_python.exists() else sys.executable
    
    args = [
        python_exe,
        str(SEARCH_SCRIPT),
        query,
        "--mode", "fast",
        "--max-results", "10",
    ]
    
    if agent_name:
        args.extend(["--agent-name", agent_name])
    
    if allow_cross_agent:
        args.append("--allow-cross-agent")
    
    result = subprocess.run(
        args,
        env=env,
        capture_output=True,
        text=True,
        cwd=str(PYTHON_DIR)
    )
    
    if result.returncode != 0:
        print(f"Search failed: {result.stderr}")
        return None
    
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON: {e}")
        print(f"Output: {result.stdout}")
        return None


def test_single_agent_search(db_path):
    """Test 1: Single agent search should show agentName."""
    print("\n" + "="*70)
    print("TEST 1: Single Agent Search (main)")
    print("="*70)
    
    result = run_search(db_path, "authentication", agent_name="main", allow_cross_agent=False)
    
    if not result:
        print("✗ Search failed")
        return False
    
    results = result.get("results", [])
    print(f"Found {len(results)} result(s)")
    
    passed = True
    for i, r in enumerate(results, 1):
        agent = r.get("agentName")
        snippet = r.get("snippet", "")[:50]
        
        if agent:
            print(f"  ✓ Result {i}: agentName='{agent}' - {snippet}...")
            if agent != "main":
                print(f"    ⚠ Expected 'main', got '{agent}'")
                passed = False
        else:
            print(f"  ✗ Result {i}: agentName MISSING - {snippet}...")
            passed = False
    
    return passed


def test_cross_agent_search(db_path):
    """Test 2: Cross-agent search should show different agentNames."""
    print("\n" + "="*70)
    print("TEST 2: Cross-Agent Search")
    print("="*70)
    
    result = run_search(db_path, "database", agent_name="main", allow_cross_agent=True)
    
    if not result:
        print("✗ Search failed")
        return False
    
    results = result.get("results", [])
    print(f"Found {len(results)} result(s)")
    
    agents_seen = set()
    passed = True
    
    for i, r in enumerate(results, 1):
        agent = r.get("agentName")
        snippet = r.get("snippet", "")[:50]
        
        if agent:
            agents_seen.add(agent)
            print(f"  ✓ Result {i}: agentName='{agent}' - {snippet}...")
        else:
            print(f"  ✗ Result {i}: agentName MISSING - {snippet}...")
            passed = False
    
    print(f"\nAgents seen: {sorted(agents_seen)}")
    
    if len(agents_seen) > 1:
        print("  ✓ Multiple agents detected (cross-agent search working)")
    else:
        print("  ⚠ Only one agent detected (expected multiple)")
    
    return passed


def test_specific_agent_search(db_path):
    """Test 3: Search specific agent (prometheus)."""
    print("\n" + "="*70)
    print("TEST 3: Specific Agent Search (prometheus)")
    print("="*70)
    
    result = run_search(db_path, "database", agent_name="prometheus", allow_cross_agent=False)
    
    if not result:
        print("✗ Search failed")
        return False
    
    results = result.get("results", [])
    print(f"Found {len(results)} result(s)")
    
    passed = True
    for i, r in enumerate(results, 1):
        agent = r.get("agentName")
        snippet = r.get("snippet", "")[:50]
        
        if agent:
            print(f"  ✓ Result {i}: agentName='{agent}' - {snippet}...")
            if agent != "prometheus":
                print(f"    ✗ Expected 'prometheus', got '{agent}'")
                passed = False
        else:
            print(f"  ✗ Result {i}: agentName MISSING - {snippet}...")
            passed = False
    
    return passed


def main():
    """Run all tests."""
    print("="*70)
    print("MEMORY SEARCH - AGENTNAME DISPLAY TEST")
    print("="*70)
    
    temp_dir, db_path = setup_test_db()
    print(f"\n✓ Test database created: {db_path}")
    
    try:
        results = {
            "Single Agent Search": test_single_agent_search(db_path),
            "Cross-Agent Search": test_cross_agent_search(db_path),
            "Specific Agent Search": test_specific_agent_search(db_path),
        }
        
        print("\n" + "="*70)
        print("TEST SUMMARY")
        print("="*70)
        
        passed = sum(1 for v in results.values() if v)
        total = len(results)
        
        for test_name, result in results.items():
            status = "✓ PASS" if result else "✗ FAIL"
            print(f"  {status}: {test_name}")
        
        print(f"\nTotal: {passed}/{total} tests passed")
        
        if passed == total:
            print("\n🎉 ALL TESTS PASSED!")
            return 0
        else:
            print(f"\n⚠️  {total - passed} test(s) failed")
            return 1
            
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"\n✓ Cleaned up test database")


if __name__ == "__main__":
    sys.exit(main())
