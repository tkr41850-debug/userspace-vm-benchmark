"""Platform registry: all platforms, organized by tier."""
from __future__ import annotations

from .base import Platform
from .tier1_namespace import (
    BubblewrapPlatform,
    NsjailPlatform,
    ApptainerPlatform,
    CharliecloudPlatform,
    PodmanPlatform,
    FirejailPlatform,
)
from .tier2_vm import (
    QemuTcgPlatform,
    UmlPlatform,
    BochsPlatform,
)
from .tier3_ptrace import (
    GvisorPlatform,
    ProotPlatform,
    MboxPlatform,
    UdockerPlatform,
)
from .tier4_capability import (
    WasmtimePlatform,
    WasmerPlatform,
    WasmEdgePlatform,
    WamrPlatform,
    DenoPlatform,
)
from .tier5_partial import (
    SeccompPlatform,
    FakechrootPlatform,
)


def all_platforms() -> list[Platform]:
    """Return all platform instances, ordered by tier."""
    return [
        # Tier 1: Namespace-based (native performance)
        BubblewrapPlatform(),
        NsjailPlatform(),
        ApptainerPlatform(),
        CharliecloudPlatform(),
        PodmanPlatform(),
        FirejailPlatform(),
        # Tier 2: Full VM emulation
        QemuTcgPlatform(),
        UmlPlatform(),
        BochsPlatform(),
        # Tier 3: Ptrace/syscall interception
        GvisorPlatform(),
        ProotPlatform(),
        MboxPlatform(),
        UdockerPlatform(),
        # Tier 4: Capability-based / language runtimes
        WasmtimePlatform(),
        WasmerPlatform(),
        WasmEdgePlatform(),
        WamrPlatform(),
        DenoPlatform(),
        # Tier 5: Partial / component-level
        SeccompPlatform(),
        FakechrootPlatform(),
    ]


def get_platform(name: str) -> Platform | None:
    """Get a platform by name."""
    for p in all_platforms():
        if p.name == name:
            return p
    return None


def platforms_by_tier() -> dict[str, list[Platform]]:
    """Return platforms grouped by tier."""
    result: dict[str, list[Platform]] = {}
    for p in all_platforms():
        key = p.tier.value
        result.setdefault(key, []).append(p)
    return result
