"""Tier 3: Ptrace/syscall-interception platforms (no namespaces needed)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import Platform
from ..util import (
    CapCheck, CapStatus, NetBackend, Tier,
    build_from_source, which, run, console, LOCAL_BIN, LOCAL_DIR,
)


class GvisorPlatform(Platform):
    name = "gvisor"
    tier = Tier.T3_PTRACE
    description = "Userspace kernel (ptrace mode, no KVM)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if network == NetBackend.TAP:
            from ..util import check_tun_tap
            if not check_tun_tap():
                return CapCheck(CapStatus.UNAVAILABLE,
                                reason="gvisor tap needs /dev/net/tun")
        if which("runsc"):
            return CapCheck(CapStatus.READY, binary_path=which("runsc"))
        return CapCheck(CapStatus.INSTALLABLE, reason="runsc not found")

    def ensure_installed(self) -> bool:
        if which("runsc"):
            return True
        # gVisor distributes pre-built binaries
        console.print("  [yellow]Downloading gVisor (runsc) binary...[/yellow]")
        try:
            run(f"curl -fsSL https://storage.googleapis.com/gvisor/releases/release/latest/x86_64/runsc "
                f"-o {LOCAL_BIN}/runsc && chmod +x {LOCAL_BIN}/runsc", timeout=120)
            return which("runsc") is not None
        except Exception as e:
            console.print(f"  [red]Failed to download gVisor: {e}[/red]")
            return False

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        runsc = which("runsc")
        # gVisor needs an OCI bundle - complex setup
        # For direct benchmark, we use runsc do which runs a command directly
        args = [
            runsc,
            "--platform=ptrace",
            "--network=sandbox" if network != NetBackend.TAP else "--network=host",
            "--rootless",
            "do",
        ]
        args += cmd
        try:
            r = run(args, timeout=timeout, check=False)
            return r.stdout
        except Exception as e:
            return f"GVISOR_ERROR={e}"


class ProotPlatform(Platform):
    name = "proot"
    tier = Tier.T3_PTRACE
    description = "Ptrace-based userspace chroot (path translation)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="proot cannot use tun/tap")
        if network == NetBackend.SLIRP or network == NetBackend.PASST:
            # PRoot has no network isolation at all
            return CapCheck(CapStatus.READY if which("proot") else CapStatus.INSTALLABLE,
                            reason="proot has no network isolation (path-only confinement)",
                            binary_path=which("proot"))
        if which("proot"):
            return CapCheck(CapStatus.READY, binary_path=which("proot"))
        return CapCheck(CapStatus.INSTALLABLE, reason="proot not found")

    def ensure_installed(self) -> bool:
        if which("proot"):
            return True
        return build_from_source(
            "proot",
            "https://github.com/proot-me/proot.git",
            [
                "make -C src -j$(nproc)",
                f"cp src/proot {LOCAL_BIN}/",
            ],
            "proot",
            branch="master",
        )

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        proot = which("proot")
        args = [proot, "-0"]  # Fake root
        if cmd and len(cmd) > 1:
            script_dir = str(Path(cmd[-1]).parent)
            args += ["-b", f"{script_dir}:{script_dir}"]
        args += cmd
        r = run(args, timeout=timeout, check=False)
        return r.stdout


class MboxPlatform(Platform):
    name = "mbox"
    tier = Tier.T3_PTRACE
    description = "MIT PDOS sandbox (ptrace + seccomp/BPF)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="mbox cannot use tun/tap")
        if which("mbox"):
            return CapCheck(CapStatus.READY, binary_path=which("mbox"))
        return CapCheck(CapStatus.INSTALLABLE, reason="mbox not found")

    def ensure_installed(self) -> bool:
        if which("mbox"):
            return True
        return build_from_source(
            "mbox",
            "https://github.com/tsgates/mbox.git",
            [
                "cd src && make -j$(nproc)",
                f"cp src/mbox {LOCAL_BIN}/",
            ],
            "mbox",
            branch="master",
        )

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        mbox = which("mbox")
        args = [mbox, "-n", "--"]  # -n = no network
        args += cmd
        r = run(args, timeout=timeout, check=False)
        return r.stdout


class UdockerPlatform(Platform):
    name = "udocker"
    tier = Tier.T3_PTRACE
    description = "Python wrapper (PRoot/Fakechroot/runc backends)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="udocker cannot use tun/tap")
        if which("udocker"):
            return CapCheck(CapStatus.READY, binary_path=which("udocker"))
        return CapCheck(CapStatus.INSTALLABLE, reason="udocker not found")

    def ensure_installed(self) -> bool:
        if which("udocker"):
            return True
        console.print("  [yellow]Installing udocker via pip...[/yellow]")
        try:
            run(f"pip3 install --user udocker 2>/dev/null || "
                f"python3 -m pip install --user udocker", timeout=120)
            return which("udocker") is not None
        except Exception:
            return False

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        udocker = which("udocker")
        # udocker needs a container created first
        args = [udocker, "run", "--rm"]
        args += cmd
        r = run(args, timeout=timeout, check=False)
        return r.stdout
