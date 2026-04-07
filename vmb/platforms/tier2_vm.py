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

    def _build_initramfs(self, script_path: str, work_dir: str) -> Optional[str]:
        """Build a minimal initramfs that runs the bench script and powers off."""
        import shutil, stat
        initrd = os.path.join(work_dir, "initrd.cpio.gz")
        root = os.path.join(work_dir, "initrd_root")

        # Build directory tree
        for d in ("bin", "proc", "sys", "dev", "tmp"):
            os.makedirs(os.path.join(root, d), exist_ok=True)

        # Copy busybox or sh
        sh = shutil.which("busybox") or shutil.which("sh") or "/bin/sh"
        dest_sh = os.path.join(root, "bin", "sh")
        shutil.copy2(sh, dest_sh)
        os.chmod(dest_sh, 0o755)

        # Copy the bench script
        import shutil as _sh
        _sh.copy2(script_path, os.path.join(root, "bench.sh"))

        # Write /init
        init_script = (
            "#!/bin/sh\n"
            "mount -t proc proc /proc\n"
            "mount -t sysfs sysfs /sys\n"
            "mount -t devtmpfs devtmpfs /dev 2>/dev/null || "
            "  mknod /dev/null c 1 3\n"
            "sh /bench.sh\n"
            "echo VMB_DONE=1\n"
            "poweroff -f 2>/dev/null || echo o > /proc/sysrq-trigger\n"
        )
        init_path = os.path.join(root, "init")
        with open(init_path, "w") as f:
            f.write(init_script)
        os.chmod(init_path, 0o755)

        # Pack cpio
        import subprocess
        r = subprocess.run(
            ["sh", "-c",
             f"cd {root} && find . | cpio -o -H newc 2>/dev/null | gzip > {initrd}"],
            capture_output=True, timeout=30,
        )
        return initrd if r.returncode == 0 and os.path.exists(initrd) else None

    def run_command(self, cmd: list[str], network: NetBackend,
                    disk_path: Optional[Path] = None, timeout: int = 120) -> str:
        qemu = which("qemu-system-x86_64")
        if not cmd:
            return ""

        script_path = cmd[-1]

        import glob, tempfile
        kernels = sorted(glob.glob("/boot/vmlinuz*"))
        if not kernels:
            return "QEMU_SKIP=no_kernel"
        kernel = kernels[0]

        with tempfile.TemporaryDirectory(prefix="vmb_qemu_") as td:
            initrd = self._build_initramfs(script_path, td)
            if not initrd:
                return "QEMU_SKIP=initrd_build_failed"

            net_args = self._get_net_args(network)
            args = [
                qemu,
                "-machine", "accel=tcg",
                "-m", "256M",
                "-nographic",
                "-no-reboot",
                "-kernel", kernel,
                "-initrd", initrd,
                "-append", "console=ttyS0 quiet",
                "-serial", "stdio",
            ] + net_args

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
