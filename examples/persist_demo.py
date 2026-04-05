import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from settlement.models import Case
from settlement.store import SQLiteStore


def main():
    db_path = os.path.join("examples", "traces", "settlement_demo.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    store = SQLiteStore(db_path)

    case_id = "persist_case_1"
    case = store.get_case(case_id)

    if case is None:
        print("No case found. Creating and saving new case...")
        case = Case(case_id=case_id)
        store.put_case(case)
        print("Saved. Re-run this script to prove it loads from disk.")
    else:
        print("Loaded case from disk:", case.case_id, "state:", case.state)


if __name__ == "__main__":
    main()