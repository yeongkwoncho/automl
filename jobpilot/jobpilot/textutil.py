"""Small text helpers."""
import datetime as dt
import html
from html.parser import HTMLParser
import re
from typing import Optional


class _TextExtractor(HTMLParser):
  _BLOCK = {'p', 'div', 'br', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'tr'}

  def __init__(self):
    super().__init__()
    self.parts = []

  def handle_starttag(self, tag, attrs):
    if tag in self._BLOCK:
      self.parts.append('\n')
    if tag == 'li':
      self.parts.append('- ')

  def handle_data(self, data):
    self.parts.append(data)


def html_to_text(raw: str) -> str:
  """Converts (possibly entity-escaped) HTML to readable plain text."""
  if not raw:
    return ''
  parser = _TextExtractor()
  parser.feed(html.unescape(raw))
  text = ''.join(parser.parts)
  text = re.sub(r'[ \t\xa0]+', ' ', text)
  return re.sub(r'\n\s*\n+', '\n\n', text).strip()


def parse_datetime(value) -> Optional[dt.datetime]:
  """Parses ISO strings or epoch seconds/millis into aware datetimes."""
  if value in (None, ''):
    return None
  try:
    if isinstance(value, (int, float)):
      if value > 1e11:  # epoch millis
        value = value / 1000
      return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
    # Handles '...Z' and Recruitee's '2026-09-01 10:00:00 UTC'.
    value = re.sub(r'\s*(Z|UTC)$', '+00:00', str(value).strip())
    if re.fullmatch(r'\d+', value):
      return parse_datetime(int(value))
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
      parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed
  except (ValueError, OverflowError, OSError):
    return None


def slugify(text: str) -> str:
  return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:80]
