"""Shared statistics/calibration helpers for the forecast engines.

Pure standard library. All functions are deterministic and side-effect
free so engines and graders can use them identically."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------- isotonic


class IsotonicCalibrator:
    """Binned isotonic regression (PAVA) with piecewise-linear interpolation.

    Training probabilities are pooled into equal-width bins over their
    observed range; each bin's hit rate is pooled into a monotone sequence
    via PAVA. `predict` clamps outside the fitted range and linearly
    interpolates between bin centres. This is the standard approach for
    small graded samples (thousands of rows), far more stable than the
    per-row step fit."""

    def __init__(self) -> None:
        self.lo: float | None = None
        self.hi: float | None = None
        self.knots: list[float] = []  # sorted bin centres
        self.values: list[float] = []  # monotone rates aligned with knots

    @classmethod
    def fit(
        cls, pairs: list[tuple[float, int]], n_bins: int = 12, min_bin_count: int = 20,
        extreme_floor: float = 0.10, extreme_cap: float = 0.90,
    ) -> "IsotonicCalibrator":
        cal = cls()
        clean = [
            (float(p), int(y)) for p, y in pairs if p is not None and y is not None
        ]
        if not clean:
            return cal
        xs = [p for p, _ in clean]
        ys = [y for _, y in clean]
        lo, hi = min(xs), max(xs)
        if hi <= lo:
            cal.lo = cal.hi = lo
            rate = sum(ys) / len(ys)
            cal.knots = [lo, hi or lo + 1e-9]
            cal.values = [rate, rate]
            return cal
        cal.lo, cal.hi = lo, hi
        width = (hi - lo) / n_bins
        centres = []
        sums = []
        counts = []
        for b in range(n_bins):
            edges = (lo + b * width, lo + (b + 1) * width)
            sel = [y for p, y in clean if edges[0] <= p < edges[1]] or (
                [y for p, y in clean if p >= edges[0]] if b == n_bins - 1 else []
            )
            centres.append(lo + (b + 0.5) * width)
            sums.append(sum(sel))
            counts.append(len(sel))
        # merge sparse bins into current so no knot rests on tiny counts
        blocks = [[sums[b], counts[b]] for b in range(n_bins)]
        i = 0
        while i < len(blocks) - 1:
            if blocks[i][1] != 0 and blocks[i][1] >= min_bin_count and blocks[i + 1][1] != 0 and blocks[i + 1][1] >= min_bin_count:
                i += 1
                continue
            # merge block i with the next non-zero bin
            j = i + 1
            while j < len(blocks) and blocks[j][1] == 0:
                j += 1
            if j >= len(blocks):
                i += 1
                continue
            rate_i = blocks[i][0] / blocks[i][1] if blocks[i][1] else 0.0
            rate_j = blocks[j][0] / blocks[j][1]
            if rate_i > rate_j:
                merged_sum = blocks[i][0] + blocks[j][0]
                merged_count = blocks[i][1] + blocks[j][1]
                blocks[i : j + 1] = [[merged_sum, merged_count]]
                i = max(i - 1, 0)
            else:
                i += 1
        # PAVA pass enforcing monotonicity after merging
        i = 0
        while i < len(blocks) - 1:
            if blocks[i][1] == 0 or blocks[i + 1][1] == 0:
                i += 1
                continue
            rate_i = blocks[i][0] / blocks[i][1]
            rate_j = blocks[i + 1][0] / blocks[i + 1][1]
            if rate_i > rate_j:
                merged_sum = blocks[i][0] + blocks[i + 1][0]
                merged_count = blocks[i][1] + blocks[i + 1][1]
                blocks[i : i + 2] = [[merged_sum, merged_count]]
                i = max(i - 1, 0)
            else:
                i += 1
        used_knots: list[float] = []
        used_values: list[float] = []
        for b, (total, count) in enumerate(blocks):
            if count == 0:
                continue
            rate = total / count
            used_knots.append(centres[b])
            used_values.append(min(max(rate, extreme_floor), extreme_cap))
        for k in range(1, len(used_values)):
            if used_values[k] < used_values[k - 1]:
                used_values[k] = used_values[k - 1]
        cal.knots = used_knots
        cal.values = used_values
        return cal

    def predict(self, probability: float) -> float:
        if not self.knots:
            return probability
        clipped = min(max(probability, self.knots[0]), self.knots[-1])
        if clipped <= self.knots[0]:
            return self.values[0]
        if clipped >= self.knots[-1]:
            return self.values[-1]
        for k in range(len(self.knots) - 1):
            if self.knots[k] <= clipped <= self.knots[k + 1]:
                span = self.knots[k + 1] - self.knots[k]
                if span <= 0:
                    return self.values[k]
                ratio = (clipped - self.knots[k]) / span
                return self.values[k] * (1 - ratio) + self.values[k + 1] * ratio
        return self.values[-1]

    @property
    def size(self) -> int:
        return len(self.knots)

    def to_dict(self) -> dict:
        return {
            "lo": self.lo,
            "hi": self.hi,
            "knots": list(self.knots),
            "values": list(self.values),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "IsotonicCalibrator":
        cal = cls()
        cal.lo = payload.get("lo")
        cal.hi = payload.get("hi")
        cal.knots = list(payload.get("knots") or [])
        cal.values = list(payload.get("values") or [])
        return cal


# ----------------------------------------------------------------- powers


def shrink_logit(
    probability: float, factor: float, pivot: float | None = None
) -> float:
    """Monotone 'shrink toward the pivot' in logit space: with factor < 1
    the output stays closer to the pivot than the raw estimate, never
    overshooting the empirical range. factor=1 is a no-op."""
    p = min(max(probability, 1e-9), 1 - 1e-9)
    if pivot is None:
        anchor = 0.5
    else:
        anchor = min(max(pivot, 1e-9), 1 - 1e-9)
    logit = math.log(anchor / (1 - anchor)
                     ) if anchor not in (0.0, 1.0) else 0.0
    # standard: z' = factor * (z - z_anchor) + z_anchor
    z = math.log(probability / (1.0 - probability))
    scaled = factor * (z - logit) + logit
    return min(max(sigmoid(scaled), 0.0), 1.0)


def brier(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs: list[tuple[float, int]]) -> float | None:
    """Probability of the *observed* outcome must be clipped."""
    if not pairs:
        return None
    total = 0.0
    for p, y in pairs:
        p_obs = p if y == 1 else 1.0 - p
        p_obs = min(max(p_obs, 1e-9), 1.0 - 1e-9)
        total -= math.log(p_obs)
    return total / len(pairs)


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p_hat = wins / n
    denom = 1 + (z * z) / n
    centre = (p_hat + (z * z) / (2 * n)) / denom
    half = (z * math.sqrt((p_hat * (1 - p_hat)) / n + (z * z) / (4 * n * n))) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


# ------------------------------------------------- logistic regression


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


class LogisticModel:
    """Deterministic full-batch gradient-descent logistic regression.

    Deliberately simple: MLB graded samples top out around a few thousand
    rows with under a dozen features, so a few hundred full-batch epochs
    converge well and stay reproducible."""

    def __init__(
        self,
        learning_rate: float = 0.35,
        epochs: int = 600,
        l2: float = 5e-4,
    ) -> None:
        self.learning_rate = learning_rate
        self.epochs = epochs
        self.l2 = l2
        self.weights: list[float] = []
        self.bias = 0.0
        self.n_features = 0
        self._means: list[float] = []
        self._stds: list[float] = []

    def fit(
        self, rows: list[list[float]], targets: list[int]
    ) -> "LogisticModel":
        if not rows:
            raise ValueError("no rows to fit")
        n_features = len(rows[0])
        n = len(rows)
        means = [
            sum(row[c] for row in rows) / n for c in range(n_features)
        ]
        stds = []
        for c in range(n_features):
            variance = sum((row[c] - means[c]) ** 2 for row in rows) / n
            stds.append(math.sqrt(variance) or 1.0)
        scaled = [
            [(row[c] - means[c]) / stds[c] for c in range(n_features)]
            for row in rows
        ]

        self.weights = [0.0] * n_features
        self.bias = 0.0
        self.n_features = n_features
        self._means = means
        self._stds = stds

        def loss():
            total = 0.0
            for row, target in zip(scaled, targets):
                z = self.bias + sum(w * x for w, x in zip(self.weights, row))
                pred = sigmoid(z)
                pred = min(max(pred, 1e-9), 1 - 1e-9)
                obs = pred if target == 1 else 1 - pred
                total += -math.log(obs)
            return total / n

        lr = self.learning_rate
        best = loss()
        scale0 = self.weights  # just to avoid closure bug; real path below
        for _epoch in range(self.epochs):
            grad_w = [0.0] * n_features
            grad_b = 0.0
            for row, target in zip(scaled, targets):
                z = self.bias + sum(w * x for w, x in zip(self.weights, row))
                err = sigmoid(z) - target
                for c, x in enumerate(row):
                    grad_w[c] += err * x
                grad_b += err
            # clamp magnitudes for stability
            grad_w = [max(-2.0, min(2.0, g / n)) for g in grad_w]
            grad_b = max(-2.0, min(2.0, grad_b / n))

            previous_weights = list(self.weights)
            previous_bias = self.bias

            self.weights = [
                w - lr * (g + self.l2 * w) for w, g in zip(self.weights, grad_w)
            ]
            self.bias -= lr * grad_b
            current = loss()
            if current < best - 1e-9:
                best = current
                continue
            # no improvement: halve the rate and step back
            self.weights = previous_weights
            self.bias = previous_bias
            lr *= 0.5
            if lr < 1e-6:
                break
        return self

    def to_dict(self) -> dict:
        return {
            "weights": list(self.weights),
            "bias": self.bias,
            "means": list(self._means),
            "stds": list(self._stds),
            "learning_rate": self.learning_rate,
            "epochs": self.epochs,
            "l2": self.l2,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "LogisticModel":
        model = cls(
            learning_rate=payload.get("learning_rate", 0.35),
            epochs=payload.get("epochs", 600),
            l2=payload.get("l2", 5e-4),
        )
        model.weights = list(payload["weights"])
        model.bias = float(payload["bias"])
        model.n_features = len(model.weights)
        model._means = list(payload["means"])
        model._stds = list(payload["stds"])
        return model

    def predict_proba(self, row: list[float]) -> float:
        if not self.weights:
            raise RuntimeError("model not fitted")
        scaled = [
            (row[c] - self._means[c]) / self._stds[c]
            for c in range(min(len(row), self.n_features))
        ]
        z = self.bias + sum(
            w * x for w, x in zip(self.weights, scaled)
        )
        return sigmoid(z)


def standardiser(means: list[float], stds: list[float]):
    def apply(row: list[float]) -> list[float]:
        return [(row[c] - means[c]) / stds[c] for c in range(len(row))]

    return apply


# --------------------------------------------- negative binomial counts


def neg_binomial_pmf(k: int, mean: float, dispersion: float) -> float:
    """Overdispersed count pmf via the log-gamma identity.

    mean: mu > 0 ; dispersion: r (size) > 0 ; var = mu + mu^2 / r."""
    if mean <= 0 or dispersion <= 0:
        return 0.0
    p = dispersion / (dispersion + mean)
    log = (
        math.lgamma(k + dispersion)
        - math.lgamma(dispersion)
        - math.lgamma(k + 1)
        + dispersion * math.log(p)
        + k * math.log(1.0 - p)
    )
    return math.exp(log)


def count_probabilities(
    line: float, mean: float, dispersion: float, max_count: int = 80
) -> tuple[float, float, float]:
    """Return (p_over, p_push, p_under) for a count prop at `line` using an
    overdispersed distribution. Over wins at strictly greater than the
    line; integer lines can push exactly. 5.0 and 5.5 behave as k>=6."""
    mean = max(mean, 0.01)
    p_over = 0.0
    p_push = 0.0
    line_is_integer = float(line).is_integer()
    for k in range(max_count + 1):
        pmf = neg_binomial_pmf(k, mean, dispersion)
        if k > line:
            p_over += pmf
        elif line_is_integer and k == line:
            p_push += pmf
    p_under = max(0.0, 1.0 - p_over - p_push)
    return p_over, p_push, p_under


@dataclass
class CalibrationReport:
    sample: str
    n: int
    brier: float | None
    log_loss: float | None
    base_rate: float | None
    base_brier: float | None
    bands: list[dict] = field(default_factory=list)
