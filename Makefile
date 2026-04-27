# Voxium — GNU make on Ubuntu, Debian, WSL (Linux), or any Unix with GNU make + bash.
# On Windows, use the `voxium` CLI in PowerShell instead of this Makefile; see README.
#
# Optional env: PYTEST_ARGS, TT_ARGS (extra flags for `voxium run` on `make start`).

ROOT       := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
MAKEFILE   := $(abspath $(firstword $(MAKEFILE_LIST)))
MKPY       := $(ROOT)/scripts/mk.py
VENV       ?= .venv
VENVDIR    := $(ROOT)/$(VENV)
DEV_STAMP  := $(ROOT)/.dev-install-stamp
PYPROJECT  := $(ROOT)/pyproject.toml
PYTEST_ARGS ?=
TT_ARGS  ?=

PYTHON     ?= python3
VENV_PYTHON  := $(VENVDIR)/bin/python
VOXIUM       := $(VENVDIR)/bin/voxium

.PHONY: help install install-dev uninstall clean disk-usage mic-info gpu-info repo-stats lint test test-cov start

.DEFAULT_GOAL := help

##@ Help

help: ## List targets grouped by category
	@"$(PYTHON)" "$(MKPY)" help --makefile "$(MAKEFILE)"

##@ Setup

install: ## Create venv and pip install -e . (installs .venv/bin/voxium)
	@"$(PYTHON)" "$(MKPY)" install --root "$(ROOT)" --venvd "$(VENVDIR)" --python "$(PYTHON)"

uninstall: ## Remove .venv, all *.egg-info, and .dev-install-stamp (undoes: make install, make install-dev)
	@"$(PYTHON)" "$(MKPY)" uninstall --root "$(ROOT)" --venvd "$(VENVDIR)" --dev-stamp "$(DEV_STAMP)"

clean: ## Remove tool caches, __pycache__, build/dist, coverage, root *.egg-info (keeps .venv/.dev-install-stamp)
	@"$(PYTHON)" "$(MKPY)" clean --root "$(ROOT)" --venvd "$(VENVDIR)" --dev-stamp "$(DEV_STAMP)"

##@ Info

disk-usage: ## Show disk usage for models/, history/, and logs/ in the repo
	@"$(PYTHON)" "$(MKPY)" disk-usage --root "$(ROOT)" --venvd "$(VENVDIR)" --dev-stamp "$(DEV_STAMP)"

mic-info: ## Print default mic, host APIs, and all PortAudio / sounddevice devices
	@test -x "$(VENV_PYTHON)" || (printf 'Missing %s. Run: make install\n' "$(VENV_PYTHON)" >&2; exit 1)
	@"$(VENV_PYTHON)" "$(ROOT)/scripts/mic_info.py"

gpu-info: ## Print GPU / driver info (nvidia-smi, ctranslate2, optional pynvml / rocm-smi)
	@test -x "$(VENV_PYTHON)" || (printf 'Missing %s. Run: make install\n' "$(VENV_PYTHON)" >&2; exit 1)
	@"$(VENV_PYTHON)" "$(ROOT)/scripts/gpu_info.py"

repo-stats: ## Regenerate docs/repository-stats.md (LOC, Mermaid pies; stdlib only, no venv)
	@command -v $(PYTHON) >/dev/null 2>&1 && $(PYTHON) "$(ROOT)/scripts/generate_repo_stats.py" || \
		(command -v python >/dev/null 2>&1 && python "$(ROOT)/scripts/generate_repo_stats.py" || (printf 'Need python3 or python for repo-stats\n' >&2; exit 1))

##@ Development

$(DEV_STAMP): $(PYPROJECT)
	@"$(PYTHON)" "$(MKPY)" dev-stamp --root "$(ROOT)" --venvd "$(VENVDIR)" --dev-stamp "$(DEV_STAMP)" --python "$(PYTHON)"

install-dev: $(DEV_STAMP) ## Install editable + dev deps (ruff, pytest, pytest-cov)

lint: $(DEV_STAMP) ## Run ruff on the repo (installs .[dev] once via stamp)
	@"$(PYTHON)" "$(MKPY)" lint --root "$(ROOT)" --venv-python "$(VENV_PYTHON)"

# Extra pytest flags: use PYTEST_ARGS=...  (one shell word-splitting layer from Make)
test: $(DEV_STAMP) ## Run pytest (set PYTEST_ARGS= for extra flags)
	@PYTEST_ARGS='$(value PYTEST_ARGS)' "$(PYTHON)" "$(MKPY)" test --root "$(ROOT)" --venv-python "$(VENV_PYTHON)"

test-cov: $(DEV_STAMP) ## Pytest + coverage; fail-under from pyproject (see docs/testing.md)
	@PYTEST_ARGS='$(value PYTEST_ARGS)' "$(PYTHON)" "$(MKPY)" test-cov --root "$(ROOT)" --venv-python "$(VENV_PYTHON)"

##@ Run

start: ## Run: voxium run (set TT_ARGS= for extra voxium run flags; needs make install)
	@test -x "$(VOXIUM)" || (printf 'Missing %s. Run: make install\n' "$(VOXIUM)" >&2; exit 1)
	@"$(VOXIUM)" run $(TT_ARGS)
