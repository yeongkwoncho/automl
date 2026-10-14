import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

D0 = dt.date(2026, 10, 1)  # Thursday


def trading_days(start, n):
  days, d = [], start
  while len(days) < n:
    if d.weekday() < 5:
      days.append(d)
    d += dt.timedelta(days=1)
  return days


def write_prices(directory, symbol, closes, start=D0):
  os.makedirs(directory, exist_ok=True)
  with open(os.path.join(directory, f'{symbol}.csv'), 'w') as f:
    f.write('date,open,high,low,close,volume\n')
    for day, c in zip(trading_days(start, len(closes)), closes):
      f.write(f'{day},{c},{c * 1.01},{c * 0.99},{c},1000\n')
