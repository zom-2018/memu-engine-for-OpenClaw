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
VENV_PYTHON = PYTHON_DIR / ".venv" / "bin" / "python"

def setup_test_db():
    """Create per-agent test databases for the current hybrid layout."""
    temp_dir = tempfile.mkdtemp(prefix="memu_search_test_")
    memory_root = os.path.join(temp_dir, "memory")
    os.makedirs(memory_root, exist_ok=True)

    def create_agent_db(agent_name: str, items: list[tuple], categories: list[tuple]) -> str:
        agent_dir = os.path.join(memory_root, agent_name)
        os.makedirs(agent_dir, exist_ok=True)
        db_path = os.path.join(agent_dir, "memu.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE memu_memory_items (
                id TEXT PRIMARY KEY,
                user_id TEXT DEFAULT 'default',
                agent_id TEXT DEFAULT 'main',
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
                agent_id TEXT DEFAULT 'main',
                agentName TEXT DEFAULT 'main',
                name TEXT,
                description TEXT,
                summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.executemany(
            "INSERT INTO memu_memory_items (id, user_id, agent_id, agentName, memory_type, summary, resource_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            items,
        )
        cursor.executemany(
            "INSERT INTO memu_memory_categories (id, user_id, agent_id, agentName, name, description, summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
            categories,
        )
        conn.commit()
        conn.close()
        return db_path

    create_agent_db(
        "main",
        [
            ("item1", "default", "main", "main", "conversation", "JWT authentication implementation using bcrypt", None),
            ("item4", "default", "main", "main", "conversation", "Python async/await patterns", None),
        ],
        [
            ("cat1", "default", "main", "main", "Authentication", "Auth-related memories", "JWT, OAuth, sessions"),
        ],
    )
    create_agent_db(
        "prometheus",
        [
            ("item2", "default", "prometheus", "prometheus", "conversation", "Database migration strategy for PostgreSQL", None),
        ],
        [
            ("cat2", "default", "prometheus", "prometheus", "Database", "Database design and migration", "PostgreSQL, migrations, schema"),
        ],
    )
    create_agent_db(
        "librarian",
        [
            ("item3", "default", "librarian", "librarian", "conversation", "React hooks best practices from official docs", None),
        ],
        [],
    )

    return temp_dir, memory_root


def run_search(memory_root, query, agent_name=None, search_stores=None):
    """Run search.py and return parsed results."""
    env = os.environ.copy()
    env["MEMU_USER_ID"] = "default"
    env["MEMU_WORKSPACE_DIR"] = "/tmp"
    env["MEMU_EXTRA_PATHS"] = "[]"
    env["MEMU_MEMORY_ROOT"] = memory_root
    env["MEMU_AGENT_SETTINGS"] = json.dumps(
        {
            "main": {"memoryEnabled": True, "searchEnabled": True, "searchableStores": ["self", "prometheus", "librarian"]},
            "prometheus": {"memoryEnabled": True, "searchEnabled": True, "searchableStores": ["self"]},
            "librarian": {"memoryEnabled": True, "searchEnabled": True, "searchableStores": ["self"]},
        }
    )

    python_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

    args = [
        python_exe,
        str(SEARCH_SCRIPT),
        query,
        "--mode", "fast",
        "--max-results", "10",
    ]

    if agent_name:
        args.extend(["--requesting-agent", agent_name])

    if search_stores:
        args.extend(["--search-stores", ",".join(search_stores)])

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


def test_single_agent_search(memory_root):
    """Test 1: Single agent search should show agentName."""
    print("\n" + "="*70)
    print("TEST 1: Single Agent Search (main)")
    print("="*70)
    
    result = run_search(memory_root, "authentication", agent_name="main", search_stores=["self"])
    
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


def test_cross_agent_search(memory_root):
    """Test 2: Cross-agent search should show different agentNames."""
    print("\n" + "="*70)
    print("TEST 2: Cross-Agent Search")
    print("="*70)
    
    result = run_search(memory_root, "database", agent_name="main", search_stores=["self", "prometheus", "librarian"])
    
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


def test_specific_agent_search(memory_root):
    """Test 3: Search specific agent (prometheus)."""
    print("\n" + "="*70)
    print("TEST 3: Specific Agent Search (prometheus)")
    print("="*70)
    
    result = run_search(memory_root, "database", agent_name="prometheus", search_stores=["self"])
    
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
    
    temp_dir, memory_root = setup_test_db()
    print(f"\n✓ Test memory root created: {memory_root}")
    
    try:
        results = {
            "Single Agent Search": test_single_agent_search(memory_root),
            "Cross-Agent Search": test_cross_agent_search(memory_root),
            "Specific Agent Search": test_specific_agent_search(memory_root),
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
