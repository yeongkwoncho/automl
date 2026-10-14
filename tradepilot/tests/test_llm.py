import json

import anthropic
import httpx2 as httpx

from tradepilot.llm import FALLBACK_BETA, ClaudeLLM
from tradepilot.models import DailyReview


def test_request_shape():
  seen = {}

  def handler(request):
    seen['body'] = json.loads(request.content)
    seen['headers'] = request.headers
    text = json.dumps({
        'summary': 's',
        'decision_reviews': [],
        'lessons': [],
        'rule_adjustments': [],
        'new_rule_ideas': []
    })
    return httpx.Response(200,
                          json={
                              'id': 'm',
                              'type': 'message',
                              'role': 'assistant',
                              'model': 'claude-opus-5',
                              'stop_reason': 'end_turn',
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
      api_key='t',
      http_client=httpx.Client(transport=httpx.MockTransport(handler)))
  out = ClaudeLLM(client=client).structured(system='S',
                                            content='c',
                                            schema=DailyReview)
  assert out.summary == 's'
  b = seen['body']
  assert b['model'] == 'claude-opus-5' and b['fallbacks'] == 'default'
  assert FALLBACK_BETA in seen['headers']['anthropic-beta']
  assert b['output_config']['format']['type'] == 'json_schema'
  assert b['system'][0]['cache_control'] == {'type': 'ephemeral'}
