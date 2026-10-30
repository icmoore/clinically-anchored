"""Fail unless the real-Postgres tests really ran.

Usage: check_db_tests_ran.py JUNIT_XML TESTS_DB_DIR

Those tests skip themselves when TEST_DATABASE_URL is missing (or psycopg, psql or pg_dump are), and
a skip is green. When CI has set TEST_DATABASE_URL a skip means the job's setup is broken, so this
script turns it into a failure: every test_*.py in TESTS_DB_DIR must have at least one passed test in
the junit file, and none of that directory's tests may be skipped, errored or failed. It prints
how many tests each module ran, so the log shows that, for example, test_patient_list.py did.
"""

import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


def main(junit: str, db_dir: str) -> int:
    if not os.environ.get("TEST_DATABASE_URL"):
        print("TEST_DATABASE_URL is not set: nothing to check.")
        return 0
    package = ".".join(Path(db_dir).parts)  # tests/db -> tests.db
    modules = {p.stem for p in Path(db_dir).glob("test_*.py")}
    outcomes: dict[str, Counter] = {m: Counter() for m in modules}
    stray = Counter()
    for case in ET.parse(junit).getroot().iter("testcase"):
        classname = case.get("classname", "")
        if not classname.startswith(package + "."):
            continue
        module = classname[len(package) + 1 :].split(".")[0]
        if case.find("skipped") is not None:
            outcome = "skipped"
        elif case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
        else:
            outcome = "passed"
        (outcomes[module] if module in outcomes else stray)[outcome] += 1

    problems = []
    print(f"{'module':44} passed skipped failed")
    for module in sorted(modules):
        c = outcomes[module]
        print(f"{module:44} {c['passed']:6} {c['skipped']:7} {c['failed']:6}")
        if c["passed"] == 0:
            problems.append(f"{module}: no test passed (collected or ran none)")
        if c["skipped"]:
            problems.append(f"{module}: {c['skipped']} skipped")
        if c["failed"]:
            problems.append(f"{module}: {c['failed']} failed or errored")
    if stray["skipped"]:
        problems.append(f"{stray['skipped']} skipped outside the test modules (collection)")
    total = sum(sum(c.values()) for c in outcomes.values())
    print(f"{total} real-Postgres tests in {len(modules)} modules")
    if problems:
        print("\nThe real-Postgres tests did not all run:\n  " + "\n  ".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
