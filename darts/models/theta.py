"""
Theta Method
------------
"""

import math
from typing import Optional, List

import numpy as np
import statsmodels.tsa.holtwinters as hw

from ..utils.statistics import check_seasonality, extract_trend_and_seasonality, remove_seasonality
from .forecasting_model import UnivariateForecastingModel
from ..logging import raise_log, get_logger, raise_if_not
from ..timeseries import TimeSeries
from .. import Season, Trend, Model

logger = get_logger(__name__)


class Theta(UnivariateForecastingModel):
    # .. todo: Implement OTM: Optimized Theta Method (https://arxiv.org/pdf/1503.03529.pdf)
    # .. todo: From the OTM, set theta_2 = 2-theta_1 to recover our generalization - but we have an explicit formula.
    # .. todo: Try with something different than SES? They do that in the paper.
    def __init__(self,
                 theta: int = 0,
                 seasonality_period: Optional[int] = None,
                 season_mode: Season = Season.MULTIPLICATIVE):
        """
        An implementation of the Theta method with configurable `theta` parameter.

        See `Unmasking the Theta method <https://robjhyndman.com/papers/Theta.pdf>`_ paper.

        The training time series is de-seasonalized according to `seasonality_period`,
        or an inferred seasonality period.

        Parameters
        ----------
        theta
            Value of the theta parameter. Defaults to 0. Cannot be set to 2.0.
            If theta = 1, then the theta method restricts to a simple exponential smoothing (SES)
        seasonality_period
            User-defined seasonality period. If not set, will be tentatively inferred from the training series upon
            calling `fit()`.
        season_mode
            Type of seasonality. Must be a Season Enum member. Defaults to `Season.MULTIPLICATIVE`.
        """

        super().__init__()

        self.model = None
        self.coef = 1
        self.alpha = 1
        self.length = 0
        self.theta = theta
        self.is_seasonal = False
        self.seasonality = None
        self.season_period = seasonality_period
        self.season_mode = season_mode

        raise_if_not(season_mode in Season,
                     "Unknown value for season_mode: {}.".format(season_mode), logger)

        if self.theta == 2:
            raise_log(ValueError('The parameter theta cannot be equal to 2.'), logger)

    def fit(self, series: TimeSeries, component_index: Optional[int] = None):
        super().fit(series, component_index)
        ts = self.training_series

        self.length = len(ts)

        # Check for statistical significance of user-defined season period
        # or infers season_period from the TimeSeries itself.
        if self.season_mode is Season.NONE:
            self.season_period = 1
        if self.season_period is None:
            max_lag = len(ts) // 2
            self.is_seasonal, self.season_period = check_seasonality(ts, self.season_period, max_lag=max_lag)
            logger.info('Theta model inferred seasonality of training series: {}'.format(self.season_period))
        else:
            # force the user-defined seasonality to be considered as a true seasonal period.
            self.is_seasonal = self.season_period > 1

        new_ts = ts

        # Store and remove seasonality effect if there is any.
        if self.is_seasonal:
            _, self.seasonality = extract_trend_and_seasonality(ts, self.season_period, model=self.season_mode.value)
            new_ts = remove_seasonality(ts, self.season_period, model=self.season_mode.value)

        # SES part of the decomposition.
        self.model = hw.SimpleExpSmoothing(new_ts.values()).fit()

        # Linear Regression part of the decomposition. We select the degree one coefficient.
        b_theta = np.polyfit(np.array([i for i in range(0, self.length)]), (1.0 - self.theta) * new_ts.values(), 1)[0]

        # Normalization of the coefficient b_theta.
        self.coef = b_theta / (2.0 - self.theta)  # change to b_theta / (-self.theta) if classical theta

        self.alpha = self.model.params["smoothing_level"]
        if self.alpha == 0.:
            self.model = hw.SimpleExpSmoothing(new_ts.values()).fit(initial_level=0.2)
            self.alpha = self.model.params["smoothing_level"]

    def predict(self, n: int) -> 'TimeSeries':
        super().predict(n)

        # Forecast of the SES part.
        forecast = self.model.forecast(n)

        # Forecast of the Linear Regression part.
        drift = self.coef * np.array([i + (1 - (1 - self.alpha) ** self.length) / self.alpha for i in range(0, n)])

        # Combining the two forecasts
        forecast += drift

        # Re-apply the seasonal trend of the TimeSeries
        if self.is_seasonal:

            replicated_seasonality = np.tile(self.seasonality.pd_series()[-self.season_period:],
                                             math.ceil(n / self.season_period))[:n]
            if self.season_mode is Season.MULTIPLICATIVE:
                forecast *= replicated_seasonality
            elif self.season_mode is Season.ADDITIVE:
                forecast += replicated_seasonality

        return self._build_forecast_series(forecast)

    def __str__(self):
        return 'Theta({})'.format(self.theta)


