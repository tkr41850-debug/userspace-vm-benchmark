"""Base class for isolation platforms."""
from __future__ import annotations

import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from ..util import (
    BenchResult, CapCheck, CapStatus, NetBackend, PlatformNetResult, Tier,
    console, run, which, DISK_DIR,
)
from ..benchmarks.workloads import (
    write_bench_scripts, parse_bench_output,
)


class Platform(ABC):
    """Base class for an isolation platform."""

    name: str
    tier: Tier
    description: str
    is_vm: bool = False

    @abstractmethod
    def check_capability(self, network: NetBackend) -> CapCheck:
        """Check if this platform+network combo can run."""
        ...

    @abstractmethod
    def ensure_installed(self) -> bool:
        """Ensure the platform binary is installed, building if needed."""
        ...

    def setup_vm(self, iso_path: Path, disk_name: str, disk_size: str = "4G") -> Optional[Path]:
        """For VM platforms: set up a disk image from an ISO. Returns disk path."""
        return None

    @abstractmethod
    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None,
                    timeout: int = 120) -> str:
        """Run a command inside the isolated environment. Returns stdout."""
        ...

    def run_benchmarks(self, network: NetBackend,
                       disk_path: Optional[Path] = None) -> PlatformNetResult:
        """Run all benchmarks inside this platform with the given network."""
        result = PlatformNetResult(
            platform=self.name,
            network=network.value,
            tier=self.tier,
            cap_check=CapCheck(CapStatus.READY),
        )

        with tempfile.TemporaryDirectory(prefix=f"vmb_{self.name}_") as td:
            scripts = write_bench_scripts(Path(td))

            for bench_name, script_path, timeout in [
                ("cpu", scripts["cpu"], 180),
                ("mem", scripts["mem"], 60),
                ("disk", scripts["disk"], 120),
                ("net_latency", scripts["net_latency"], 30),
                ("net_bandwidth", scripts["net_bandwidth"], 60),
            ]:
                try:
                    t0 = time.monotonic()
                    output = self.run_command(
                        ["sh", str(script_path)],
                        network=network,
                        disk_path=disk_path,
                        timeout=timeout,
                    )
                    wall = time.monotonic() - t0
                    parsed = parse_bench_output(output)

                    match bench_name:
                        case "cpu":
                            elapsed_ns = int(parsed.get("CPU_ELAPSED_NS", 0))
                            elapsed_s = elapsed_ns / 1e9 if elapsed_ns > 0 else wall
                            primes = int(parsed.get("CPU_PRIMES", 0))
                            result.cpu_result = BenchResult(
                                metric="primes_per_sec",
                                value=round(primes / max(elapsed_s, 0.001), 1),
                                unit="primes/s",
                                raw_output=output,
                            )
                        case "mem":
                            elapsed_ns = int(parsed.get("MEM_ELAPSED_NS", 0))
                            elapsed_s = elapsed_ns / 1e9 if elapsed_ns > 0 else wall
                            bw = int(parsed.get("MEM_BYTES", 0)) / max(elapsed_s, 0.001)
                            result.mem_result = BenchResult(
                                metric="mem_bandwidth",
                                value=round(bw / 1e6, 1),
                                unit="MB/s",
                                raw_output=output,
                            )
                        case "disk":
                            elapsed_ns = int(parsed.get("DISK_ELAPSED_NS", 0))
                            elapsed_s = elapsed_ns / 1e9 if elapsed_ns > 0 else wall
                            bw = int(parsed.get("DISK_BYTES", 0)) / max(elapsed_s, 0.001)
                            result.disk_result = BenchResult(
                                metric="disk_throughput",
                                value=round(bw / 1e6, 1),
                                unit="MB/s",
                                raw_output=output,
                            )
                        case "net_latency":
                            elapsed_ns = int(parsed.get("NET_LATENCY_NS", 0))
                            elapsed_s = elapsed_ns / 1e9 if elapsed_ns > 0 else wall
                            ok = int(parsed.get("NET_OK", -1))
                            result.net_latency_result = BenchResult(
                                metric="http_latency",
                                value=round(elapsed_s * 1000, 1) if ok == 1 else -1,
                                unit="ms" if ok == 1 else "failed",
                                raw_output=output,
                            )
                        case "net_bandwidth":
                            elapsed_ns = int(parsed.get("NETBW_ELAPSED_NS", 0))
                            elapsed_s = elapsed_ns / 1e9 if elapsed_ns > 0 else wall
                            bw_ok = int(parsed.get("NETBW_OK", -1))
                            bw_bytes = int(parsed.get("NETBW_BYTES", 0))
                            if bw_ok == 1 and bw_bytes > 0 and elapsed_s > 0:
                                bw_kbps = round((bw_bytes * 8) / (elapsed_s * 1000), 1)
                            else:
                                bw_kbps = -1
                            result.net_bandwidth_result = BenchResult(
                                metric="net_bandwidth",
                                value=bw_kbps,
                                unit="Kbps" if bw_kbps > 0 else "failed",
                                raw_output=output,
                            )
                except Exception as e:
                    result.errors.append(f"{bench_name}: {e}")

        return result
