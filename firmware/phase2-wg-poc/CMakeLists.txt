# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The esp32-tailscale Authors
cmake_minimum_required(VERSION 3.16)

# Secrets (Wi-Fi + WireGuard keys) live in sdkconfig.secrets, which is git-ignored.
# `make config` generates it from the form secrets/secrets.env.
set(SDKCONFIG_DEFAULTS "sdkconfig.defaults")
if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.secrets")
    list(APPEND SDKCONFIG_DEFAULTS "sdkconfig.secrets")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wg_poc)
