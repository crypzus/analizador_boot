"""
Hipotesis unica: rupturas de canal Donchian en tendencia fuerte (ADX alto) y a favor de EMA200,
en 1h, con stop dinamico por ATR (sin ROI fijo: se deja correr la ganancia).

Protocolo (definido ANTES de mirar resultados):
  - Variantes: A = Donchian 20, B = Donchian 55. Nada mas.
  - Se ve cada variante en 2 tramos: 20250801-20260201 y 20260201-20261104.
  - Se acepta solo si gana en AMBOS tramos. Sin ajustes posteriores a ese resultado.
"""
import numpy as np
import pandas as pd
import talib.abstract as ta
from freqtrade.strategy import IStrategy, stoploss_from_absolute


class TrendBreakout(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    startup_candle_count = 250

    donchian = 20
    adx_min = 25
    atr_mult = 2.5
    LEVERAGE = 3.0

    stoploss = -0.99  # respaldo; el stop real es custom_stoploss
    use_custom_stoploss = True
    minimal_roi = {"0": 100}
    use_exit_signal = False

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs) -> float:
        return min(self.LEVERAGE, max_leverage)

    def populate_indicators(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        df["ema200"] = ta.EMA(df, timeperiod=200)
        df["adx"] = ta.ADX(df, timeperiod=14)
        df["atr"] = ta.ATR(df, timeperiod=14)
        df["hh"] = df["high"].rolling(self.donchian).max().shift(1)
        df["ll"] = df["low"].rolling(self.donchian).min().shift(1)
        return df

    def populate_entry_trend(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        strong = df["adx"] > self.adx_min
        df.loc[strong & (df["close"] > df["hh"]) & (df["close"] > df["ema200"]) & (df["volume"] > 0),
               ["enter_long", "enter_tag"]] = (1, "donchian_up")
        df.loc[strong & (df["close"] < df["ll"]) & (df["close"] < df["ema200"]) & (df["volume"] > 0),
               ["enter_short", "enter_tag"]] = (1, "donchian_dn")
        return df

    def populate_exit_trend(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        return df

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit,
                        after_fill, **kwargs):
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df.empty:
            return None
        atr = df.iloc[-1]["atr"]
        if not np.isfinite(atr):
            return None
        if trade.is_short:
            stop = current_rate + self.atr_mult * atr
        else:
            stop = current_rate - self.atr_mult * atr
        return stoploss_from_absolute(stop, current_rate, is_short=trade.is_short,
                                      leverage=trade.leverage)


class TrendBreakout55(TrendBreakout):
    donchian = 55
