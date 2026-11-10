"""
Estrategia de tendencia (long/short) con filtro EMA200 en 1h, cruce EMA9/21 en 15m y RSI.
SOLO para dry-run / backtest. Los parametros (PARAMS) son los que ajustaremos con hyperopt
y validaremos fuera de muestra para evitar overfitting.
"""
import pandas as pd
from freqtrade.strategy import IStrategy, informative


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, 1e-12))


class TrendEmaAtr(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "15m"
    can_short = True
    startup_candle_count = 250

    LEVERAGE = 3.0  # techo bajo; el riesgo real lo marca el stoploss
    # stoploss se mide sobre el margen: -0.06 con 3x ~ 2% de movimiento de precio
    stoploss = -0.06
    minimal_roi = {"0": 0.12, "120": 0.06, "480": 0.0}
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.05
    trailing_only_offset_is_reached = True

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs) -> float:
        return min(self.LEVERAGE, max_leverage)

    @informative("1h")
    def populate_indicators_1h(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        df["ema200"] = ema(df["close"], 200)
        return df

    def populate_indicators(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        df["ema9"] = ema(df["close"], 9)
        df["ema21"] = ema(df["close"], 21)
        df["rsi"] = rsi(df["close"])
        return df

    def populate_entry_trend(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        cross_up = (df["ema9"] > df["ema21"]) & (df["ema9"].shift(1) <= df["ema21"].shift(1))
        cross_dn = (df["ema9"] < df["ema21"]) & (df["ema9"].shift(1) >= df["ema21"].shift(1))
        df.loc[cross_up & (df["close"] > df["ema200_1h"]) & df["rsi"].between(50, 70) & (df["volume"] > 0),
               ["enter_long", "enter_tag"]] = (1, "ema_cross_up")
        df.loc[cross_dn & (df["close"] < df["ema200_1h"]) & df["rsi"].between(30, 50) & (df["volume"] > 0),
               ["enter_short", "enter_tag"]] = (1, "ema_cross_dn")
        return df

    # v2: se quita la salida por cruce inverso de EMAs (en el backtest v1 cerraba en perdida
    # el 98% de las veces). Salen solo: ROI, trailing stop y stop-loss.
    use_exit_signal = False

    def populate_exit_trend(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        return df
