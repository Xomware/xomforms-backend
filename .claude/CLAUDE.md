# xomforms-backend

> Group availability scheduler backend — When2meet/Doodle done right.

## What This Is
Python Lambda backend for Xomforms. Creator defines a candidate date range +
time-of-day window; respondents paint availability; overlap is computed
on-read. Mirrors `xomify-backend`'s `lambdas/<name>/handler.py` +
`lambdas/common/` pattern, with Pydantic 2.8 models at the request/response
boundary (see `docs/features/xomforms/PLAN.md`).

## Stack
- Python 3.12, AWS Lambda, DynamoDB, Pydantic 2.8

## Key Commands
```bash
pip install -r requirements.txt
./run_tests.sh
```

## Project Config
```yaml
pm_tool: github-projects
github_project_number: 2
github_project_owner: Xomware
base_branch: master
test_commands:
  - ./run_tests.sh
```

## Constraints
- Availability blocks stored as canonical UTC instants; rendered per-viewer in local tz.
- Overlap is compute-on-read via `lambdas/common/overlap.py::compute_overlap(poll_id)`.
- Grid size capped at poll creation so a response item stays < 400 KB (see `models.py`).

## Lessons
