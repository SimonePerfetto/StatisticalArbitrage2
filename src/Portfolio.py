from datetime import date
import numpy as np
import pandas as pd
from typing import Tuple
from src.Cointegrator import CointPair
from enum import Enum, unique


@unique
class Holdingtype(Enum):
    Long = "Long"
    Short = "Short"


class Holding:
    def __init__(self, ticker: str, price: float, units: int, holding_type: Holdingtype, transaction_fee: float):
        self.ticker: str = ticker
        self.price: float = price
        self.units: int = units
        self.holding_type: Holdingtype = holding_type
        self.transaction_fee: float = transaction_fee
        self.prev_holding_pnl: float = 0
        self.current_holding_pnl: float = 0
        self.adjust_pnl_for_entry_transaction_costs()

    def update_holding_price_and_pnl(self, new_price: float, is_hold_being_liquidated: bool = False) -> None:
        self.prev_holding_pnl = self.current_holding_pnl
        if self.holding_type.value == "Long":
            self.current_holding_pnl += self.units * (new_price - self.price)
        else:
            self.current_holding_pnl -= self.units * (new_price - self.price)
        if is_hold_being_liquidated:
            self.current_holding_pnl -= self.units * new_price * self.transaction_fee
        self.price = new_price

    def adjust_pnl_for_entry_transaction_costs(self):
        self.current_holding_pnl = - self.transaction_fee * (self.price * self.units)
        self.prev_holding_pnl = self.current_holding_pnl

    def __repr__(self):
        return f"{self.holding_type.value}({self.ticker})"


class PairHolding:
    def __init__(self, holding_long: Holding, holding_short: Holding):
        self.holding_long: Holding = holding_long
        self.holding_short: Holding = holding_short
        self.pair_prev_holding_pnl: float = 0
        self.pair_current_holding_pnl: float = 0

    def compute_current_pair_holding_pnl(self):
        self.pair_prev_holding_pnl = self.pair_current_holding_pnl
        self.pair_current_holding_pnl += (self.holding_long.current_holding_pnl - self.holding_long.prev_holding_pnl) + \
                                         (self.holding_short.current_holding_pnl - self.holding_short.prev_holding_pnl)

    def __repr__(self):
        return f"PairHolding[{self.holding_long}, {self.holding_short}]"


