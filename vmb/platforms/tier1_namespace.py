"""Tier 1: Namespace-based platforms (native performance, need user namespaces)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import Platform
from ..util import (
    CapCheck, CapStatus, NetBackend, Tier,
    build_from_source, check_user_namespaces, which, run, console,
    LOCAL_BIN, LOCAL_DIR,
)


class BubblewrapPlatform(Platform):
    name = "bubblewrap"
    tier = Tier.T1_NAMESPACE
    description = "Lightweight namespace sandbox (bwrap)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if not check_user_namespaces():
            return CapCheck(CapStatus.UNAVAILABLE, reason="user namespaces disabled")
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="bwrap cannot use tun/tap without root")
        if which("bwrap"):
            return CapCheck(CapStatus.READY, binary_path=which("bwrap"))
        return CapCheck(CapStatus.INSTALLABLE, reason="bwrap not found")

    def ensure_installed(self) -> bool:
        if which("bwrap"):
            return True
        return build_from_source(
            "bubblewrap",
            "https://github.com/containers/bubblewrap.git",
            [
                f"meson setup build --prefix={LOCAL_DIR} 2>/dev/null || "
                f"meson build --prefix={LOCAL_DIR}",
                "ninja -C build",
                "ninja -C build install",
            ],
            "bwrap",
            branch="main",
        )

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        bwrap = which("bwrap")
        args = [
            bwrap,
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/sbin", "/sbin",
            "--symlink", "usr/lib64", "/lib64",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--tmpfs", "/run",
        ]
        # Bind the bench script directory
        if cmd and len(cmd) > 1:
            script_dir = str(Path(cmd[-1]).parent)
            args += ["--ro-bind", script_dir, script_dir]
        if network in (NetBackend.SLIRP, NetBackend.PASST):
            args += ["--unshare-net"]
        args += ["--die-with-parent", "--"]
        args += cmd
        r = run(args, timeout=timeout, check=False)
        return r.stdout


class NsjailPlatform(Platform):
    name = "nsjail"
    tier = Tier.T1_NAMESPACE
    description = "Google process isolation tool"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if not check_user_namespaces():
            return CapCheck(CapStatus.UNAVAILABLE, reason="user namespaces disabled")
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="nsjail cannot use tun/tap rootless")
        if which("nsjail"):
            return CapCheck(CapStatus.READY, binary_path=which("nsjail"))
        return CapCheck(CapStatus.INSTALLABLE, reason="nsjail not found")

    def ensure_installed(self) -> bool:
        if which("nsjail"):
            return True
        return build_from_source(
            "nsjail",
            "https://github.com/google/nsjail.git",
            [
                "make -j$(nproc)",
                f"cp nsjail {LOCAL_BIN}/",
            ],
            "nsjail",
            branch="master",
        )

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        nsjail = which("nsjail")
        args = [
            nsjail,
            "--mode", "o",
            "--user", str(65534),
            "--group", str(65534),
            "-R", "/usr",
            "-R", "/lib",
            "-R", "/lib64",
            "-R", "/bin",
            "-R", "/sbin",
            "-R", "/dev",
            "-T", "/tmp",
            "--keep_env",
            "--disable_clone_newnet", "false" if network != NetBackend.TAP else "true",
            "--really_quiet",
        ]
        if cmd and len(cmd) > 1:
            script_dir = str(Path(cmd[-1]).parent)
            args += ["-R", script_dir]
        args += ["--"]
        args += cmd
        r = run(args, timeout=timeout, check=False)
        return r.stdout


class ApptainerPlatform(Platform):
    name = "apptainer"
    tier = Tier.T1_NAMESPACE
    description = "HPC container runtime (Singularity)"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if not check_user_namespaces():
            return CapCheck(CapStatus.UNAVAILABLE, reason="user namespaces disabled")
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="apptainer cannot use tun/tap rootless")
        if which("apptainer") or which("singularity"):
            return CapCheck(CapStatus.READY,
                            binary_path=which("apptainer") or which("singularity"))
        return CapCheck(CapStatus.INSTALLABLE, reason="apptainer not found")

    def ensure_installed(self) -> bool:
        if which("apptainer") or which("singularity"):
            return True
        # Apptainer requires Go to build - check if available
        if not which("go"):
            console.print("  [yellow]Apptainer requires Go to build. Skipping.[/yellow]")
            return False
        return build_from_source(
            "apptainer",
            "https://github.com/apptainer/apptainer.git",
            [
                f"./mconfig --prefix={LOCAL_DIR} --without-suid",
                "make -C builddir -j$(nproc)",
                "make -C builddir install",
            ],
            "apptainer",
            branch="main",
        )

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        apptainer = which("apptainer") or which("singularity")
        args = [
            apptainer, "exec",
            "--contain", "--no-home",
            "--bind", "/tmp:/tmp",
        ]
        if cmd and len(cmd) > 1:
            script_dir = str(Path(cmd[-1]).parent)
            args += ["--bind", f"{script_dir}:{script_dir}"]
        if network != NetBackend.TAP:
            args += ["--net", "--network", "none"]
        # Use the host rootfs via --bind
        args += ["/"]
        args += cmd
        r = run(args, timeout=timeout, check=False)
        return r.stdout


class CharliecloudPlatform(Platform):
    name = "charliecloud"
    tier = Tier.T1_NAMESPACE
    description = "HPC unprivileged containers"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if not check_user_namespaces():
            return CapCheck(CapStatus.UNAVAILABLE, reason="user namespaces disabled")
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="charliecloud cannot use tun/tap rootless")
        if which("ch-run"):
            return CapCheck(CapStatus.READY, binary_path=which("ch-run"))
        return CapCheck(CapStatus.INSTALLABLE, reason="ch-run not found")

    def ensure_installed(self) -> bool:
        if which("ch-run"):
            return True
        return build_from_source(
            "charliecloud",
            "https://github.com/hpc/charliecloud.git",
            [
                f"./configure --prefix={LOCAL_DIR}",
                "make -j$(nproc)",
                "make install",
            ],
            "ch-run",
            branch="main",
        )

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        ch_run = which("ch-run")
        args = [ch_run, "/"]  # Use host rootfs
        if cmd and len(cmd) > 1:
            script_dir = str(Path(cmd[-1]).parent)
            args += [f"--bind={script_dir}:{script_dir}"]
        args += ["--"]
        args += cmd
        r = run(args, timeout=timeout, check=False)
        return r.stdout


class PodmanPlatform(Platform):
    name = "podman"
    tier = Tier.T1_NAMESPACE
    description = "Rootless OCI container runtime"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if not check_user_namespaces():
            return CapCheck(CapStatus.UNAVAILABLE, reason="user namespaces disabled")
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="podman cannot use tun/tap rootless")
        # Podman always needs /etc/subuid regardless of install status
        import os
        user = os.environ.get("USER", "")
        try:
            subuid = Path("/etc/subuid").read_text()
            if user not in subuid and str(os.getuid()) not in subuid:
                return CapCheck(CapStatus.UNAVAILABLE,
                                reason=f"user {user} not in /etc/subuid (needs root to set up)")
        except FileNotFoundError:
            return CapCheck(CapStatus.UNAVAILABLE,
                            reason="/etc/subuid not found (needs root to create)")
        if which("podman"):
            return CapCheck(CapStatus.READY, binary_path=which("podman"))
        return CapCheck(CapStatus.INSTALLABLE, reason="podman not found")

    def ensure_installed(self) -> bool:
        if which("podman"):
            return True
        # Podman is complex to build from source (Go + many deps)
        console.print("  [yellow]Podman requires system-level setup (/etc/subuid). Skipping build.[/yellow]")
        return False

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        podman = which("podman")
        net_flag = "--network=none"
        if network == NetBackend.SLIRP:
            net_flag = "--network=slirp4netns"
        elif network == NetBackend.PASST:
            net_flag = "--network=pasta"

        args = [podman, "run", "--rm", net_flag]
        if cmd and len(cmd) > 1:
            script_dir = str(Path(cmd[-1]).parent)
            args += ["-v", f"{script_dir}:{script_dir}:ro"]
        args += ["alpine:latest"]
        args += cmd
        r = run(args, timeout=timeout, check=False)
        return r.stdout


class FirejailPlatform(Platform):
    name = "firejail"
    tier = Tier.T1_NAMESPACE
    description = "Desktop application sandbox"

    def check_capability(self, network: NetBackend) -> CapCheck:
        if not check_user_namespaces():
            return CapCheck(CapStatus.UNAVAILABLE, reason="user namespaces disabled")
        if network == NetBackend.TAP:
            return CapCheck(CapStatus.UNAVAILABLE, reason="firejail cannot use tun/tap rootless")
        if which("firejail"):
            return CapCheck(CapStatus.READY, binary_path=which("firejail"))
        return CapCheck(CapStatus.INSTALLABLE, reason="firejail not found")

    def ensure_installed(self) -> bool:
        if which("firejail"):
            return True
        return build_from_source(
            "firejail",
            "https://github.com/netblue30/firejail.git",
            [
                f"./configure --prefix={LOCAL_DIR} --enable-force-nonroot",
                "make -j$(nproc)",
                "make install",
            ],
            "firejail",
            branch="master",
        )

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        firejail = which("firejail")
        args = [
            firejail,
            "--noprofile",
            "--quiet",
            "--private",
            "--net=none" if network != NetBackend.TAP else "",
        ]
        args = [a for a in args if a]  # Remove empty strings
        args += ["--"]
        args += cmd
        r = run(args, timeout=timeout, check=False)
        return r.stdout
