"""Main benchmark runner with progress display and formatted results."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from .util import (
    CapCheck, CapStatus, NetBackend, PlatformNetResult, Tier,
    check_user_namespaces, check_kvm, check_tun_tap,
    console, DISK_DIR, format_bytes, format_duration,
)
from .platforms.registry import all_platforms, platforms_by_tier
from .benchmarks.workloads import run_native_benchmarks
from .networks.backends import check_network, ensure_network


# ── System capability overview ───────────────────────────────────────────────

def print_system_info():
    """Print system capability overview."""
    import platform
    import psutil

    table = Table(title="System Information", show_header=False, border_style="blue")
    table.add_column("Key", style="bold cyan", width=24)
    table.add_column("Value")

    table.add_row("OS", platform.platform())
    table.add_row("Kernel", platform.release())
    table.add_row("Arch", platform.machine())
    table.add_row("CPUs", str(psutil.cpu_count()))
    table.add_row("Memory", format_bytes(psutil.virtual_memory().total))
    table.add_row("User namespaces",
                  "[green]yes[/green]" if check_user_namespaces() else "[red]no[/red]")
    table.add_row("KVM (/dev/kvm)",
                  "[green]yes[/green]" if check_kvm() else "[dim]no (not needed)[/dim]")
    table.add_row("TUN/TAP (/dev/net/tun)",
                  "[green]yes[/green]" if check_tun_tap() else "[yellow]no[/yellow]")

    console.print()
    console.print(table)
    console.print()


# ── Capability scan ──────────────────────────────────────────────────────────

def run_capability_scan(platforms_list, networks: list[NetBackend]) -> dict:
    """Scan all platform+network combos for capability."""
    results = {}

    total = len(platforms_list) * len(networks)
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )

    with progress:
        task = progress.add_task("Scanning capabilities...", total=total)
        for plat in platforms_list:
            for net in networks:
                key = f"{plat.name}+{net.value}"
                cap = plat.check_capability(net)
                results[key] = {
                    "platform": plat.name,
                    "network": net.value,
                    "tier": plat.tier.value,
                    "status": cap.status.value,
                    "reason": cap.reason,
                    "binary": cap.binary_path,
                }
                progress.advance(task)

    return results


def print_capability_matrix(scan_results: dict, platforms_list, networks: list[NetBackend]):
    """Print a capability matrix table."""
    table = Table(title="Capability Matrix", border_style="blue", show_lines=False)
    table.add_column("Platform", style="bold", width=14, no_wrap=True)
    table.add_column("Tier", style="dim", width=6, no_wrap=True)
    for net in networks:
        table.add_column(net.value, justify="center", width=10, no_wrap=True)

    for plat in platforms_list:
        row = [plat.name, plat.tier.value.split("-")[0]]
        for net in networks:
            key = f"{plat.name}+{net.value}"
            info = scan_results.get(key, {})
            status = info.get("status", "unknown")
            match status:
                case "ready":
                    cell = "[green]READY[/green]"
                case "installable":
                    cell = "[yellow]BUILD[/yellow]"
                case "unavailable":
                    cell = "[red]NO[/red]"
                case _:
                    cell = "[dim]?[/dim]"
            row.append(cell)
        table.add_row(*row)

    console.print()
    console.print(table)
    console.print()


# ── Install phase ────────────────────────────────────────────────────────────

def install_platforms(platforms_list, scan_results: dict,
                      networks: list[NetBackend]) -> set[str]:
    """Install platforms that need building. Return set of installed platform names."""
    to_install = set()
    for plat in platforms_list:
        for net in networks:
            key = f"{plat.name}+{net.value}"
            if scan_results.get(key, {}).get("status") == "installable":
                to_install.add(plat.name)

    if not to_install:
        console.print("[dim]All available platforms already installed.[/dim]")
        return set()

    console.print(f"\n[bold]Building {len(to_install)} platforms from source...[/bold]\n")

    installed = set()
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold yellow]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )

    with progress:
        task = progress.add_task("Building...", total=len(to_install))
        for plat in platforms_list:
            if plat.name in to_install:
                progress.update(task, description=f"Building {plat.name}...")
                if plat.ensure_installed():
                    installed.add(plat.name)
                progress.advance(task)

    # Also install network backends
    for net in networks:
        net_check = check_network(net)
        if net_check.status == CapStatus.INSTALLABLE:
            console.print(f"  [yellow]Installing network backend: {net.value}...[/yellow]")
            ensure_network(net)

    return installed


# ── VM setup ─────────────────────────────────────────────────────────────────

def setup_vms(platforms_list, ubuntu_iso: Optional[Path],
              alpine_iso: Optional[Path]) -> dict[str, Path]:
    """Set up VM disk images for VM platforms. Returns {platform_name: disk_path}."""
    vm_disks = {}
    vm_platforms = [p for p in platforms_list if p.is_vm]

    if not vm_platforms:
        return vm_disks

    console.print("\n[bold]Setting up VM disk images...[/bold]\n")

    for plat in vm_platforms:
        # Use alpine ISO if available (lighter), otherwise ubuntu
        iso = alpine_iso or ubuntu_iso
        if iso and iso.exists():
            disk = plat.setup_vm(iso, f"{plat.name}_bench")
            if disk:
                vm_disks[plat.name] = disk
        else:
            console.print(f"  [dim]{plat.name}: No ISO provided, skipping VM setup[/dim]")

    return vm_disks


# ── Benchmark execution ─────────────────────────────────────────────────────

def run_all_benchmarks(platforms_list, networks: list[NetBackend],
                       scan_results: dict, vm_disks: dict,
                       ) -> list[PlatformNetResult]:
    """Run benchmarks for all viable platform+network combos."""
    # First, run native baseline
    console.print("\n[bold]Running native baseline benchmarks...[/bold]")
    baseline = run_native_benchmarks()
    console.print(f"  [green]Baseline: CPU={baseline['cpu'].value} {baseline['cpu'].unit}, "
                  f"Mem={baseline['mem'].value} {baseline['mem'].unit}, "
                  f"Disk={baseline['disk'].value} {baseline['disk'].unit}[/green]\n")

    # Determine which combos to run
    runnable = []
    for plat in platforms_list:
        for net in networks:
            key = f"{plat.name}+{net.value}"
            info = scan_results.get(key, {})
            if info.get("status") == "ready":
                runnable.append((plat, net))
            elif info.get("status") == "installable" and plat.ensure_installed():
                # Re-check after install
                cap = plat.check_capability(net)
                if cap.status == CapStatus.READY:
                    runnable.append((plat, net))

    if not runnable:
        console.print("[red]No platform+network combos are available to benchmark![/red]")
        return []

    console.print(f"[bold]Running benchmarks: {len(runnable)} platform+network combos[/bold]\n")

    results: list[PlatformNetResult] = []

    # Add baseline as a pseudo-result
    baseline_result = PlatformNetResult(
        platform="native (baseline)",
        network="host",
        tier=Tier.T1_NAMESPACE,
        cap_check=CapCheck(CapStatus.READY),
        cpu_result=baseline.get("cpu"),
        mem_result=baseline.get("mem"),
        disk_result=baseline.get("disk"),
        net_latency_result=baseline.get("net_latency"),
        net_bandwidth_result=baseline.get("net_bandwidth"),
    )
    results.append(baseline_result)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold green]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    )

    with progress:
        task = progress.add_task("Benchmarking...", total=len(runnable))

        for plat, net in runnable:
            desc = f"{plat.name} + {net.value}"
            progress.update(task, description=desc)

            disk = vm_disks.get(plat.name)
            t0 = time.monotonic()
            try:
                result = plat.run_benchmarks(network=net, disk_path=disk)
                result.setup_time = time.monotonic() - t0
                results.append(result)
            except Exception as e:
                results.append(PlatformNetResult(
                    platform=plat.name,
                    network=net.value,
                    tier=plat.tier,
                    cap_check=CapCheck(CapStatus.READY),
                    errors=[str(e)],
                    setup_time=time.monotonic() - t0,
                ))

            progress.advance(task)

    return results


# ── Results display ──────────────────────────────────────────────────────────

def print_results(results: list[PlatformNetResult]):
    """Print benchmark results as a formatted table."""
    if not results:
        console.print("[red]No results to display.[/red]")
        return

    # Main results table
    table = Table(
        title="Benchmark Results",
        border_style="green",
        show_lines=True,
        expand=True,
    )
    table.add_column("Platform", style="bold", min_width=16)
    table.add_column("Net", min_width=6)
    table.add_column("Tier", style="dim", min_width=5)
    table.add_column("CPU p/s", justify="right", min_width=7)
    table.add_column("Mem MB/s", justify="right", min_width=8)
    table.add_column("Disk MB/s", justify="right", min_width=9)
    table.add_column("Lat ms", justify="right", min_width=6)
    table.add_column("BW Kbps", justify="right", min_width=7)
    table.add_column("Ovrhd", justify="right", min_width=5)
    table.add_column("Errors", min_width=10)

    # Get baseline CPU for overhead calculation
    baseline_cpu = None
    for r in results:
        if r.platform == "native (baseline)" and r.cpu_result:
            baseline_cpu = r.cpu_result.value
            break

    for r in results:
        cpu_str = f"{r.cpu_result.value:.0f}" if r.cpu_result and r.cpu_result.value > 0 else "[dim]--[/dim]"
        mem_str = f"{r.mem_result.value:.0f}" if r.mem_result and r.mem_result.value > 0 else "[dim]--[/dim]"
        disk_str = f"{r.disk_result.value:.0f}" if r.disk_result and r.disk_result.value > 0 else "[dim]--[/dim]"

        if r.net_latency_result:
            if r.net_latency_result.value > 0:
                lat_str = f"{r.net_latency_result.value:.0f}"
            elif r.net_latency_result.unit == "failed":
                lat_str = "[red]fail[/red]"
            else:
                lat_str = "[dim]--[/dim]"
        else:
            lat_str = "[dim]--[/dim]"

        if r.net_bandwidth_result:
            if r.net_bandwidth_result.value > 0:
                bw_str = f"{r.net_bandwidth_result.value:.0f}"
            elif r.net_bandwidth_result.unit == "failed":
                bw_str = "[red]fail[/red]"
            else:
                bw_str = "[dim]--[/dim]"
        else:
            bw_str = "[dim]--[/dim]"

        # Calculate overhead vs baseline
        if baseline_cpu and r.cpu_result and r.cpu_result.value > 0 and r.platform != "native (baseline)":
            overhead = (baseline_cpu / r.cpu_result.value)
            if overhead >= 1.5:
                overhead_str = f"[red]{overhead:.1f}x[/red]"
            elif overhead >= 1.1:
                overhead_str = f"[yellow]{overhead:.1f}x[/yellow]"
            else:
                overhead_str = f"[green]{overhead:.1f}x[/green]"
        elif r.platform == "native (baseline)":
            overhead_str = "[bold green]1.0x[/bold green]"
        else:
            overhead_str = "[dim]--[/dim]"

        errors = ", ".join(r.errors[:2]) if r.errors else ""
        if len(errors) > 14:
            errors = errors[:11] + "..."

        tier_short = r.tier.value.split("-")[0] if r.tier else ""

        table.add_row(
            r.platform, r.network, tier_short,
            cpu_str, mem_str, disk_str, lat_str, bw_str,
            overhead_str, errors,
        )

    console.print()
    console.print(table)
    console.print()


def print_summary(results: list[PlatformNetResult], elapsed: float):
    """Print final summary."""
    total = len(results)
    success = sum(1 for r in results if not r.errors and r.cpu_result)
    failed = sum(1 for r in results if r.errors)
    skipped = total - success - failed

    panel_text = (
        f"[bold green]{success}[/bold green] benchmarked  "
        f"[bold yellow]{skipped}[/bold yellow] skipped  "
        f"[bold red]{failed}[/bold red] errors  "
        f"[dim]({format_duration(elapsed)} total)[/dim]"
    )
    console.print(Panel(panel_text, title="Summary", border_style="blue"))


def save_results(results: list[PlatformNetResult], output_path: Path):
    """Save results to JSON."""
    data = {
        "timestamp": datetime.now().isoformat(),
        "results": [],
    }
    for r in results:
        entry = {
            "platform": r.platform,
            "network": r.network,
            "tier": r.tier.value if r.tier else None,
            "errors": r.errors,
            "setup_time_s": round(r.setup_time, 2),
        }
        if r.cpu_result:
            entry["cpu"] = {"value": r.cpu_result.value, "unit": r.cpu_result.unit}
        if r.mem_result:
            entry["mem"] = {"value": r.mem_result.value, "unit": r.mem_result.unit}
        if r.disk_result:
            entry["disk"] = {"value": r.disk_result.value, "unit": r.disk_result.unit}
        if r.net_latency_result:
            entry["net_latency"] = {"value": r.net_latency_result.value, "unit": r.net_latency_result.unit}
        if r.net_bandwidth_result:
            entry["net_bandwidth"] = {"value": r.net_bandwidth_result.value, "unit": r.net_bandwidth_result.unit}
        data["results"].append(entry)

    output_path.write_text(json.dumps(data, indent=2))
    console.print(f"\n[dim]Results saved to {output_path}[/dim]")


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VMB - Virtual Machine / Container Benchmark Suite\n"
                    "Benchmark rootless userspace isolation on x86_64 Linux.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  uv run vmb                                  # Scan + benchmark all platforms
  uv run vmb --scan-only                      # Just show capability matrix
  uv run vmb --platforms bubblewrap,qemu-tcg   # Only specific platforms
  uv run vmb --tiers tier1,tier3              # Only specific tiers
  uv run vmb --networks slirp,passt           # Only specific net backends
  uv run vmb --skip-build                     # Only test pre-installed tools
  uv run vmb --alpine-iso ~/alpine.iso        # Provide ISO for VM setup
  uv run vmb -o results.json                  # Custom output path

Platforms (--platforms):
  Tier 1 (namespace):   bubblewrap, nsjail, apptainer, charliecloud, podman, firejail
  Tier 2 (VM):          qemu-tcg, uml, bochs
  Tier 3 (ptrace):      gvisor, proot, mbox, udocker
  Tier 4 (capability):  wasmtime, wasmer, wasmedge, wamr, deno
  Tier 5 (partial):     seccomp-bpf, fakechroot

Networks (--networks):  slirp, passt, tun/tap

Tiers (--tiers):        tier1, tier2, tier3, tier4, tier5

Benchmarks run:         CPU (prime sieve), memory (dd), disk I/O (write+read),
                        network latency (HTTP fetch), network bandwidth (download)
        """,
    )
    parser.add_argument("--scan-only", action="store_true",
                        help="Only scan capabilities, don't run benchmarks")
    parser.add_argument("--skip-build", action="store_true",
                        help="Skip building from source, only use what's installed")
    parser.add_argument("--platforms", type=str, default="",
                        help="Comma-separated list of platforms to test (default: all)")
    parser.add_argument("--networks", type=str, default="slirp,passt,tun/tap",
                        help="Comma-separated network backends (default: slirp,passt,tun/tap)")
    parser.add_argument("--ubuntu-iso", type=Path, default=None,
                        help="Path to Ubuntu ISO for VM setup")
    parser.add_argument("--alpine-iso", type=Path, default=None,
                        help="Path to Alpine ISO for VM setup")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Save results to JSON file")
    parser.add_argument("--tiers", type=str, default="",
                        help="Comma-separated tiers to include (e.g., tier1,tier2,tier3)")
    return parser.parse_args()