class FourTheta(UnivariateForecastingModel):
    def __init__(self,
                 theta: int = 2,
                 seasonality_period: Optional[int] = None,
                 season_mode: Season = Season.MULTIPLICATIVE,
                 model_mode: Model = Model.ADDITIVE,
                 trend_mode: Trend = Trend.LINEAR,
                 normalization: bool = True):
        """
        An implementation of the 4Theta method with configurable `theta` parameter.

        See M4 competition `solution <https://github.com/Mcompetitions/M4-methods/blob/master/4Theta%20method.R>`_.

        The training time series is de-seasonalized according to `seasonality_period`,
        or an inferred seasonality period.

        When called with `theta = 2 - X`, `model_mode = Model.ADDITIVE` and `trend_mode = Trend.LINEAR`,
        this model is equivalent to calling `Theta(theta=X)`.

        Parameters
        ----------
        theta
            Value of the theta parameter. Defaults to 2.
            If theta = 1, then the fourtheta method restricts to a simple exponential smoothing (SES).
            If theta = 0, then the fourtheta method restricts to a simple `trend_mode` regression.
        seasonality_period
            User-defined seasonality period. If not set, will be tentatively inferred from the training series upon
            calling `fit()`.
        model_mode
            Type of model combining the Theta lines. Must be a Model Enum member. Defaults to `Model.ADDITIVE`.
        season_mode
            Type of seasonality. Must be a Season Enum member. Defaults to `Season.MULTIPLICATIVE`.
        trend_mode
            Type of trend to fit. Must be a Trend Enum member. Defaults to `Trend.LINEAR`.
        normalization
            If `True`, the data is normalized so that the mean is 1. Defaults to `True`.
        """

        super().__init__()

        self.model = None
        self.drift = None
        self.mean = 1
        self.length = 0
        self.theta = theta
        self.is_seasonal = False
        self.seasonality = None
        self.season_period = seasonality_period
        self.model_mode = model_mode
        self.season_mode = season_mode
        self.trend_mode = trend_mode
        self.wses = 0 if self.theta == 0 else (1 / theta)
        self.wdrift = 1 - self.wses
        self.fitted_values = None
        self.normalization = normalization

        raise_if_not(model_mode in Model,
                     "Unknown value for model_mode: {}.".format(model_mode), logger)
        raise_if_not(trend_mode in Trend,
                     "Unknown value for trend_mode: {}.".format(trend_mode), logger)
        raise_if_not(season_mode in Season,
                     "Unknown value for season_mode: {}.".format(season_mode), logger)

    def fit(self, ts, component_index: Optional[int] = None):
        super().fit(ts, component_index)

        self.length = len(ts)
        # normalization of data
        if self.normalization:
            self.mean = ts.mean().mean()
            raise_if_not(not np.isclose(self.mean, 0),
                         "The mean value of the TimeSeries must not be 0 when using normalization", logger)
            new_ts = ts / self.mean
        else:
            new_ts = ts.copy()

        # Check for statistical significance of user-defined season period
        # or infers season_period from the TimeSeries itself.
        if self.season_mode is Season.NONE:
            self.season_period = 1
        if self.season_period is None:
            max_lag = len(ts) // 2
            self.is_seasonal, self.season_period = check_seasonality(ts, self.season_period, max_lag=max_lag)
            logger.info('FourTheta model inferred seasonality of training series: {}'.format(self.season_period))
        else:
            # force the user-defined seasonality to be considered as a true seasonal period.
            self.is_seasonal = self.season_period > 1

        # Store and remove seasonality effect if there is any.
        if self.is_seasonal:
            _, self.seasonality = extract_trend_and_seasonality(new_ts, self.season_period,
                                                                model=self.season_mode.value)
            new_ts = remove_seasonality(new_ts, self.season_period, model=self.season_mode.value)

        ts_values = new_ts.univariate_values()
        if (ts_values <= 0).any():
            self.model_mode = Model.ADDITIVE
            self.trend_mode = Trend.LINEAR
            logger.warn("Time series has negative values. Fallback to additive and linear model")

        # Drift part of the decomposition
        if self.trend_mode is Trend.LINEAR:
            linreg = ts_values
        else:
            linreg = np.log(ts_values)
        self.drift = np.poly1d(np.polyfit(np.arange(self.length), linreg, 1))
        theta0_in = self.drift(np.arange(self.length))
        if self.trend_mode is Trend.EXPONENTIAL:
            theta0_in = np.exp(theta0_in)

        if (theta0_in > 0).all() and self.model_mode is Model.MULTIPLICATIVE:
            theta_t = (ts_values ** self.theta) * (theta0_in ** (1 - self.theta))
        else:
            if self.model_mode is Model.MULTIPLICATIVE:
                logger.warn("Negative Theta line. Fallback to additive model")
                self.model_mode = Model.ADDITIVE
            theta_t = self.theta * ts_values + (1 - self.theta) * theta0_in

        # SES part of the decomposition.
        self.model = hw.SimpleExpSmoothing(theta_t).fit()
        theta2_in = self.model.fittedvalues

        if (theta2_in > 0).all() and self.model_mode is Model.MULTIPLICATIVE:
            self.fitted_values = theta2_in**self.wses * theta0_in**self.wdrift
        else:
            if self.model_mode is Model.MULTIPLICATIVE:
                self.model_mode = Model.ADDITIVE
                logger.warn("Negative Theta line. Fallback to additive model")
                theta_t = self.theta * new_ts.values() + (1 - self.theta) * theta0_in
                self.model = hw.SimpleExpSmoothing(theta_t).fit()
                theta2_in = self.model.fittedvalues
            self.fitted_values = self.wses * theta2_in + self.wdrift * theta0_in
        if self.is_seasonal:
            if self.season_mode is Season.ADDITIVE:
                self.fitted_values += self.seasonality.univariate_values()
            elif self.season_mode is Season.MULTIPLICATIVE:
                self.fitted_values *= self.seasonality.univariate_values()
        # Fitted values are the results of the fit of the model on the train series. A good fit of the model
        # will lead to fitted_values similar to ts. But one cannot see if it overfits.
        if self.normalization:
            self.fitted_values *= self.mean
        # Takes too much time to create a TimeSeries
        # Overhead: 30% ± 10 (2-10 ms in average)
        self.fitted_values = TimeSeries.from_times_and_values(ts.time_index(), self.fitted_values)

    def predict(self, n: int) -> 'TimeSeries':
        super().predict(n)

        # Forecast of the SES part.
        forecast = self.model.forecast(n)

        # Forecast of the Linear Regression part.
        drift = self.drift(np.arange(self.length, self.length + n))
        if self.trend_mode is Trend.EXPONENTIAL:
            drift = np.exp(drift)

        if self.model_mode is Model.ADDITIVE:
            forecast = self.wses * forecast + self.wdrift * drift
        else:
            forecast = forecast**self.wses * drift**self.wdrift

        # Re-apply the seasonal trend of the TimeSeries
        if self.is_seasonal:

            replicated_seasonality = np.tile(self.seasonality.pd_series()[-self.season_period:],
                                             math.ceil(n / self.season_period))[:n]
            if self.season_mode is Season.MULTIPLICATIVE:
                forecast *= replicated_seasonality
            else:
                forecast += replicated_seasonality

        if self.normalization:
            forecast *= self.mean

        return self._build_forecast_series(forecast)

    @staticmethod
    def select_best_model(ts: TimeSeries, thetas: Optional[List[int]] = None,
                          m: Optional[int] = None, normalization: bool = True) -> 'FourTheta':
        """
        Performs a grid search over all hyper parameters to select the best model.

        Parameters
        ----------
        ts
            The TimeSeries on which the model will be tested.
        thetas
            A list of thetas to loop over. Defaults to [1, 2, 3].
        m
            Optionally, the season used to decompose the time series.
        normalization
            If `True`, the data is normalized so that the mean is 1. Defaults to `True`.
        Returns
        -------
        FourTheta
            The best performing model on the time series.
        """
        # Only import if needed
        from ..backtesting.backtesting import backtest_gridsearch
        from ..metrics import mae
        if thetas is None:
            thetas = [1, 2, 3]
        if (ts.values() <= 0).any():
            drift_mode = [Trend.LINEAR]
            model_mode = [Model.ADDITIVE]
            season_mode = [Season.ADDITIVE]
            logger.warn("The given TimeSeries has negative values. The method will only test "
                        "linear trend and additive modes.")
        else:
            season_mode = [season for season in Season]
            model_mode = [model for model in Model]
            drift_mode = [trend for trend in Trend]

        theta = backtest_gridsearch(FourTheta,
                                    {"theta": thetas,
                                     "model_mode": model_mode,
                                     "season_mode": season_mode,
                                     "trend_mode": drift_mode,
                                     "seasonality_period": [m],
                                     "normalization": normalization
                                     },
                                    ts, use_fitted_values=True, metric=mae)
        return theta

    def __str__(self):
        return '4Theta(theta:{}, curve:{}, model:{}, seasonality:{})'.format(self.theta, self.trend_mode,
                                                                             self.model_mode, self.season_mode)