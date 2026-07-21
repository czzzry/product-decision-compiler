PYTHON ?= python3
VENV ?= .venv

.PHONY: demo install test lint

install:
	@test -x "$(VENV)/bin/python" || "$(PYTHON)" -m venv "$(VENV)"
	@"$(VENV)/bin/python" -m pip install -e '.[dev]'

demo: install
	@"$(VENV)/bin/product-decision-compiler-demo"
	@"$(VENV)/bin/product-decision-compiler-integrations-demo"

test: install
	@"$(VENV)/bin/python" -m pytest -p no:cacheprovider tests/product_decision_compiler

lint: install
	@"$(VENV)/bin/ruff" check .
