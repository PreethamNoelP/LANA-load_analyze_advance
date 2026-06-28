"""Linear regression analysis."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_squared_error, r2_score


@dataclass
class RegressionResult:
    r2: float
    coefficient: float
    intercept: float
    mse: float
    rmse: float
    x_values: np.ndarray
    y_values: np.ndarray
    predictions: np.ndarray
    x_col: str
    y_col: str

    def as_dict(self) -> dict:
        return {
            "R² Score": round(self.r2, 4),
            "Coefficient": round(self.coefficient, 4),
            "Intercept": round(self.intercept, 4),
            "MSE": round(self.mse, 4),
            "RMSE": round(self.rmse, 4),
        }

    def interpretation(self) -> str:
        direction = "increases" if self.coefficient > 0 else "decreases"
        quality = (
            "strong" if self.r2 > 0.7
            else "moderate" if self.r2 > 0.4
            else "weak"
        )
        return (
            f"For each unit increase in '{self.x_col}', '{self.y_col}' {direction} "
            f"by {abs(self.coefficient):.4f}. "
            f"The model explains {self.r2 * 100:.1f}% of variance ({quality} fit)."
        )


def perform_linear_regression(
    df: pd.DataFrame, x_col: str, y_col: str
) -> RegressionResult:
    """Fit OLS linear regression between two numeric columns.

    Drops rows where either column is null before fitting.
    """
    mask = df[x_col].notna() & df[y_col].notna()
    X = df.loc[mask, x_col].values.reshape(-1, 1)
    Y = df.loc[mask, y_col].values

    if len(X) < 2:
        raise ValueError("Need at least 2 non-null rows to fit a regression model.")

    reg = LinearRegression()
    reg.fit(X, Y)
    preds = reg.predict(X)

    return RegressionResult(
        r2=r2_score(Y, preds),
        coefficient=float(reg.coef_[0]),
        intercept=float(reg.intercept_),
        mse=float(mean_squared_error(Y, preds)),
        rmse=float(np.sqrt(mean_squared_error(Y, preds))),
        x_values=X.flatten(),
        y_values=Y,
        predictions=preds,
        x_col=x_col,
        y_col=y_col,
    )