.PHONY: install test lint demo
install:
	pip install -e ".[dev]"
test:
	pytest -q
lint:
	ruff check src tests && mypy src
demo:
	finlake scorecard && finlake close && finlake lineage
