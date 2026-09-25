# Jev Mobile: `make install`, then `make connect APP=<bundle id> PROJECT=<your app repo>`.

SHELL := /bin/bash
.DEFAULT_GOAL := help

LAYA_HOME := $(HOME)/Library/Application Support/jev-mobile/laya
LAYA_URL ?= http://127.0.0.1:8081
AGENT := com.jev-mobile.laya
PLIST := $(HOME)/Library/LaunchAgents/$(AGENT).plist
DOMAIN := gui/$(shell id -u)
VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
WHEEL := dist/jev_mobile-$(VERSION)-py3-none-any.whl

PROJECT ?= $(CURDIR)
APP ?=
DEVICE ?=
CLIENT ?=

.PHONY: help check install install-cli laya laya-status laya-logs doctor devices apps \
	connect test uninstall laya-uninstall

help: ## Show this help
	@echo "Jev Mobile: local MCP server for driving iOS simulators from AI agents"
	@echo
	@echo "Quick start:"
	@echo "  make install                                  # tools + local model (~1 GB, once)"
	@echo "  make apps                                     # find your app's bundle ID"
	@echo "  make connect APP=com.you.app PROJECT=~/code/your-app"
	@echo
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "connect options: APP=bundle id, DEVICE=simulator UDID, PROJECT=app repo,"
	@echo "                 CLIENT=claude|codex|cursor|json (default: all detected)"

check: ## Check prerequisites (uv, Xcode, Java, Maestro)
	@echo "Checking prerequisites:"
	@scripts/check.sh

install: check install-cli laya ## Install everything: CLI command + local Laya model service
	@echo
	@echo "Installed. Next: make apps, then make connect APP=<bundle id> PROJECT=<app repo>"

install-cli: ## Install only the `jev-mobile` command on your PATH
	uv sync --locked
	uv tool install --force --editable .
	@command -v jev-mobile >/dev/null || echo "Add ~/.local/bin to PATH: uv tool update-shell"

laya: ## Install or update the background Laya model service (Apple Silicon)
	@[ "$$(uname -m)" = arm64 ] || { echo "Laya needs Apple Silicon"; exit 1; }
	uv build --wheel
	@mkdir -p "$(LAYA_HOME)" "$(HOME)/Library/LaunchAgents"
	@[ -x "$(LAYA_HOME)/.venv/bin/python" ] || uv venv "$(LAYA_HOME)/.venv"
	-@launchctl bootout "$(DOMAIN)/$(AGENT)" 2>/dev/null
	uv pip install --python "$(LAYA_HOME)/.venv/bin/python" \
		--reinstall-package jev-mobile "$(WHEEL)[laya]"
	@echo "Downloading the pinned model if needed (~850 MB, first time only)..."
	@HF_HOME="$(LAYA_HOME)/cache" "$(LAYA_HOME)/.venv/bin/python" -c \
		"from huggingface_hub import snapshot_download as d; from jev_mobile.laya import DEFAULT_MODEL as m, DEFAULT_REVISION as r; d(m, revision=r)" >/dev/null
	@sed "s|@LAYA_HOME@|$(LAYA_HOME)|g" scripts/laya-agent.plist > "$(PLIST)"
	launchctl bootstrap "$(DOMAIN)" "$(PLIST)"
	@printf "Waiting for the model to load"
	@for i in $$(seq 90); do \
		curl -fs "$(LAYA_URL)/health" | grep -q '"ready"' && { echo " ready"; exit 0; }; \
		printf .; sleep 2; \
	done; echo; echo "Laya did not become ready; run: make laya-logs"; exit 1

laya-status: ## Show whether the Laya service is ready
	@curl -fs "$(LAYA_URL)/health" && echo || echo "Laya is not responding at $(LAYA_URL)"

laya-logs: ## Show recent Laya service logs
	@tail -n 40 "$(LAYA_HOME)/server.stderr.log" "$(LAYA_HOME)/server.stdout.log"

doctor: check ## Check prerequisites, Laya, and the Maestro connection
	@echo "Laya:"; printf "  "; $(MAKE) -s laya-status
	@echo "Maestro MCP:"; jev-mobile doctor | sed 's/^/  /'

devices: ## List booted simulators
	@xcrun simctl list devices booted | grep Booted || echo "No booted simulator; open Simulator.app"

apps: ## List your installed apps' bundle IDs on booted simulators
	@for udid in $$(xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}'); do \
		echo "$$udid:"; \
		xcrun simctl listapps "$$udid" | plutil -convert json -o - - | python3 -c \
			'import json,sys; [print("  " + k + "  (" + (v.get("CFBundleDisplayName") or v.get("CFBundleName") or "?") + ")") for k,v in sorted(json.load(sys.stdin).items()) if v.get("ApplicationType")=="User"]'; \
	done

connect: ## Register the MCP server with your AI agents for PROJECT
	@command -v jev-mobile >/dev/null || { echo "Run make install first"; exit 1; }
	cd "$(patsubst ~%,$(HOME)%,$(PROJECT))" && jev-mobile setup \
		$(if $(APP),--app-id "$(APP)") $(if $(DEVICE),--device "$(DEVICE)") \
		$(foreach c,$(CLIENT),--client $(c))

test: ## Run tests and lint (for contributors)
	uv sync --locked
	uv run pytest -q
	uv run ruff check .

uninstall: ## Remove the `jev-mobile` command (keeps the Laya service)
	-uv tool uninstall jev-mobile

laya-uninstall: ## Stop and delete the Laya service, its environment, and model cache
	-launchctl bootout "$(DOMAIN)/$(AGENT)" 2>/dev/null
	rm -f "$(PLIST)"
	rm -rf "$(LAYA_HOME)"
