# Contributing

Run `uv sync`, `uv run pytest`, and `uv run ruff check .` before submitting changes.
Add fixture-based tests for new hierarchy formats or executor behavior. Keep model output
restricted to offered choices, preserve independent success checks, and never include
credentials or personal device traces in a contribution.

The first milestone is a verified iOS simulator demo. Android support, richer text input,
a live inspector, and better target freshness checks are useful follow-up work.
