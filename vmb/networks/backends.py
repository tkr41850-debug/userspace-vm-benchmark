"""Network backend detection and setup: slirp4netns, passt, tun/tap."""
from __future__ import annotations

from ..util import (CapCheck, CapStatus, NetBackend, build_from_source,
                    check_tun_tap, which, console, LOCAL_BIN)


def check_slirp() -> CapCheck:
    """Check if slirp4netns is available or installable."""
    path = which("slirp4netns")
    if path:
        return CapCheck(CapStatus.READY, binary_path=path)
    return CapCheck(CapStatus.INSTALLABLE, reason="slirp4netns not found, can build from source")


def _ensure_libslirp() -> bool:
    """Build libslirp from source into ~/.local/ if not present."""
    from ..util import SRC_DIR, LOCAL_DIR, LOCAL_LIB, ensure_cmake_ninja
    import os, subprocess, multiprocessing
    if (LOCAL_LIB / "pkgconfig" / "slirp.pc").exists():
        return True
    if not ensure_cmake_ninja():
        return False
    src = SRC_DIR / "libslirp"
    build_env = {**os.environ,
                 "PKG_CONFIG_PATH": str(LOCAL_LIB / "pkgconfig")}
    try:
        if not src.exists():
            r = subprocess.run(
                ["git", "clone", "--depth", "1",
                 "https://gitlab.freedesktop.org/slirp/libslirp.git", str(src)],
                timeout=120, capture_output=True, text=True, env=build_env,
            )
            if r.returncode != 0:
                return False
        nproc = multiprocessing.cpu_count()
        for cmd in [
            f"meson setup build --prefix={LOCAL_DIR} --default-library=static",
            f"ninja -C build",
            f"ninja -C build install",
        ]:
            r = subprocess.run(["sh", "-c", cmd], cwd=str(src), timeout=300,
                               capture_output=True, text=True, env=build_env)
            if r.returncode != 0:
                print(f"\n[BUILD FAIL] libslirp:\n{r.stderr[-2000:]}", flush=True)
                return False
        return (LOCAL_LIB / "pkgconfig" / "slirp.pc").exists()
    except subprocess.TimeoutExpired:
        return False


def install_slirp() -> bool:
    """Build slirp4netns from source."""
    if not _ensure_libslirp():
        return False
    return build_from_source(
        "slirp4netns",
        "https://github.com/rootless-containers/slirp4netns.git",
        [
            "autoreconf -fi",
            f"./configure --prefix={LOCAL_BIN}/..",
            f"make -j$(nproc) && make install",
        ],
        "slirp4netns",
        branch="master",
    )


def check_passt() -> CapCheck:
    """Check if passt/pasta is available or installable."""
    for name in ("passt", "pasta"):
        path = which(name)
        if path:
            return CapCheck(CapStatus.READY, binary_path=path)
    return CapCheck(CapStatus.INSTALLABLE, reason="passt not found, can build from source")


def install_passt() -> bool:
    """Build passt from source."""
    return build_from_source(
        "passt",
        "https://passt.top/passt",
        [
            "sed -i 's/-std=c11/-std=gnu11/g' Makefile",
            f"make -j$(nproc) prefix={LOCAL_BIN}/..",
            f"make install prefix={LOCAL_BIN}/..",
        ],
        "passt",
        branch="master",
    )


def check_tap() -> CapCheck:
    """Check if tun/tap device is accessible (requires /dev/net/tun)."""
    if check_tun_tap():
        return CapCheck(CapStatus.READY, reason="/dev/net/tun accessible")
    return CapCheck(CapStatus.UNAVAILABLE,
                    reason="/dev/net/tun not accessible (needs root or CAP_NET_ADMIN)")


def check_network(backend: NetBackend) -> CapCheck:
    """Check if a network backend is available."""
    match backend:
        case NetBackend.SLIRP:
            return check_slirp()
        case NetBackend.PASST:
            return check_passt()
        case NetBackend.TAP:
            return check_tap()


def ensure_network(backend: NetBackend) -> CapCheck:
    """Ensure a network backend is installed, building if necessary."""
    check = check_network(backend)
    if check.status == CapStatus.READY:
        return check
    if check.status == CapStatus.UNAVAILABLE:
        return check
    # INSTALLABLE - try to build
    match backend:
        case NetBackend.SLIRP:
            if install_slirp():
                return check_network(backend)
        case NetBackend.PASST:
            if install_passt():
                return check_network(backend)
        case _:
            pass
    return CapCheck(CapStatus.UNAVAILABLE, reason=f"Failed to install {backend.value}")


def get_net_flag(platform_name: str, backend: NetBackend) -> list[str]:
    """Get the network CLI flags for a platform + backend combination.

    Returns the command-line arguments to pass to the isolation tool.
    Different platforms have different syntax for network configuration.
    """
    match backend:
        case NetBackend.SLIRP:
            slirp = which("slirp4netns") or "slirp4netns"
            match platform_name:
                case "qemu-tcg":
                    return ["-nic", "user,restrict=off"]
                case "uml":
                    return [f"eth0=slirp,,{slirp}"]
                case "bochs":
                    return []  # Bochs network via config file
                case "podman":
                    return ["--network=slirp4netns"]
                case "gvisor":
                    return ["--network=sandbox"]
                case _:
                    return ["--unshare-net"]  # namespace-based
        case NetBackend.PASST:
            match platform_name:
                case "qemu-tcg":
                    return ["-nic", "user,restrict=off"]  # passt via QEMU
                case "podman":
                    return ["--network=pasta"]
                case _:
                    return ["--unshare-net"]
        case NetBackend.TAP:
            match platform_name:
                case "qemu-tcg":
                    return ["-nic", "tap,ifname=tap0,script=no,downscript=no"]
                case _:
                    return []  # Most namespace tools can't use tap without root
