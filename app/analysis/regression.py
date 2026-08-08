"""Ordinary least squares regression with inference and diagnostics.

A fitted slope on its own is not a finding. Without a confidence interval it
cannot be distinguished from noise; without residual diagnostics the model's
assumptions may be violated in ways that make the interval meaningless; and
without explicit language about causation, "for each unit increase in X, Y
increases by b" reads as a causal claim that the fit does not support.

This module reports all of it, and refuses to fit where the fit would be
meaningless (identifier columns, constant columns, too few points).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_squared_error, r2_score

from ..data.profile import profile_column

# Below this many paired observations, the standard errors are so wide that
# the fit conveys essentially nothing.
MIN_OBSERVATIONS = 3

# Warn when residual spread is visibly related to the fitted value, which
# breaks the constant-variance assumption behind the confidence intervals.
_HETEROSCEDASTICITY_R = 0.3


@dataclass
class RegressionResult:
    """A fitted simple linear model, with the uncertainty around it."""

    x_col: str
    y_col: str
    n: int
    r2: float
    adjusted_r2: float
    coefficient: float
    intercept: float
    rmse: float

    # Inference on the slope.
    std_error: float
    t_statistic: float
    p_value: float
    ci95_low: float
    ci95_high: float

    diagnostics: dict[str, Any] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    @property
    def slope_is_significant(self) -> bool:
        """True when the 95% interval for the slope excludes zero."""
        return self.ci95_low > 0 or self.ci95_high < 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "x_col": self.x_col,
            "y_col": self.y_col,
            "n": self.n,
            "r2_score": round(self.r2, 4),
            "adjusted_r2": round(self.adjusted_r2, 4),
            "coefficient": round(self.coefficient, 4),
            "intercept": round(self.intercept, 4),
            "rmse": round(self.rmse, 4),
            "std_error": round(self.std_error, 4),
            "t_statistic": round(self.t_statistic, 4),
            "p_value": round(self.p_value, 6),
            "ci95_low": round(self.ci95_low, 4),
            "ci95_high": round(self.ci95_high, 4),
            "significant": self.slope_is_significant,
            "diagnostics": self.diagnostics,
            "caveats": self.caveats,
            "interpretation": self.interpretation(),
        }

    def interpretation(self) -> str:
        """Plain-English reading of the fit, stated associationally."""
        direction = "higher" if self.coefficient > 0 else "lower"
        quality = ("strong" if self.r2 > 0.7 else "moderate" if self.r2 > 0.4 else "weak")

        lines = [
            f"Across {self.n:,} paired observations, each one-unit increase in "
            f"'{self.x_col}' is associated with a {abs(self.coefficient):.4g} "
            f"{direction} '{self.y_col}' on average "
            f"(95% CI: {self.ci95_low:.4g} to {self.ci95_high:.4g}).",
            f"The model accounts for {self.r2 * 100:.1f}% of the variation in "
            f"'{self.y_col}' ({quality} fit); typical prediction error is "
            f"{self.rmse:.4g} in '{self.y_col}' units.",
        ]
        if self.slope_is_significant:
            lines.append(
                f"The slope is statistically distinguishable from zero "
                f"(p = {self.p_value:.4g})."
            )
        else:
            lines.append(
                f"The 95% interval includes zero (p = {self.p_value:.4g}), so this "
                f"data does not establish that '{self.x_col}' and '{self.y_col}' "
                f"are related at all."
            )
        lines.append(
            "This is an association measured in this dataset, not evidence that "
            f"'{self.x_col}' causes '{self.y_col}'. A third variable, reverse "
            "causation, or selection in how these rows were collected would "
            "produce the same fit."
        )
        return " ".join(lines)


def perform_linear_regression(df: pd.DataFrame, x_col: str, y_col: str) -> RegressionResult:
    """Fit OLS between two numeric columns and quantify the uncertainty.

    Rows with a null in either column are dropped before fitting (complete-case
    analysis), so ``n`` reflects the paired observations actually used.
    """
    for col in (x_col, y_col):
        if col not in df.columns:
            raise ValueError(f"Column '{col}' not found.")

    x_profile = profile_column(df[x_col])
    y_profile = profile_column(df[y_col])
    for profile, role in ((x_profile, "predictor"), (y_profile, "outcome")):
        if not pd.api.types.is_numeric_dtype(df[profile.name]):
            raise ValueError(
                f"Column '{profile.name}' is not numeric — it cannot be used as "
                f"the {role} in a linear regression."
            )
        if profile.unique <= 1:
            raise ValueError(
                f"Column '{profile.name}' has a single distinct value. A "
                f"regression against a constant is undefined."
            )

    mask = df[x_col].notna() & df[y_col].notna()
    x = df.loc[mask, x_col].to_numpy(dtype="float64")
    y = df.loc[mask, y_col].to_numpy(dtype="float64")
    n = len(x)

    if n < MIN_OBSERVATIONS:
        raise ValueError(
            f"Only {n} row(s) have a value in both '{x_col}' and '{y_col}'. "
            f"At least {MIN_OBSERVATIONS} are needed to fit a line and estimate "
            "its uncertainty."
        )
    if np.ptp(x) == 0:
        raise ValueError(
            f"Every non-null value of '{x_col}' in the paired rows is identical — "
            "the slope is undefined."
        )

    reg = LinearRegression().fit(x.reshape(-1, 1), y)
    predictions = reg.predict(x.reshape(-1, 1))
    residuals = y - predictions

    slope = float(reg.coef_[0])
    intercept = float(reg.intercept_)
    r2 = float(r2_score(y, predictions))
    rmse = float(np.sqrt(mean_squared_error(y, predictions)))

    # ── Inference on the slope ───────────────────────────────────────────────
    # dof = n - 2: one degree of freedom spent on the slope, one on the intercept.
    dof = n - 2
    if dof > 0:
        residual_variance = float(np.sum(residuals ** 2) / dof)
        sum_sq_x = float(np.sum((x - x.mean()) ** 2))
        std_error = float(np.sqrt(residual_variance / sum_sq_x)) if sum_sq_x > 0 else float("inf")
        if std_error > 0 and np.isfinite(std_error):
            t_stat = slope / std_error
            p_value = float(2 * scipy_stats.t.sf(abs(t_stat), df=dof))
            margin = float(scipy_stats.t.ppf(0.975, df=dof) * std_error)
        else:
            # A perfect fit leaves no residual variance; the slope is exact
            # within this sample, which is not the same as being certain.
            t_stat, p_value, margin = float("inf"), 0.0, 0.0
        adjusted_r2 = 1 - (1 - r2) * (n - 1) / dof
    else:
        std_error, t_stat, p_value, margin = float("nan"), float("nan"), float("nan"), float("nan")
        adjusted_r2 = float("nan")

    result = RegressionResult(
        x_col=x_col, y_col=y_col, n=n,
        r2=r2, adjusted_r2=adjusted_r2,
        coefficient=slope, intercept=intercept, rmse=rmse,
        std_error=std_error, t_statistic=t_stat, p_value=p_value,
        ci95_low=slope - margin, ci95_high=slope + margin,
    )
    result.diagnostics = _diagnose(x, y, predictions, residuals, n)
    result.caveats = _regression_caveats(result, x_profile, y_profile, df, mask)
    return result


def _diagnose(
    x: np.ndarray,
    y: np.ndarray,
    predictions: np.ndarray,
    residuals: np.ndarray,
    n: int,
) -> dict[str, Any]:
    """Residual checks on the assumptions the confidence interval relies on."""
    diagnostics: dict[str, Any] = {}

    # Heteroscedasticity: does the residual size track the fitted value? If so,
    # the standard errors above are wrong (usually too small).
    abs_resid = np.abs(residuals)
    if n >= 8 and np.ptp(predictions) > 0 and np.ptp(abs_resid) > 0:
        spread_r = float(np.corrcoef(predictions, abs_resid)[0, 1])
        diagnostics["heteroscedasticity_r"] = round(spread_r, 4)
        diagnostics["constant_variance"] = bool(abs(spread_r) < _HETEROSCEDASTICITY_R)

    # Residual normality underpins the t-based interval.
    if 8 <= n <= 5000:
        try:
            _, p_normal = scipy_stats.shapiro(residuals)
            diagnostics["residual_normality_p"] = round(float(p_normal), 6)
            diagnostics["residuals_normal"] = bool(p_normal > 0.05)
        except Exception:
            pass

    if n >= 8:
        diagnostics["residual_skew"] = round(float(scipy_stats.skew(residuals)), 4)

    # High-leverage points: far from mean(x), so they pull the line hardest.
    x_std = float(np.std(x))
    if x_std > 0:
        leverage = np.abs(x - x.mean()) / x_std
        diagnostics["high_leverage_points"] = int((leverage > 3).sum())

    return diagnostics


def _regression_caveats(
    result: RegressionResult,
    x_profile,
    y_profile,
    df: pd.DataFrame,
    mask: pd.Series,
) -> list[str]:
    caveats: list[str] = []

    dropped = int(len(df) - mask.sum())
    if dropped:
        pct = dropped / len(df) * 100
        caveats.append(
            f"{dropped:,} row(s) ({pct:.1f}%) were excluded because one of the two "
            f"columns was empty. If those rows differ systematically from the rest, "
            f"this fit describes a biased subset."
        )

    if result.n < 30:
        caveats.append(
            f"Only {result.n} paired observations. The confidence interval is wide "
            "and a single unusual point can move the slope substantially."
        )

    if result.diagnostics.get("constant_variance") is False:
        caveats.append(
            f"Residual spread grows with the fitted value "
            f"(r = {result.diagnostics['heteroscedasticity_r']}). The constant-variance "
            "assumption is violated, so the confidence interval and p-value above are "
            "optimistic — consider modelling the log of the outcome instead."
        )
    if result.diagnostics.get("residuals_normal") is False:
        caveats.append(
            "Residuals are significantly non-normal, so the p-value should be read "
            "as approximate. With a large sample the slope estimate is still usable."
        )
    if result.diagnostics.get("high_leverage_points"):
        caveats.append(
            f"{result.diagnostics['high_leverage_points']} point(s) sit more than 3 "
            f"standard deviations from the mean of '{result.x_col}' and therefore "
            "exert outsized influence on the slope. Refit without them to see how "
            "much the result depends on them — do not delete them on that basis alone."
        )
    if result.r2 > 0.99 and result.n > 5:
        caveats.append(
            f"An R^2 of {result.r2:.4f} is unusually high for real measurements. "
            f"Check whether '{result.y_col}' is derived from '{result.x_col}' by a "
            "formula, in which case the fit is arithmetic rather than a finding."
        )
    for profile in (x_profile, y_profile):
        if profile.shape == "highly skewed":
            caveats.append(
                f"'{profile.name}' is highly skewed (skewness {profile.skewness}). "
                "OLS fits the conditional mean, which a long tail dominates; a log "
                "transform usually gives a more faithful line."
            )

    return caveats
