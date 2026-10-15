import json

import anthropic
import httpx2 as httpx
import pytest

from jobpilot.llm import FALLBACK_BETA, ClaudeLLM, LLMError
from jobpilot.models import MatchAssessment


def fake_client(stop_reason='end_turn', payload=None):
  seen = {}

  def handler(request: httpx.Request):
    seen['body'] = json.loads(request.content)
    seen['headers'] = request.headers
    text = json.dumps(
        payload or {
            'score': 120,
            'verdict': 'strong',
            'summary': 's',
            'matched_strengths': [],
            'gaps': [],
            'dealbreakers': [],
            'seniority_fit': 'match'
        })
    return httpx.Response(200,
                          json={
                              'id': 'msg_1',
                              'type': 'message',
                              'role': 'assistant',
                              'model': 'claude-opus-5',
                              'stop_reason': stop_reason,
                              'stop_sequence': None,
                              'content': [{
                                  'type': 'text',
                                  'text': text
                              }],
                              'usage': {
                                  'input_tokens': 1,
                                  'output_tokens': 1
                              }
                          })

  client = anthropic.Anthropic(
      api_key='test',
      http_client=httpx.Client(transport=httpx.MockTransport(handler)))
  return client, seen


def test_request_shape():
  client, seen = fake_client()
  out = ClaudeLLM(client=client).structured(system='SYS',
                                            content='job',
                                            schema=MatchAssessment,
                                            effort='medium')
  assert isinstance(out, MatchAssessment) and out.score == 120
  body = seen['body']
  assert body['model'] == 'claude-opus-5'
  assert body['fallbacks'] == 'default'
  assert FALLBACK_BETA in seen['headers']['anthropic-beta']
  assert body['system'][0]['cache_control'] == {'type': 'ephemeral'}
  assert body['thinking'] == {'type': 'adaptive'}
  assert body['output_config']['effort'] == 'medium'
  assert body['output_config']['format']['type'] == 'json_schema'


@pytest.mark.parametrize('reason', ['refusal', 'max_tokens'])
def test_bad_stop_reasons(reason):
  client, _ = fake_client(stop_reason=reason)
  with pytest.raises(LLMError):
    ClaudeLLM(client=client).structured(system='s',
                                        content='c',
                                        schema=MatchAssessment)
