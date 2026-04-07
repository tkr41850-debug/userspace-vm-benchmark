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


def install_slirp() -> bool:
    """Build slirp4netns from source."""
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
            f"make -j$(nproc) prefix={LOCAL_BIN}/.. CFLAGS='-std=gnu11 -O2 -pie -fPIE' && make install prefix={LOCAL_BIN}/..",
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
