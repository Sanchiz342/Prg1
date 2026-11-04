import math

from pipeline.export import _simplify


def test_simplify_keeps_closed_rings_and_drops_redundant_points():
    # A closed square with many collinear points on its edges.
    side = [(x, 0.0) for x in range(11)] + [(10.0, y) for y in range(1, 11)] + [(x, 10.0) for x in range(9, -1, -1)] + [(0.0, y) for y in range(9, 0, -1)]
    ring = side + [side[0]]
    out = _simplify(ring, 0.5)
    assert out[0] == out[-1], "ring stays closed"
    assert {(0, 0), (10, 0), (10, 10), (0, 10)} <= {(round(x), round(y)) for x, y in out}
    assert len(out) <= 6


def test_simplify_respects_tolerance():
    line = [(x / 10, math.sin(x / 10)) for x in range(0, 63)]
    loose, tight = _simplify(line, 0.5), _simplify(line, 0.01)
    assert len(loose) < len(tight) <= len(line)
    assert loose[0] == line[0] and loose[-1] == line[-1]
