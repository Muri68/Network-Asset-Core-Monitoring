"""
Port name patterns.

    expand("GigabitEthernet1/0/[1-48]")  → GigabitEthernet1/0/1 … GigabitEthernet1/0/48
    expand("Gi[1-2]/0/[1-24]")           → Gi1/0/1 … Gi2/0/24   (every combination)
    expand("port[1,3,5-7]")              → port1, port3, port5, port6, port7
    expand("mgmt")                       → mgmt
"""

import itertools
import re

MAX_PORTS = 1000
_RANGE = re.compile(r"\[([^\]]+)\]")


class PatternError(ValueError):
    pass


def _numbers(spec):
    values = []
    for part in spec.split(","):
        part = part.strip()
        if re.fullmatch(r"\d+", part):
            values.append(int(part))
        elif m := re.fullmatch(r"(\d+)\s*-\s*(\d+)", part):
            start, end = int(m[1]), int(m[2])
            if end < start:
                raise PatternError(f"[{spec}]: the range goes backwards.")
            if end - start >= MAX_PORTS:
                raise PatternError(f"[{spec}]: too many ports.")
            values.extend(range(start, end + 1))
        else:
            raise PatternError(f"[{spec}] isn't a number range like [1-48] or [1,3,5-8].")
    return values


def expand(pattern):
    pattern = pattern.strip()
    if not pattern:
        return []
    pieces = _RANGE.split(pattern)  # literal, range, literal, range, ..., literal
    literals, ranges = pieces[0::2], [_numbers(r) for r in pieces[1::2]]
    if any("[" in lit or "]" in lit for lit in literals):
        raise PatternError(f"“{pattern}” has an unclosed bracket.")
    total = 1
    for r in ranges:
        total *= len(r)
    if total > MAX_PORTS:
        raise PatternError(f"“{pattern}” would create {total} ports (limit {MAX_PORTS}).")
    names = []
    for combo in itertools.product(*ranges):
        name = literals[0]
        for number, literal in zip(combo, literals[1:], strict=True):
            name += f"{number}{literal}"
        names.append(name)
    return names


def expand_lines(text):
    """Several patterns, one per line; duplicates are dropped, order kept."""
    seen, names = set(), []
    for line in text.splitlines():
        for name in expand(line):
            if name.lower() not in seen:
                seen.add(name.lower())
                names.append(name)
    if len(names) > MAX_PORTS:
        raise PatternError(f"That's {len(names)} ports (limit {MAX_PORTS}).")
    return names


def natural_key(name):
    """'Gi1/0/10' sorts after 'Gi1/0/9': numbers are zero-padded."""
    return re.sub(r"\d+", lambda m: m.group().zfill(6), name.lower())[:200]
