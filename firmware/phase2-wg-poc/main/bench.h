// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The esp32-tailscale Authors
#pragma once

/*
 * Benchmark services, reachable through the tunnel (10.66.0.2 by default):
 *   UDP 7     echo              -> RTT / loss       (wg_bench.py rtt)
 *   UDP 5001  sink + "STAT"     -> RX throughput    (wg_bench.py udp-rx)
 *   TCP 5002  discard sink      -> RX throughput    (wg_bench.py tcp-rx)
 *   TCP 5003  source (N s)      -> TX throughput    (wg_bench.py tcp-tx)
 */
void bench_start(void);
