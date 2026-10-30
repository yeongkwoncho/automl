from jobpilot.appliers.formfill import (FieldInfo, match_option, plan_fill,
                                        resolve_questions)
from jobpilot.models import QuestionAnswer


def F(i, label, tag='input', type_='text', **kw):
  return FieldInfo(index=i, tag=tag, type=type_, label=label, **kw)


def test_plan_fill(profile):
  fields = [
      F(0, 'First Name *', required=True),
      F(1, 'Last Name', required=True),
      F(2, 'Email', type_='email'),
      F(3, 'Phone'),
      F(4, 'Resume/CV', type_='file', required=True),
      F(5, 'LinkedIn Profile'),
      F(6, 'Cover Letter', tag='textarea'),
      F(7, 'Notice period'),
      F(8,
        'Gender',
        tag='select',
        options=['Select...', 'Male', 'Female', 'Decline to self-identify']),
      F(9, 'I agree to the privacy policy', type_='checkbox', required=True),
      F(10, 'Years of PyTorch experience?', required=True),
      F(11, '', type_='hidden'),
      F(12, 'Current company'),
      F(13, 'School name'),
  ]
  plan = plan_fill(fields, profile, 'LETTER', '/tmp/cl.txt')
  got = {a.index: (a.kind, a.value) for a in plan.actions}
  assert got[0] == ('fill', 'Jane') and got[1] == ('fill', 'Doe')
  assert got[2] == ('fill', 'jane@example.com')
  assert got[4] == ('upload', profile.resume_path)
  assert got[5] == ('fill', 'https://li/jane')
  assert got[6] == ('fill', 'LETTER')
  assert got[7] == ('fill', '1 month')
  assert got[8] == ('select', 'Decline to self-identify')
  assert got[12] == ('fill', 'Acme')
  assert 13 not in got  # "School name" is not the candidate's name
  assert [f.index for f in plan.unresolved] == [9]
  assert [f.index for f in plan.questions] == [10, 13]


def test_resolve_questions(profile):
  fields = [
      F(0, 'Years of PyTorch?', required=True),
      F(1, 'Visa status?', required=True),
      F(2, 'Level', tag='select', options=['Junior', 'Senior'], required=True)
  ]
  plan = plan_fill(fields, profile, '', None)

  def answer_fn(qs):
    return [
        QuestionAnswer(question=qs[0], answer='5', needs_human=False),
        QuestionAnswer(question=qs[1], answer='', needs_human=True),
        QuestionAnswer(question=qs[2], answer='senior', needs_human=False)
    ]

  plan = resolve_questions(plan, answer_fn)
  assert {(a.index, a.value) for a in plan.actions} == {(0, '5'), (2, 'Senior')}
  assert [f.index for f in plan.unresolved] == [1]


def test_match_option():
  opts = ['Select...', 'Yes', 'No']
  assert match_option('yes', opts) == 'Yes'
  assert match_option('maybe', opts) is None
