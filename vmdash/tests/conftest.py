import importlib.util

# These tests exercise the ~/dashboard package. They are collected there
# (after the relay copies vmdash/tests into ~/dashboard/tests) and ignored
# here, where that package does not exist.
if importlib.util.find_spec("dashboard") is None:
    collect_ignore = ["test_core.py", "test_mlb_season.py"]
