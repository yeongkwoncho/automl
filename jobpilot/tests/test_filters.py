import datetime as dt

from conftest import make_job
from jobpilot.filters import prefilter


def test_passes(prefs):
  assert prefilter(make_job(), prefs).passed


def test_rejections(prefs):
  old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)
  cases = {
      'excluded company':
          make_job(company='badco'),
      'excluded keyword: intern':
          make_job(title='Machine Learning Engineer Intern'),
      'title does not match':
          make_job(title='Sales Manager'),
      'too old':
          make_job(posted_at=old),
      'location: Berlin':
          make_job(location='Berlin'),
  }
  for reason, job in cases.items():
    assert prefilter(job, prefs).reason == reason


def test_keyword_word_boundary(prefs):
  # "internal" must not trip the "intern" exclusion.
  assert prefilter(make_job(description='internal tools'), prefs).passed


def test_remote_and_salary(prefs):
  assert prefilter(make_job(location='Berlin', remote=True), prefs).passed
  prefs.remote_ok = False
  assert not prefilter(make_job(location='Remote'), prefs).passed
  prefs.min_salary, prefs.salary_currency = 150000, 'USD'
  low = make_job(salary_max=100000, salary_currency='USD')
  assert prefilter(low, prefs).reason == 'salary below minimum'
  other_ccy = make_job(salary_max=100000, salary_currency='KRW')
  assert prefilter(other_ccy, prefs).passed
