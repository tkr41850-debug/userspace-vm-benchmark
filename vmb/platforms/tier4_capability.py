"""Tier 4: Capability-based / language runtimes (Wasm, Deno)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from .base import Platform
from ..util import (
    CapCheck, CapStatus, NetBackend, Tier,
    build_from_source, which, run, console, LOCAL_BIN, LOCAL_DIR, HOME,
)
from ..benchmarks.workloads import CPU_BENCH_SCRIPT, MEM_BENCH_SCRIPT, DISK_BENCH_SCRIPT


class WasmtimePlatform(Platform):
    name = "wasmtime"
    tier = Tier.T4_CAPABILITY
    description = "Wasm runtime (Bytecode Alliance)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="Wasm runtimes don't use tun/tap")
        # Wasmtime can only run Wasm binaries, not shell scripts
        # We'd need to compile benchmarks to Wasm first
        if which("wasmtime"):
            return CapCheck(CapStatus.READY, binary_path=which("wasmtime"),
                            reason="Can only run Wasm binaries (not arbitrary shell scripts)")
        return CapCheck(CapStatus.INSTALLABLE, reason="wasmtime not found")

    def ensure_installed(self) -> bool:
        if which("wasmtime"):
            return True
        console.print("  [yellow]Installing wasmtime...[/yellow]")
        try:
            run("curl https://wasmtime.dev/install.sh -sSf | bash", timeout=120)
            # wasmtime installs to ~/.wasmtime/bin
            wt = HOME / ".wasmtime" / "bin" / "wasmtime"
            if wt.exists():
                import shutil
                shutil.copy2(str(wt), str(LOCAL_BIN / "wasmtime"))
            return which("wasmtime") is not None
        except Exception:
            return False

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        # Wasmtime can't run shell scripts - would need Wasm compilation
        return "WASM_SKIP=cannot_run_shell_scripts"


class WasmerPlatform(Platform):
    name = "wasmer"
    tier = Tier.T4_CAPABILITY
    description = "Wasm runtime (Wasmer Inc)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="Wasm runtimes don't use tun/tap")
        if which("wasmer"):
            return CapCheck(CapStatus.READY, binary_path=which("wasmer"),
                            reason="Can only run Wasm binaries")
        return CapCheck(CapStatus.INSTALLABLE, reason="wasmer not found")

    def ensure_installed(self) -> bool:
        if which("wasmer"):
            return True
        console.print("  [yellow]Installing wasmer...[/yellow]")
        try:
            run("curl https://get.wasmer.io -sSfL | sh", timeout=120)
            wasmer = HOME / ".wasmer" / "bin" / "wasmer"
            if wasmer.exists():
                import shutil
                shutil.copy2(str(wasmer), str(LOCAL_BIN / "wasmer"))
            return which("wasmer") is not None
        except Exception:
            return False

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        return "WASM_SKIP=cannot_run_shell_scripts"


class WasmEdgePlatform(Platform):
    name = "wasmedge"
    tier = Tier.T4_CAPABILITY
    description = "Wasm runtime (CNCF, claims fastest)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="Wasm runtimes don't use tun/tap")
        if which("wasmedge"):
            return CapCheck(CapStatus.READY, binary_path=which("wasmedge"),
                            reason="Can only run Wasm binaries")
        return CapCheck(CapStatus.INSTALLABLE, reason="wasmedge not found")

    def ensure_installed(self) -> bool:
        if which("wasmedge"):
            return True
        console.print("  [yellow]Installing WasmEdge...[/yellow]")
        try:
            run(f"curl -sSf https://raw.githubusercontent.com/WasmEdge/WasmEdge/master/utils/install.sh "
                f"| bash -s -- -p {LOCAL_DIR}", timeout=120)
            return which("wasmedge") is not None
        except Exception:
            return False

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        return "WASM_SKIP=cannot_run_shell_scripts"


class WamrPlatform(Platform):
    name = "wamr"
    tier = Tier.T4_CAPABILITY
    description = "Wasm Micro Runtime (Bytecode Alliance, tiny footprint)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="Wasm runtimes don't use tun/tap")
        if which("iwasm"):
            return CapCheck(CapStatus.READY, binary_path=which("iwasm"),
                            reason="Can only run Wasm binaries")
        return CapCheck(CapStatus.INSTALLABLE, reason="iwasm not found")

    def ensure_installed(self) -> bool:
        if which("iwasm"):
            return True
        return build_from_source(
            "wamr",
            "https://github.com/bytecodealliance/wasm-micro-runtime.git",
            [
                f"cd product-mini/platforms/linux && mkdir -p build && cd build && "
                f"cmake .. -DCMAKE_INSTALL_PREFIX={LOCAL_DIR} && "
                f"make -j$(nproc) && make install",
            ],
            "iwasm",
            branch="main",
        )

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        return "WASM_SKIP=cannot_run_shell_scripts"


class DenoPlatform(Platform):
    name = "deno"
    tier = Tier.T4_CAPABILITY
    description = "Secure JS/TS runtime (deny-by-default permissions)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="Deno doesn't use tun/tap")
        if which("deno"):
            return CapCheck(CapStatus.READY, binary_path=which("deno"),
                            reason="Can only run JS/TS (not shell scripts)")
        return CapCheck(CapStatus.INSTALLABLE, reason="deno not found")

    def ensure_installed(self) -> bool:
        if which("deno"):
            return True
        console.print("  [yellow]Installing deno...[/yellow]")
        try:
            run(f"curl -fsSL https://deno.land/install.sh | DENO_INSTALL={LOCAL_DIR} sh",
                timeout=120)
            return which("deno") is not None
        except Exception:
            return False

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        return "DENO_SKIP=cannot_run_shell_scripts"
