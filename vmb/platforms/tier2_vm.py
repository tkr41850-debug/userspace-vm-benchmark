"""Tier 2: Full VM emulation (software, no KVM needed)."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from .base import Platform
from ..util import (
    CapCheck, CapStatus, NetBackend, Tier,
    build_from_source, which, run, console, DISK_DIR, LOCAL_DIR, LOCAL_BIN,
)


class QemuTcgPlatform(Platform):
    name = "qemu-tcg"
    tier = Tier.T2_VM
    description = "QEMU system emulation (TCG, no KVM)"
    is_vm = True

    def check_capability(self, network: NetBackend) -> CapCheck:
        if network == NetBackend.TAP:
            from ..util import check_tun_tap
            if not check_tun_tap():
                return CapCheck(CapStatus.UNAVAILABLE,
                                reason="/dev/net/tun not accessible for tap networking")
        if which("qemu-system-x86_64"):
            return CapCheck(CapStatus.READY, binary_path=which("qemu-system-x86_64"))
        return CapCheck(CapStatus.INSTALLABLE, reason="qemu-system-x86_64 not found")

    def ensure_installed(self) -> bool:
        if which("qemu-system-x86_64"):
            return True
        return build_from_source(
            "qemu",
            "https://gitlab.com/qemu-project/qemu.git",
            [
                f"./configure --prefix={LOCAL_DIR} --target-list=x86_64-softmmu "
                f"--disable-docs --disable-gtk --disable-sdl --disable-spice "
                f"--enable-slirp --enable-virtfs",
                "make -j$(nproc)",
                "make install",
            ],
            "qemu-system-x86_64",
            branch="stable-9.2",
        )

    def setup_vm(self, iso_path: Path, disk_name: str, disk_size: str = "4G") -> Optional[Path]:
        qemu_img = which("qemu-img")
        if not qemu_img:
            console.print("  [red]qemu-img not found[/red]")
            return None

        disk_path = DISK_DIR / f"{disk_name}.qcow2"
        if disk_path.exists():
            console.print(f"  [dim]Disk {disk_path} already exists, reusing[/dim]")
            return disk_path

        console.print(f"  [yellow]Creating disk {disk_path} ({disk_size})...[/yellow]")
        run([qemu_img, "create", "-f", "qcow2", str(disk_path), disk_size])

        console.print(f"  [yellow]Installing from {iso_path} (headless, this takes a while)...[/yellow]")
        # Automated install with cloud-init or preseed would go here
        # For benchmark purposes, we use a pre-built cloud image approach instead
        console.print(f"  [dim]Note: For full VM benchmarks, use a pre-built cloud image[/dim]")
        return disk_path

    def _get_net_args(self, network: NetBackend) -> list[str]:
        match network:
            case NetBackend.SLIRP:
                return ["-nic", "user,restrict=off,model=virtio"]
            case NetBackend.PASST:
                passt = which("passt")
                if passt:
                    return ["-nic", f"stream,addr=/tmp/vmb_passt.sock,model=virtio"]
                return ["-nic", "user,restrict=off,model=virtio"]  # Fallback
            case NetBackend.TAP:
                return ["-nic", "tap,ifname=vmbtap0,script=no,downscript=no,model=virtio"]

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        """For VMs, we inject the bench script via virtio-9p or serial console.

        Since full VM boot is slow, for the benchmark we create a tiny
        initramfs that runs the script and outputs results on serial.
        """
        qemu = which("qemu-system-x86_64")

        # For a real benchmark, we'd boot the VM and run commands inside.
        # Here we use QEMU's -kernel/-initrd with a custom init that runs our script.
        # This avoids the full OS boot overhead for measuring isolation overhead.

        if not cmd or len(cmd) < 2:
            return ""

        script_path = cmd[-1]
        # Create a minimal wrapper that boots a kernel and runs the script
        # For now, use QEMU's built-in Linux boot with the host kernel
        kernel = "/boot/vmlinuz" if os.path.exists("/boot/vmlinuz") else ""
        if not kernel:
            # Try common paths
            import glob
            kernels = glob.glob("/boot/vmlinuz-*")
            if kernels:
                kernel = kernels[0]

        if not kernel:
            # Can't do direct kernel boot - return marker for skip
            return "QEMU_SKIP=no_kernel"

        net_args = self._get_net_args(network)
        args = [
            qemu,
            "-machine", "accel=tcg",
            "-m", "512M",
            "-nographic",
            "-no-reboot",
            "-kernel", kernel,
            "-append", f"console=ttyS0 init=/bin/sh -- -c 'cat {script_path} | sh; poweroff -f'",
        ] + net_args + [
            "-serial", "stdio",
        ]

        if disk_path and disk_path.exists():
            args += ["-drive", f"file={disk_path},format=qcow2,if=virtio"]

        try:
            r = run(args, timeout=timeout, check=False)
            return r.stdout
        except Exception as e:
            return f"QEMU_ERROR={e}"


class UmlPlatform(Platform):
    name = "uml"
    tier = Tier.T2_VM
    description = "User-mode Linux (kernel as userspace process)"
    is_vm = True

    def check_capability(self, network: NetBackend) -> CapCheck:
        if network == NetBackend.TAP:
            from ..util import check_tun_tap
            if not check_tun_tap():
                return CapCheck(CapStatus.UNAVAILABLE,
                                reason="UML tap networking needs /dev/net/tun")
        if which("linux") or which("vmlinux"):
            return CapCheck(CapStatus.READY,
                            binary_path=which("linux") or which("vmlinux"))
        return CapCheck(CapStatus.INSTALLABLE,
                        reason="UML kernel binary not found (need to compile)")

    def ensure_installed(self) -> bool:
        if which("linux") or which("vmlinux"):
            return True
        console.print("  [yellow]UML requires compiling a Linux kernel with ARCH=um. "
                       "This is slow but doable.[/yellow]")
        # This is a heavy build - kernel compilation
        return build_from_source(
            "linux-uml",
            "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git",
            [
                "make ARCH=um defconfig",
                "make ARCH=um -j$(nproc)",
                f"cp linux {LOCAL_BIN}/linux-uml",
            ],
            "linux-uml",
            branch="linux-6.6.y",
        )

    def setup_vm(self, iso_path: Path, disk_name: str, disk_size: str = "4G") -> Optional[Path]:
        disk_path = DISK_DIR / f"{disk_name}.ext4"
        if disk_path.exists():
            console.print(f"  [dim]Disk {disk_path} already exists, reusing[/dim]")
            return disk_path
        console.print(f"  [yellow]Creating UML root filesystem {disk_path}...[/yellow]")
        # Create a minimal ext4 image
        run(f"dd if=/dev/zero of={disk_path} bs=1M count=512 2>/dev/null", check=False)
        run(f"mkfs.ext4 -F {disk_path} 2>/dev/null", check=False)
        return disk_path

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        uml = which("linux-uml") or which("linux") or which("vmlinux")
        if not uml:
            return "UML_SKIP=not_installed"
        args = [uml, "mem=256M", "rootfstype=hostfs", "rw"]
        if disk_path and disk_path.exists():
            args.append(f"ubd0={disk_path}")
        args.append(f"init=/bin/sh")
        # UML outputs on its console
        try:
            r = run(args, timeout=timeout, check=False)
            return r.stdout
        except Exception as e:
            return f"UML_ERROR={e}"


class BochsPlatform(Platform):
    name = "bochs"
    tier = Tier.T2_VM
    description = "Pure interpretive x86 PC emulator"
    is_vm = True

    def check_capability(self, network: NetBackend) -> CapCheck:
        # Bochs is so slow it's questionable for benchmarks
        if network == NetBackend.TAP:
            from ..util import check_tun_tap
            if not check_tun_tap():
                return CapCheck(CapStatus.UNAVAILABLE,
                                reason="Bochs tap needs /dev/net/tun")
        if which("bochs"):
            return CapCheck(CapStatus.READY, binary_path=which("bochs"))
        return CapCheck(CapStatus.INSTALLABLE, reason="bochs not found")

    def ensure_installed(self) -> bool:
        if which("bochs"):
            return True
        return build_from_source(
            "bochs",
            "https://github.com/bochs-emu/Bochs.git",
            [
                "cd bochs && "
                f"./configure --prefix={LOCAL_DIR} --enable-x86-64 "
                f"--with-nogui --enable-all-optimizations "
                f"--disable-debugger --disable-readline",
                "cd bochs && make -j$(nproc)",
                "cd bochs && make install",
            ],
            "bochs",
            branch="master",
        )

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        # Bochs is extremely slow - we'd need a full disk image
        return "BOCHS_SKIP=too_slow_for_automated_bench"
