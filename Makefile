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

help: ## Danh sách lệnh
	@awk 'BEGIN{FS=":.*## "} /^[a-zA-Z_-]+:.*## /{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}' $(MAKEFILE_LIST)

## ---- Môi trường ----
doctor: ## Kiểm tra môi trường (không thay đổi gì)
	@$(TM) doctor
deps: ## Cài gói apt cần thiết (sudo)
	@$(TM) deps
idf: ## Clone + cài ESP-IDF và toolchain esp32
	@$(TM) idf
setup: deps idf secrets ## deps + idf + tạo form secrets
	@echo "Điền secrets/secrets.env rồi chạy: make config build"

## ---- Cấu hình ----
secrets: ## Tạo form secrets/secrets.env (không ghi đè)
	@$(TM) secrets
config: ## Kiểm tra form, sinh khóa + peer + sdkconfig.secrets
	@$(TM) config

## ---- Firmware ----
build: ## Build firmware
	@$(TM) build
size: ## Báo cáo Flash/RAM tĩnh → results/
	@$(TM) size
flash: ## Nạp firmware (SERIAL_PORT trong form, trống = tự dò)
	@$(TM) flash
monitor: ## Mở serial monitor (Ctrl+] để thoát)
	@$(TM) monitor
flash-monitor: ## Nạp rồi mở monitor
	@$(TM) flash-monitor
menuconfig: ## idf.py menuconfig
	@$(TM) menuconfig
clean: ## idf.py clean
	@$(TM) clean
fullclean: ## Xóa build + sdkconfig
	@$(TM) fullclean
docker-build: ## Build bằng image espressif/idf (chỉ cần Docker)
	@$(TM) docker-build

## ---- Chạy / đo (cần board) ----
peer-up: ## Bật WireGuard peer (sudo)
	@$(TM) peer-up
peer-down: ## Tắt WireGuard peer
	@$(TM) peer-down
peer-status: ## wg show
	@$(TM) peer-status
log: ## Reset board và ghi log → results/serial.log
	@$(TM) log
bench: ## Chạy bộ đo qua tunnel (HOST=<ip> để đo baseline LAN, LABEL=...)
	@$(TM) bench $(if $(HOST),--host "$(HOST)") $(if $(LABEL),--label "$(LABEL)")
metrics: ## Tóm tắt results/serial.log → results/periodic.csv
	@$(TM) metrics

## ---- Phát triển ----
test: ## Unit test cho tooling (không cần board / ESP-IDF)
	@$(TM) test