class Portfolio:

    def __init__(self, max_active_pairs: int, cash: float = 1_000_000.0):
        self.max_active_pairs: int = max_active_pairs
        self.cur_cash: float = cash
        self.margin_locked_cash: float = 0
        self.outstanding_pnl_dict = {}
        self.realised_pnl: float = 0
        self.current_pnl: float = 0
        self.previous_pnl: float = 0
        self.total_pnl: float = 0
        self.current_holdings: dict = {}
        self.t_fee: float = 0.0005
        self.n_good_trades: int = 0
        self.n_bad_trades: int = 0

    def __repr__(self):
        #CurrCash: {self.cur_cash}, LockedCash: {self.margin_locked_cash},
        return f"Portfolio(GoodTrades: {self.n_good_trades}, BadTrades: {self.n_bad_trades}," \
               f" UnrealisedPnL: {round(self.current_pnl, 2)}, RealisedPnL: {round(self.realised_pnl,2)}, " \
               f"TotPnL: {round(self.total_pnl,2)})"

    def rebalance(self, coint_pairs, today):
        for coint_pair in coint_pairs:
            trade_action = self.evaluate_trade_action(coint_pair)
            self.make_trade_action(coint_pair, today, trade_action)
        self.total_pnl = self.current_pnl + self.realised_pnl

    @staticmethod
    def evaluate_trade_action(coint_pair):
        prev_sign, curr_sign = coint_pair.previous_pair_signal, coint_pair.current_pair_signal
        if curr_sign == 1 and prev_sign == 0:
            return "OpenLong"
        elif curr_sign == -1 and prev_sign == 0:
            return "OpenShort"
        elif curr_sign == 0 and prev_sign == -1:
            return "CloseShort"
        elif curr_sign == 0 and prev_sign == 1:
            return "CloseLong"
        elif curr_sign == 0 and prev_sign == 0:
            return "Pass"
        elif curr_sign == 1 and prev_sign == 1:
            return "HoldLong"
        elif curr_sign == -1 and prev_sign == -1:
            return "HoldShort"
        else:
            raise ValueError(f"Unexpected combination of previous signal: {prev_sign}, current signal: {curr_sign}")

    def make_trade_action(self, coint_pair: CointPair, today: date, trade_action: str) -> None:
        if trade_action in ("OpenLong", "OpenShort"):
            if len(self.current_holdings) > self.max_active_pairs:
                coint_pair.current_pair_signal = 0
                return
            pair_holding = self.__get__new_pair_holding(coint_pair, today, trade_action)
            self.current_holdings[coint_pair] = pair_holding
            #coint_pair.plot_residuals_and_bb_bands()

        elif trade_action in ("CloseLong", "CloseShort"):
            self.__update_pair_holding_pnl(coint_pair, today, trade_action)
            self.__add_holding_pnl_to_portfolio_pnl(coint_pair)
            self.__consolidate_pair_holding_pnl_for_closed_position(coint_pair)
            del self.current_holdings[coint_pair]
            #coint_pair.plot_residuals_and_bb_bands()

        elif trade_action in ("HoldLong", "HoldShort"):
            self.__update_pair_holding_pnl(coint_pair, today, trade_action)
            self.__add_holding_pnl_to_portfolio_pnl(coint_pair)

    def __add_holding_pnl_to_portfolio_pnl(self, coint_pair: CointPair) -> None:
        self.current_pnl += (self.current_holdings[coint_pair].pair_current_holding_pnl -
                             self.current_holdings[coint_pair].pair_prev_holding_pnl)

    def __consolidate_pair_holding_pnl_for_closed_position(self, coint_pair: CointPair) -> None:
        closing_pair_pnl = self.current_holdings[coint_pair].pair_current_holding_pnl
        self.realised_pnl += closing_pair_pnl
        self.current_pnl -= closing_pair_pnl
        if closing_pair_pnl > 0: self.n_good_trades += 1
        else: self.n_bad_trades += 1

    def __get__new_pair_holding(self, coint_pair: CointPair, today: date, trade_action: str) -> PairHolding:
        hedge_ratio = coint_pair.hedge_ratio
        px, py = self.__get_todays_price_x_y(coint_pair, today)
        nx, ny = self.__units_finder(py, hedge_ratio)
        ticker_x, ticker_y = self.__get_ticker_x_y(coint_pair)
        if trade_action == "OpenLong":  # long pair means buy 1 unit of y, sell hedgeratio units of x
            holding_long = Holding(ticker_y, py, ny, Holdingtype.Long, self.t_fee)
            holding_short = Holding(ticker_x, px, nx, Holdingtype.Short, self.t_fee)
        else:  # short pair means sell 1 unit of y, buy hedgeratio units of x
            holding_long = Holding(ticker_x, px, nx, Holdingtype.Long, self.t_fee)
            holding_short = Holding(ticker_y, py, ny, Holdingtype.Short, self.t_fee)
        return PairHolding(holding_long=holding_long, holding_short=holding_short)

    @staticmethod
    def __get_todays_price_x_y(coint_pair: CointPair, today: date) -> Tuple:
        px = coint_pair.stock_x.window_prices.loc[pd.to_datetime(today)]
        py = coint_pair.stock_y.window_prices.loc[pd.to_datetime(today)]
        return px, py

    @staticmethod
    def __get_ticker_x_y(coint_pair: CointPair) -> Tuple:
        return coint_pair.stock_x.ticker, coint_pair.stock_y.ticker

    @staticmethod
    def __units_finder(py, hedge_ratio, min_notional=48_000, max_notional=52_000) -> Tuple:
        notionals = np.linspace(min_notional, max_notional, 101)
        errors = []
        for notional in notionals:
            ny = round(notional / py)
            nx = round(ny * hedge_ratio)
            abs_err = abs(nx / ny - hedge_ratio)
            errors.append(abs_err)
        idx_min_error = np.argmin(errors)
        notional_w_min_err = notionals[idx_min_error]
        ny_star = round(notional_w_min_err / py)
        nx_star = round(ny_star * hedge_ratio)
        return nx_star, ny_star

    def __update_pair_holding_pnl(self, coint_pair: CointPair, today: date, trade_action: str) -> None:
        px, py = self.__get_todays_price_x_y(coint_pair, today)
        if trade_action == "HoldLong":  # long pair means buy 1 unit of y, sell hedgeratio units of x
            self.current_holdings[coint_pair].holding_long.update_holding_price_and_pnl(py)
            self.current_holdings[coint_pair].holding_short.update_holding_price_and_pnl(px)
        elif trade_action == "CloseLong":
            self.current_holdings[coint_pair].holding_long.update_holding_price_and_pnl(py, is_hold_being_liquidated=True)
            self.current_holdings[coint_pair].holding_short.update_holding_price_and_pnl(px, is_hold_being_liquidated=True)
        elif trade_action == "HoldShort":  # short pair means sell 1 unit of y, buy hedgeratio units of x
            self.current_holdings[coint_pair].holding_long.update_holding_price_and_pnl(px)
            self.current_holdings[coint_pair].holding_short.update_holding_price_and_pnl(py)
        elif trade_action == "CloseShort":
            self.current_holdings[coint_pair].holding_long.update_holding_price_and_pnl(px, is_hold_being_liquidated=True)
            self.current_holdings[coint_pair].holding_short.update_holding_price_and_pnl(py, is_hold_being_liquidated=True)
        else:
            raise ValueError(f"Check the logic for date:{today} and cointpair:{coint_pair} as this should not happen")

        self.current_holdings[coint_pair].compute_current_pair_holding_pnl()
