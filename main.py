#!/usr/bin/env python3
"""VMB - Virtual Machine / Container Benchmark Suite.

Benchmark rootless userspace isolation technologies on x86_64 Linux.
Tests CPU, memory, disk I/O, and network performance across multiple
isolation platforms and network backends.

Usage:
    uv run python main.py                    # Full benchmark run
    uv run python main.py --scan-only        # Just show capability matrix
    uv run python main.py --help             # Show all options
"""
from vmb.runner import main

if __name__ == "__main__":
    main()
