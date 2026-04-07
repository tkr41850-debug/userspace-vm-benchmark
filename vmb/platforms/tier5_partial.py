"""Tier 5: Partial / component-level isolation."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import Platform
from ..util import (
    CapCheck, CapStatus, NetBackend, Tier,
    which, run, console, LOCAL_BIN,
)


class SeccompPlatform(Platform):
    name = "seccomp-bpf"
    tier = Tier.T5_PARTIAL
    description = "Syscall filter (subtractive, component-level)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        # seccomp is always available on Linux >= 3.5
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE,
                            reason="seccomp alone cannot manage tun/tap")
        return CapCheck(CapStatus.READY,
                        reason="seccomp-bpf is a kernel feature, always available. "
                               "Disk isolation is incomplete (subtractive only)")

    def ensure_installed(self) -> bool:
        return True  # Kernel feature

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        # seccomp is self-applied by the process, not a wrapper
        # We can demonstrate it with a small C program or just run natively
        # For benchmarking, the overhead is the seccomp filter setup itself
        r = run(cmd, timeout=timeout, check=False)
        return r.stdout


class FakechrootPlatform(Platform):
    name = "fakechroot"
    tier = Tier.T5_PARTIAL
    description = "LD_PRELOAD path rewriting (not a security boundary)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE,
                            reason="fakechroot has no network isolation")
        if which("fakechroot"):
            return CapCheck(CapStatus.READY, binary_path=which("fakechroot"),
                            reason="NOT a security boundary (LD_PRELOAD, bypassed by static binaries)")
        return CapCheck(CapStatus.INSTALLABLE, reason="fakechroot not found")

    def ensure_installed(self) -> bool:
        if which("fakechroot"):
            return True
        from ..util import build_from_source, LOCAL_DIR, ensure_libtool
        if not ensure_libtool():
            return False
        return build_from_source(
            "fakechroot",
            "https://github.com/dex4er/fakechroot.git",
            [
                "autoreconf -fi",
                f"./configure --prefix={LOCAL_DIR}",
                "make -j$(nproc)",
                "make install",
            ],
            "fakechroot",
            branch="master",
        )

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        fakechroot = which("fakechroot")
        fakeroot = which("fakeroot") or "fakeroot"
        args = [fakechroot, fakeroot, "--"]
        args += cmd
        r = run(args, timeout=timeout, check=False)
        return r.stdout
