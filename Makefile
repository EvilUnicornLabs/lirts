PYTHON ?= venv/bin/python
PIP    ?= venv/bin/pip

.PHONY: help venv install dev lint format format-check typecheck test coverage smoke file-length secrets ci-local check hooks run list completion build clean

help:            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

venv:            ## Create the virtualenv
	python3 -m venv venv
	$(PIP) install --upgrade pip

install: venv    ## Install lirts into the virtualenv
	$(PIP) install -e .

dev: venv        ## Install lirts plus the development tools
	$(PIP) install -e ".[dev]"

lint:            ## Ruff lint
	venv/bin/ruff check lirts tests

format:          ## Ruff format + autofix
	venv/bin/ruff format lirts tests
	venv/bin/ruff check --fix lirts tests

format-check:    ## Ruff format, fail on diff
	venv/bin/ruff format --check lirts tests

typecheck:       ## Mypy
	venv/bin/mypy lirts

test:            ## Pytest
	$(PYTHON) -m pytest

coverage:        ## Pytest with coverage report
	$(PYTHON) -m pytest --cov=lirts --cov-report=term-missing

smoke:           ## The binary starts and the demo works
	venv/bin/lirts --version
	venv/bin/lirts explain --demo > /dev/null

file-length:     ## No code file over 400 lines
	scripts/check-file-length.sh

secrets:         ## Gitleaks secret scan (binary, or docker; warns when neither exists)
	@if command -v gitleaks >/dev/null; then gitleaks detect --source=. --no-git --redact; \
	elif command -v docker >/dev/null; then docker run --rm -v "$$PWD:/repo" ghcr.io/gitleaks/gitleaks:v8.18.4 detect --source=/repo --no-git --redact; \
	else echo "WARN: gitleaks unavailable (no binary, no docker); CI still runs it"; fi

ci-local: lint format-check typecheck test smoke file-length secrets   ## Exactly what CI runs; mandatory before a push
	@echo "ci-local: all checks passed, safe to push"

check: ci-local  ## Alias for ci-local

hooks:           ## Install the pre-push hook that runs make ci-local
	git config core.hooksPath .githooks
	@echo "pre-push hook installed (.githooks/pre-push)"

run:             ## Launch the dashboard
	venv/bin/lirts

list:            ## One-shot table of listeners
	venv/bin/lirts list

completion:      ## Install shell completion for lirts (bash/zsh/fish auto-detected)
	venv/bin/lirts --install-completion

build:           ## Build sdist and wheel into dist/
	$(PIP) install -q build
	$(PYTHON) -m build

clean:           ## Remove caches and build artefacts
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
