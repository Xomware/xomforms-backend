# xomforms-backend

Python Lambda backend for **Xomforms** — a group availability scheduler (When2meet/Doodle done right).

See [`docs/features/xomforms/PLAN.md`](https://github.com/Xomware/xomforms-backend) *(plan lives in the local `docs/` monorepo working tree, not this repo)* for the full spec.

## Structure

- `lambdas/common/` — shared helpers (logger, errors, utility_helpers, constants), Pydantic models, DynamoDB modules, timezone + overlap logic.
- `lambdas/<name>/handler.py` — one Lambda per API route.
- `tests/` — pytest, RED-before-GREEN.

## Setup

```bash
pip install -r requirements.txt
pip install pytest pytest-cov moto boto3
./run_tests.sh
```