def main():
    args = parse_args()
    start_time = time.monotonic()

    # ── Header ───────────────────────────────────────────────────────────
    console.print()
    console.print(Panel(
        "[bold]VMB[/bold] - Virtual Machine / Container Benchmark Suite\n"
        "[dim]Rootless isolation technology benchmarks for x86_64 Linux[/dim]",
        border_style="blue",
    ))

    # ── System info ──────────────────────────────────────────────────────
    print_system_info()

    # ── Parse network selection ──────────────────────────────────────────
    net_map = {"slirp": NetBackend.SLIRP, "passt": NetBackend.PASST, "tun/tap": NetBackend.TAP}
    networks = []
    for n in args.networks.split(","):
        n = n.strip().lower()
        if n in net_map:
            networks.append(net_map[n])
        else:
            console.print(f"[yellow]Unknown network backend: {n}[/yellow]")
    if not networks:
        networks = list(NetBackend)

    # ── Get platform list ────────────────────────────────────────────────
    platforms_list = all_platforms()

    if args.platforms:
        names = [n.strip() for n in args.platforms.split(",")]
        platforms_list = [p for p in platforms_list if p.name in names]
        if not platforms_list:
            console.print(f"[red]No matching platforms found for: {args.platforms}[/red]")
            sys.exit(1)

    if args.tiers:
        tier_names = [t.strip().lower() for t in args.tiers.split(",")]
        platforms_list = [
            p for p in platforms_list
            if any(t in p.tier.value for t in tier_names)
        ]

    console.print(f"[bold]Platforms:[/bold] {len(platforms_list)}  "
                  f"[bold]Networks:[/bold] {', '.join(n.value for n in networks)}  "
                  f"[bold]Combos:[/bold] {len(platforms_list) * len(networks)}\n")

    # ── Capability scan ──────────────────────────────────────────────────
    scan_results = run_capability_scan(platforms_list, networks)
    print_capability_matrix(scan_results, platforms_list, networks)

    if args.scan_only:
        console.print("[dim]Scan-only mode, exiting.[/dim]")
        return

    # ── Install phase ────────────────────────────────────────────────────
    if not args.skip_build:
        install_platforms(platforms_list, scan_results, networks)
        # Re-scan after installs
        scan_results = run_capability_scan(platforms_list, networks)
        console.print()
        console.print("[bold]Updated capability matrix after builds:[/bold]")
        print_capability_matrix(scan_results, platforms_list, networks)

    # ── VM setup ─────────────────────────────────────────────────────────
    vm_disks = setup_vms(platforms_list, args.ubuntu_iso, args.alpine_iso)

    # ── Run benchmarks ───────────────────────────────────────────────────
    results = run_all_benchmarks(platforms_list, networks, scan_results, vm_disks)

    # ── Display results ──────────────────────────────────────────────────
    print_results(results)

    elapsed = time.monotonic() - start_time
    print_summary(results, elapsed)

    # ── Save results ─────────────────────────────────────────────────────
    output = args.output or Path("vmb_results.json")
    save_results(results, output)


if __name__ == "__main__":
    main()
