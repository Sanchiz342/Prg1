from collections import Counter

counters: Counter = Counter()
gauges: Counter = Counter()


def render() -> str:
    lines = []
    for name, value in sorted(counters.items()):
        lines += [f"# TYPE {name} counter", f"{name} {value}"]
    for name, value in sorted(gauges.items()):
        lines += [f"# TYPE {name} gauge", f"{name} {value}"]
    return "\n".join(lines) + "\n"
