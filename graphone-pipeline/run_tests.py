"""Minimal stdlib-only test runner (no pytest dependency needed to verify this repo)."""
import importlib
import sys
import traceback

test_modules = [
    "tests.test_date_utils",
    "tests.test_entity_resolution",
    "tests.test_llm_chunking",
]

total, failed = 0, 0
for mod_name in test_modules:
    mod = importlib.import_module(mod_name)
    for name in dir(mod):
        if name.startswith("test_"):
            total += 1
            fn = getattr(mod, name)
            try:
                fn()
                print(f"PASS  {mod_name}.{name}")
            except Exception:
                failed += 1
                print(f"FAIL  {mod_name}.{name}")
                traceback.print_exc()

print(f"\n{total - failed}/{total} tests passed")
sys.exit(1 if failed else 0)
