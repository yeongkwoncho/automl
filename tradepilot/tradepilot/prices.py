"""Daily price providers."""
import abc
import csv
import datetime as dt
import os
from typing import List

from tradepilot.models import Bar


class PriceSource(abc.ABC):

  @abc.abstractmethod
  def bars(self, symbol: str, start: dt.date, end: dt.date) -> List[Bar]:
    """Daily bars with start <= date <= end, oldest first."""


class CSVPrices(PriceSource):
  """Reads <dir>/<symbol>.csv with date,open,high,low,close[,volume].

  Export from your broker/HTS, or append one row per day. Column names are
  case-insensitive; Korean headers (일자, 시가, 고가, 저가, 종가, 거래량)
  are accepted too.
  """
  _ALIASES = {
      '일자': 'date',
      '날짜': 'date',
      '시가': 'open',
      '고가': 'high',
      '저가': 'low',
      '종가': 'close',
      '거래량': 'volume'
  }

  def __init__(self, directory: str):
    self.directory = directory

  def bars(self, symbol, start, end):
    path = os.path.join(self.directory, f'{symbol}.csv')
    if not os.path.exists(path):
      return []
    out = []
    with open(path, newline='', encoding='utf-8-sig') as f:
      for row in csv.DictReader(f):
        r = {
            self._ALIASES.get(k.strip(),
                              k.strip().lower()): v for k, v in row.items() if k
        }
        day = dt.date.fromisoformat(r['date'].strip().replace('/', '-')[:10])
        if start <= day <= end:
          num = lambda k: float(str(r.get(k) or 0).replace(',', ''))
          out.append(
              Bar(date=day,
                  open=num('open'),
                  high=num('high'),
                  low=num('low'),
                  close=num('close'),
                  volume=num('volume')))
    return sorted(out, key=lambda b: b.date)


class YFinancePrices(PriceSource):
  """Yahoo Finance via the optional `yfinance` package.

  Korean tickers use the exchange suffix: 005930.KS (KOSPI), 035720.KQ ...
  """

  def bars(self, symbol, start, end):
    import yfinance  # pylint: disable=g-import-not-at-top
    df = yfinance.Ticker(symbol).history(start=start.isoformat(),
                                         end=(end +
                                              dt.timedelta(days=1)).isoformat(),
                                         auto_adjust=False)
    return [
        Bar(date=idx.date(),
            open=float(r['Open']),
            high=float(r['High']),
            low=float(r['Low']),
            close=float(r['Close']),
            volume=float(r.get('Volume', 0))) for idx, r in df.iterrows()
    ]


def build_prices(cfg: dict, base_dir: str) -> PriceSource:
  kind = cfg.get('provider', 'csv')
  if kind == 'csv':
    d = cfg.get('dir', 'prices')
    return CSVPrices(d if os.path.isabs(d) else os.path.join(base_dir, d))
  if kind == 'yfinance':
    return YFinancePrices()
  raise ValueError(f'unknown price provider: {kind}')
