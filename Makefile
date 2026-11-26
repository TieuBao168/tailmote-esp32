# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
# esp32-tailscale — setup / build / run entry points.  `make help` for the list.
# Every target forwards to scripts/tm.py; on Windows use .\tm <target> instead.
.DEFAULT_GOAL := help
PYTHON ?= python3
TM := $(PYTHON) scripts/tm.py

HOST ?=
LABEL ?=

.PHONY: help doctor deps idf setup secrets config build size flash monitor flash-monitor \
        menuconfig clean fullclean docker-build peer-up peer-down peer-status log bench metrics test

help: ## List the commands
	@awk 'BEGIN{FS=":.*## "} /^[a-zA-Z_-]+:.*## /{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}' $(MAKEFILE_LIST)

## ---- Environment ----
doctor: ## Check the environment (changes nothing)
	@$(TM) doctor
deps: ## Install required apt packages (sudo)
	@$(TM) deps
idf: ## Clone and install ESP-IDF and the esp32 toolchain
	@$(TM) idf
setup: deps idf secrets ## deps + idf + create the secrets form
	@echo "Fill in secrets/secrets.env, then run: make config build"

## ---- Configuration ----
secrets: ## Create the secrets/secrets.env form (never overwrites)
	@$(TM) secrets
config: ## Validate the form; generate keys, peer config and sdkconfig.secrets
	@$(TM) config

## ---- Firmware ----
build: ## Build firmware
	@$(TM) build
size: ## Static flash/RAM report → results/
	@$(TM) size
flash: ## Flash the firmware (SERIAL_PORT from the form, empty = auto-detect)
	@$(TM) flash
monitor: ## Open the serial monitor (Ctrl+] to exit)
	@$(TM) monitor
flash-monitor: ## Flash, then open the monitor
	@$(TM) flash-monitor
menuconfig: ## idf.py menuconfig
	@$(TM) menuconfig
clean: ## idf.py clean
	@$(TM) clean
fullclean: ## Remove build/ and sdkconfig
	@$(TM) fullclean
docker-build: ## Build with the espressif/idf image (needs only Docker)
	@$(TM) docker-build

## ---- Run / measure (needs a board) ----
peer-up: ## Start the WireGuard peer (sudo)
	@$(TM) peer-up
peer-down: ## Stop the WireGuard peer
	@$(TM) peer-down
peer-status: ## wg show
	@$(TM) peer-status
log: ## Reset the board and capture its log → results/serial.log
	@$(TM) log
bench: ## Run the benchmarks over the tunnel (HOST=<ip> for a LAN baseline, LABEL=...)
	@$(TM) bench $(if $(HOST),--host "$(HOST)") $(if $(LABEL),--label "$(LABEL)")
metrics: ## Summarize results/serial.log → results/periodic.csv
	@$(TM) metrics

## ---- Development ----
test: ## Unit tests for the tooling (no board / ESP-IDF needed)
	@$(TM) test
