# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
# esp32-tailscale — setup / build / run entry points.  `make help` for the list.
SHELL := /bin/bash
.DEFAULT_GOAL := help
S := scripts

HOST ?=
LABEL ?=

.PHONY: help doctor deps idf setup secrets config build size flash monitor flash-monitor \
        menuconfig clean fullclean docker-build peer-up peer-down peer-status log bench metrics

help: ## Danh sách lệnh
	@awk 'BEGIN{FS=":.*## "} /^[a-zA-Z_-]+:.*## /{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}' $(MAKEFILE_LIST)

## ---- Môi trường ----
doctor: ## Kiểm tra môi trường (không thay đổi gì)
	@$(S)/doctor.sh
deps: ## Cài gói apt cần thiết (sudo)
	@$(S)/install-deps.sh
idf: ## Clone + cài ESP-IDF và toolchain esp32
	@$(S)/install-idf.sh
setup: deps idf secrets ## deps + idf + tạo form secrets
	@echo "Điền secrets/secrets.env rồi chạy: make config build"

## ---- Cấu hình ----
secrets: ## Tạo form secrets/secrets.env (không ghi đè)
	@$(S)/new-secrets.sh
config: ## Kiểm tra form, sinh khóa + Linux peer + sdkconfig.secrets
	@$(S)/gen-config.sh

## ---- Firmware ----
build: ## Build firmware
	@$(S)/fw.sh build
size: ## Báo cáo Flash/RAM tĩnh → results/
	@$(S)/fw.sh size
flash: ## Nạp firmware (SERIAL_PORT trong form)
	@$(S)/fw.sh flash
monitor: ## Mở serial monitor (Ctrl+] để thoát)
	@$(S)/fw.sh monitor
flash-monitor: ## Nạp rồi mở monitor
	@$(S)/fw.sh flash-monitor
menuconfig: ## idf.py menuconfig
	@$(S)/fw.sh menuconfig
clean: ## idf.py clean
	@$(S)/fw.sh clean
fullclean: ## Xóa build + sdkconfig
	@$(S)/fw.sh fullclean
docker-build: ## Build bằng image espressif/idf (chỉ cần Docker)
	@$(S)/docker-build.sh

## ---- Chạy / đo (cần board) ----
peer-up: ## Bật WireGuard peer trên Linux (sudo)
	@$(S)/peer.sh up
peer-down: ## Tắt WireGuard peer
	@$(S)/peer.sh down
peer-status: ## wg show
	@$(S)/peer.sh status
log: ## Reset board và ghi log → results/serial.log
	@source $(S)/common.sh && idf_env && load_secrets && \
	  python tools/bench/serial_capture.py --port "$$SERIAL_PORT" --reset --out results/serial.log
bench: ## Chạy bộ đo qua tunnel (HOST=<ip> để đo baseline LAN, LABEL=...)
	@source $(S)/common.sh && load_secrets && \
	  python3 tools/bench/wg_bench.py all --host "$(if $(HOST),$(HOST),$$ESP_TUNNEL_IP)" \
	    --label "$(if $(LABEL),$(LABEL),$(if $(HOST),baseline LAN,tunnel) PS=$$WIFI_POWER_SAVE)" \
	    --out results/bench.jsonl
metrics: ## Tóm tắt results/serial.log → results/periodic.csv
	@python3 tools/bench/parse_metrics.py results/serial.log --csv results/periodic.csv
