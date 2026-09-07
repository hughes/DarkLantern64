"""Independent fixed-point rounding check for the staged Tiny3D RSP patch.

Run: python tools/tiny3d/reciprocal_reference.py
Hardware parity and pixel correctness remain the responsibility of depth_study.py.
"""
import json

Q = 65536


def refine_half_inverse(w: int, seed: int) -> int:
    """Inputs/output are signed16.16; intermediates model the RSP sequence."""
    residual = ((w * 128 * seed) >> 16) - 64 * Q
    correction = (((seed * residual) >> 16) + 32) >> 6
    return seed - correction


def verify() -> dict:
    count = 0
    maximum = 0.0
    maximum_rounding = 0.0
    for sign in (-1, 1):
        for step in range(2048):
            w = sign * round(0.001 * 2000 ** (step / 2047) * Q)
            for error in (-0.00195, -0.001, 0, 0.001, 0.00195):
                seed = round(Q * Q / (2 * w) * (1 + error))
                result = refine_half_inverse(w, seed)
                initial = seed * w / (Q * Q / 2) - 1
                actual = result * w / (Q * Q / 2) - 1
                # Exact Newton relative error is -initial^2. Each bound term
                # accounts for residual truncation, product truncation and
                # rounding the final correction, respectively.
                bound = (abs(1 + initial) / (64 * Q)
                         + abs(w) / (32 * Q * Q) + abs(w) / (Q * Q))
                rounding = abs(actual + initial * initial)
                if rounding > bound + 1e-12:
                    raise AssertionError((w, seed, result, rounding, bound))
                maximum = max(maximum, abs(actual))
                maximum_rounding = max(maximum_rounding, rounding)
                count += 1
    return {
        "passed": True,
        "scope": "Fixed-point rounding bound; not an RSP instruction emulator",
        "cases": count,
        "normalized_w_absolute_range": [0.001, 2],
        "both_signs": True,
        "seed_relative_error_targets": [-0.00195, -0.001, 0, 0.001, 0.00195],
        "maximum_result_relative_error": maximum,
        "maximum_difference_from_exact_newton": maximum_rounding,
    }


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
