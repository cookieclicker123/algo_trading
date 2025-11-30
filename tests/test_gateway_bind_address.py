#!/usr/bin/env python3
"""
Check what address Gateway is actually bound to.

Gateway might be bound to a specific interface, not accepting all connections.
"""
import subprocess
import sys

def check_gateway_bindings():
    """Check detailed port binding information."""
    print("=" * 80)
    print("Gateway Port Binding Analysis")
    print("=" * 80)
    print()
    
    # Get detailed lsof output
    print("1. Detailed lsof output:")
    print("-" * 80)
    try:
        result = subprocess.run(
            ["lsof", "-i", ":4001", "-n", "-P"],
            capture_output=True,
            text=True,
            timeout=5
        )
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
    except Exception as e:
        print(f"Error: {e}")
    
    print()
    print("2. netstat output:")
    print("-" * 80)
    try:
        result = subprocess.run(
            ["netstat", "-an"],
            capture_output=True,
            text=True,
            timeout=5
        )
        lines = [l for l in result.stdout.split('\n') if '4001' in l]
        for line in lines:
            print(line)
    except Exception as e:
        print(f"Error: {e}")
    
    print()
    print("3. Gateway process info:")
    print("-" * 80)
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5
        )
        lines = [l for l in result.stdout.split('\n') if 'gateway' in l.lower() or 'ib' in l.lower()]
        for line in lines[:5]:  # First 5 matches
            print(line)
    except Exception as e:
        print(f"Error: {e}")
    
    print()
    print("=" * 80)
    print("ANALYSIS:")
    print("=" * 80)
    print()
    print("If Gateway is bound to:")
    print("  - 0.0.0.0:4001 or *:4001 -> Should accept all connections")
    print("  - 127.0.0.1:4001 -> Only accepts localhost IPv4")
    print("  - ::1:4001 -> Only accepts localhost IPv6")
    print("  - Specific IP -> Only accepts from that interface")
    print()
    print("If port shows 'LISTEN' but connections fail:")
    print("  - Gateway might have API client access disabled")
    print("  - Gateway might require authentication before accepting")
    print("  - Gateway might be in read-only mode blocking new connections")
    print("  - Firewall might be blocking despite port being bound")

if __name__ == "__main__":
    check_gateway_bindings()

