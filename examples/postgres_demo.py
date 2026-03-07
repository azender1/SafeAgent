import sys
import os

# allow running from repo root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from settlement.models import Case
from settlement.postgres_store import PostgresStore


def main():
    dsn = os.getenv("SAFEAGENT_POSTGRES_DSN")

    if not dsn:
        print("Missing SAFEAGENT_POSTGRES_DSN")
        print("Example:")
        print('$env:SAFEAGENT_POSTGRES_DSN="postgresql://user:pass@localhost:5432/safeagent"')
        return

    store = PostgresStore(dsn)

    case_id = "pg_demo_case_1"

    case = store.get_case(case_id)

    if case is None:
        print("No case found. Creating one...")
        case = Case(case_id=case_id)
        store.put_case(case)
        print("Saved case:", case.case_id, "state:", case.state)
    else:
        print("Loaded case:", case.case_id, "state:", case.state)


if __name__ == "__main__":
    main()