"""Benchmark workloads: CPU, memory, disk I/O, network.

Each benchmark produces a script that can be run inside any isolation
environment. The runner captures timing and throughput metrics.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from ..util import BenchResult, run, DISK_DIR, console


# ── Timing helper (embedded in each script) ──────────────────────────────────
# Busybox date doesn't support %N. We use python3 which is always available
# in our runner environment. For environments without python3, fall back to
# GNU date, then seconds-only.
_TIMING_PREAMBLE = r"""
get_ns() {
    python3 -c 'import time;print(int(time.time()*1e9))' 2>/dev/null && return
    # GNU date fallback (not busybox)
    local ts=$(date +%s%N 2>/dev/null)
    if [ ${#ts} -gt 15 ]; then echo "$ts"; return; fi
    # Last resort: seconds only (1s resolution)
    echo "$(($(date +%s)*1000000000))"
}
"""

# ── Benchmark scripts (portable sh) ─────────────────────────────────────────
# These scripts are injected into the isolated environment.

CPU_BENCH_SCRIPT = _TIMING_PREAMBLE + r"""
# Compute-bound: generate primes via trial division
START=$(get_ns)
i=2; count=0
while [ $count -lt 5000 ]; do
    j=2; is_prime=1
    while [ $((j*j)) -le $i ]; do
        if [ $((i%j)) -eq 0 ]; then is_prime=0; break; fi
        j=$((j+1))
    done
    if [ $is_prime -eq 1 ]; then count=$((count+1)); fi
    i=$((i+1))
done
END=$(get_ns)
echo "CPU_PRIMES=$count"
echo "CPU_ELAPSED_NS=$((END-START))"
"""

MEM_BENCH_SCRIPT = _TIMING_PREAMBLE + r"""
# Memory bandwidth: allocate and fill 64MB via dd
START=$(get_ns)
dd if=/dev/zero bs=1M count=64 2>/dev/null | cat > /dev/null
END=$(get_ns)
echo "MEM_BYTES=67108864"
echo "MEM_ELAPSED_NS=$((END-START))"
"""

DISK_BENCH_SCRIPT = _TIMING_PREAMBLE + r"""
# Disk I/O: write then read 32MB
TMPF=$(mktemp /tmp/vmb_disk_XXXXXX)
START=$(get_ns)
dd if=/dev/zero of=$TMPF bs=1M count=32 conv=fdatasync 2>/dev/null
dd if=$TMPF of=/dev/null bs=1M 2>/dev/null
END=$(get_ns)
rm -f $TMPF
echo "DISK_BYTES=67108864"
echo "DISK_ELAPSED_NS=$((END-START))"
"""

NET_LATENCY_BENCH_SCRIPT = _TIMING_PREAMBLE + r"""
# Network latency: measure DNS resolution + small HTTP fetch time
START=$(get_ns)
if command -v wget >/dev/null 2>&1; then
    wget -qO /dev/null http://detectportal.firefox.com/canonical.html 2>/dev/null && echo "NET_OK=1" || echo "NET_OK=0"
elif command -v curl >/dev/null 2>&1; then
    curl -so /dev/null http://detectportal.firefox.com/canonical.html 2>/dev/null && echo "NET_OK=1" || echo "NET_OK=0"
else
    echo "NET_OK=-1"
fi
END=$(get_ns)
echo "NET_LATENCY_NS=$((END-START))"
"""

NET_BANDWIDTH_BENCH_SCRIPT = _TIMING_PREAMBLE + r"""
# Network bandwidth: download a known-size payload and measure throughput
# Uses a 1MB test - small enough to be fast, large enough to measure
TMPF=$(mktemp /tmp/vmb_netbw_XXXXXX)
START=$(get_ns)
if command -v wget >/dev/null 2>&1; then
    wget -qO "$TMPF" http://speed.hetzner.de/100KB.bin 2>/dev/null && echo "NETBW_OK=1" || echo "NETBW_OK=0"
elif command -v curl >/dev/null 2>&1; then
    curl -so "$TMPF" http://speed.hetzner.de/100KB.bin 2>/dev/null && echo "NETBW_OK=1" || echo "NETBW_OK=0"
else
    echo "NETBW_OK=-1"
fi
END=$(get_ns)
FSIZE=$(wc -c < "$TMPF" 2>/dev/null || echo 0)
rm -f "$TMPF"
echo "NETBW_BYTES=$FSIZE"
echo "NETBW_ELAPSED_NS=$((END-START))"
"""


def write_bench_scripts(target_dir: Path) -> dict[str, Path]:
    """Write all benchmark scripts to a directory, return paths."""
    target_dir.mkdir(parents=True, exist_ok=True)
    scripts = {}
    for name, content in [
        ("cpu_bench.sh", CPU_BENCH_SCRIPT),
        ("mem_bench.sh", MEM_BENCH_SCRIPT),
        ("disk_bench.sh", DISK_BENCH_SCRIPT),
        ("net_latency_bench.sh", NET_LATENCY_BENCH_SCRIPT),
        ("net_bandwidth_bench.sh", NET_BANDWIDTH_BENCH_SCRIPT),
    ]:
        p = target_dir / name
        p.write_text("#!/bin/sh\n" + content)
        p.chmod(0o755)
        key = name.replace("_bench.sh", "")
        scripts[key] = p
    return scripts


def parse_bench_output(output: str) -> dict[str, str]:
    """Parse KEY=VALUE lines from benchmark script output."""
    result = {}
    for line in output.strip().splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip()
    return result


def run_native_benchmarks() -> dict[str, BenchResult]:
    """Run benchmarks natively (no isolation) as baseline."""
    results = {}
    with tempfile.TemporaryDirectory(prefix="vmb_") as td:
        scripts = write_bench_scripts(Path(td))

        # CPU
        t0 = time.monotonic()
        r = run(["sh", str(scripts["cpu"])], timeout=120, check=False)
        wall = time.monotonic() - t0
        parsed = parse_bench_output(r.stdout)
        elapsed_ns = int(parsed.get("CPU_ELAPSED_NS", 0))
        elapsed_s = elapsed_ns / 1e9 if elapsed_ns > 0 else wall
        results["cpu"] = BenchResult(
            metric="primes_per_sec",
            value=round(int(parsed.get("CPU_PRIMES", 0)) / max(elapsed_s, 0.001), 1),
            unit="primes/s",
            raw_output=r.stdout,
        )

        # Memory
        t0 = time.monotonic()
        r = run(["sh", str(scripts["mem"])], timeout=60, check=False)
        wall = time.monotonic() - t0
        parsed = parse_bench_output(r.stdout)
        elapsed_ns = int(parsed.get("MEM_ELAPSED_NS", 0))
        elapsed_s = elapsed_ns / 1e9 if elapsed_ns > 0 else wall
        bw = int(parsed.get("MEM_BYTES", 0)) / max(elapsed_s, 0.001)
        results["mem"] = BenchResult(
            metric="mem_bandwidth",
            value=round(bw / 1e6, 1),
            unit="MB/s",
            raw_output=r.stdout,
        )

        # Disk
        t0 = time.monotonic()
        r = run(["sh", str(scripts["disk"])], timeout=120, check=False)
        wall = time.monotonic() - t0
        parsed = parse_bench_output(r.stdout)
        elapsed_ns = int(parsed.get("DISK_ELAPSED_NS", 0))
        elapsed_s = elapsed_ns / 1e9 if elapsed_ns > 0 else wall
        bw = int(parsed.get("DISK_BYTES", 0)) / max(elapsed_s, 0.001)
        results["disk"] = BenchResult(
            metric="disk_throughput",
            value=round(bw / 1e6, 1),
            unit="MB/s",
            raw_output=r.stdout,
        )

        # Network latency
        t0 = time.monotonic()
        r = run(["sh", str(scripts["net_latency"])], timeout=30, check=False)
        wall = time.monotonic() - t0
        parsed = parse_bench_output(r.stdout)
        elapsed_ns = int(parsed.get("NET_LATENCY_NS", 0))
        elapsed_s = elapsed_ns / 1e9 if elapsed_ns > 0 else wall
        ok = int(parsed.get("NET_OK", -1))
        results["net_latency"] = BenchResult(
            metric="http_latency",
            value=round(elapsed_s * 1000, 1) if ok == 1 else -1,
            unit="ms" if ok == 1 else "failed",
            raw_output=r.stdout,
        )

        # Network bandwidth
        t0 = time.monotonic()
        r = run(["sh", str(scripts["net_bandwidth"])], timeout=60, check=False)
        wall = time.monotonic() - t0
        parsed = parse_bench_output(r.stdout)
        elapsed_ns = int(parsed.get("NETBW_ELAPSED_NS", 0))
        elapsed_s = elapsed_ns / 1e9 if elapsed_ns > 0 else wall
        bw_ok = int(parsed.get("NETBW_OK", -1))
        bw_bytes = int(parsed.get("NETBW_BYTES", 0))
        if bw_ok == 1 and bw_bytes > 0 and elapsed_s > 0:
            bw_kbps = round((bw_bytes * 8) / (elapsed_s * 1000), 1)
        else:
            bw_kbps = -1
        results["net_bandwidth"] = BenchResult(
            metric="net_bandwidth",
            value=bw_kbps,
            unit="Kbps" if bw_kbps > 0 else "failed",
            raw_output=r.stdout,
        )

    return results
