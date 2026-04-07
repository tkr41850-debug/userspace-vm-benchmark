"""Shared types and utilities for the benchmark suite."""
from __future__ import annotations

import enum
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()

# ── Directories ──────────────────────────────────────────────────────────────

HOME = Path.home()
SRC_DIR = HOME / "src"
LOCAL_DIR = HOME / ".local"
LOCAL_BIN = LOCAL_DIR / "bin"
LOCAL_ETC = LOCAL_DIR / "etc"
LOCAL_LIB = LOCAL_DIR / "lib"
DISK_DIR = HOME / "disks"

for d in (SRC_DIR, LOCAL_BIN, LOCAL_ETC, LOCAL_LIB, DISK_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Ensure ~/.local/bin is on PATH for child processes
os.environ["PATH"] = f"{LOCAL_BIN}:{os.environ.get('PATH', '')}"
os.environ["LD_LIBRARY_PATH"] = f"{LOCAL_LIB}:{os.environ.get('LD_LIBRARY_PATH', '')}"


# ── Enums ────────────────────────────────────────────────────────────────────

class Tier(enum.Enum):
    T1_NAMESPACE = "tier1-namespace"
    T2_VM = "tier2-vm"
    T3_PTRACE = "tier3-ptrace"
    T4_CAPABILITY = "tier4-capability"
    T5_PARTIAL = "tier5-partial"


class NetBackend(enum.Enum):
    SLIRP = "slirp"
    PASST = "passt"
    TAP = "tun/tap"


class CapStatus(enum.Enum):
    READY = "ready"
    INSTALLABLE = "installable"
    UNAVAILABLE = "unavailable"


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class CapCheck:
    """Result of a capability check for a platform+network combo."""
    status: CapStatus
    reason: str = ""
    binary_path: Optional[str] = None


@dataclass
class BenchResult:
    """Result of a single benchmark run."""
    metric: str
    value: float
    unit: str
    raw_output: str = ""


@dataclass
class PlatformNetResult:
    """Full result for one platform + network combination."""
    platform: str
    network: str
    tier: Tier
    cap_check: CapCheck
    cpu_result: Optional[BenchResult] = None
    mem_result: Optional[BenchResult] = None
    disk_result: Optional[BenchResult] = None
    net_latency_result: Optional[BenchResult] = None
    net_bandwidth_result: Optional[BenchResult] = None
    setup_time: float = 0.0
    errors: list[str] = field(default_factory=list)


# ── Utility functions ────────────────────────────────────────────────────────

def which(name: str) -> Optional[str]:
    """Find an executable on PATH or in ~/.local/bin."""
    result = shutil.which(name)
    if result:
        return result
    local = LOCAL_BIN / name
    if local.exists() and os.access(local, os.X_OK):
        return str(local)
    return None


def run(cmd: list[str] | str, timeout: int = 300, check: bool = True,
        capture: bool = True, env: dict | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a command, merging env with current env."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    if isinstance(cmd, str):
        cmd = ["sh", "-c", cmd]
    return subprocess.run(
        cmd, timeout=timeout, check=check, capture_output=capture,
        text=True, env=full_env, cwd=cwd,
    )


def check_user_namespaces() -> bool:
    """Check if user namespaces are available."""
    try:
        result = run(["unshare", "--user", "--pid", "--fork", "echo", "ok"], check=False)
        return result.returncode == 0
    except Exception:
        return False


def check_kvm() -> bool:
    """Check if /dev/kvm is accessible."""
    return os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)


def check_tun_tap() -> bool:
    """Check if /dev/net/tun is accessible."""
    return os.path.exists("/dev/net/tun") and os.access("/dev/net/tun", os.R_OK | os.W_OK)


def build_from_source(name: str, repo_url: str, build_cmds: list[str],
                      check_binary: str, branch: str = "main") -> bool:
    """Clone and build a project from source into ~/.local/."""
    import multiprocessing
    src = SRC_DIR / name
    if which(check_binary):
        return True

    nproc = multiprocessing.cpu_count()
    build_env = {
        "PREFIX": str(LOCAL_DIR),
        "prefix": str(LOCAL_DIR),
        "NPROC": str(nproc),
        "PKG_CONFIG_PATH": ":".join(filter(None, [
            str(LOCAL_LIB / "pkgconfig"),
            str(LOCAL_DIR / "share" / "pkgconfig"),
            os.environ.get("PKG_CONFIG_PATH", ""),
        ])),
        "CFLAGS": f"-I{LOCAL_DIR}/include",
        "LDFLAGS": f"-L{LOCAL_LIB}",
    }

    try:
        if not src.exists():
            r = subprocess.run(
                ["git", "clone", "--depth", "1", "-b", branch, repo_url, str(src)],
                timeout=120, capture_output=True, text=True,
                env={**os.environ, **build_env},
            )
            if r.returncode != 0:
                print(f"\n[BUILD FAIL] {name} clone:\n{r.stderr}", flush=True)
                return False

        for cmd in build_cmds:
            # Replace $(nproc) with actual count for shell commands
            if isinstance(cmd, str):
                cmd = cmd.replace("$(nproc)", str(nproc))
            full_env = {**os.environ, **build_env}
            r = subprocess.run(
                cmd if isinstance(cmd, list) else ["sh", "-c", cmd],
                cwd=str(src), timeout=600, capture_output=True, text=True,
                env=full_env,
            )
            if r.returncode != 0:
                print(f"\n[BUILD FAIL] {name}:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}", flush=True)
                return False

        if which(check_binary):
            return True
        else:
            print(f"\n[BUILD FAIL] {name}: binary not found after build: {check_binary}", flush=True)
            return False
    except subprocess.TimeoutExpired:
        print(f"\n[BUILD FAIL] {name}: timed out", flush=True)
        return False


def format_bytes(n: float) -> str:
    """Format bytes to human readable."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def format_duration(seconds: float) -> str:
    """Format seconds to human readable duration."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(seconds, 60)
    return f"{int(m)}m{int(s)}s"
