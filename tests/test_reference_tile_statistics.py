"""Whole-grid tile statistics must agree with a direct per-tile computation."""
import numpy as np

from vision.fretboard_reference import _correlation, _tile_statistics


def test_tile_statistics_match_direct_masked_reductions():
    rng = np.random.default_rng(7)
    height, width, rows, cols = 203, 97, 10, 4
    first = rng.normal(120, 30, (height, width)).astype(np.float32)
    second = (first * .6 + rng.normal(40, 12, (height, width))).astype(np.float32)
    valid = rng.random((height, width)) > .3
    valid[:25] = False  # Entirely hidden tiles.
    first[60:80, 24:48] = 77.  # A textureless tile has no defined correlation.
    row_starts = np.arange(rows) * height // rows
    col_starts = np.arange(cols) * width // cols

    count, first_std, second_std, correlation = _tile_statistics(first, second, valid, row_starts, col_starts)

    for row in range(rows):
        for col in range(cols):
            ys = slice(row * height // rows, (row + 1) * height // rows)
            xs = slice(col * width // cols, (col + 1) * width // cols)
            support = valid[ys, xs]
            assert count[row, col] == support.sum()
            if not support.any():
                assert correlation[row, col] == -1
                continue
            a, b = first[ys, xs][support], second[ys, xs][support]
            np.testing.assert_allclose(first_std[row, col], a.std(), atol=1e-3)
            np.testing.assert_allclose(second_std[row, col], b.std(), atol=1e-3)
            np.testing.assert_allclose(correlation[row, col], _correlation(a, b), atol=1e-4)
