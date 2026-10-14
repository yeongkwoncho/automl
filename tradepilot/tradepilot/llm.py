"""Claude wrapper for structured (Pydantic) outputs."""
from typing import List, Optional, Protocol, Type, TypeVar, Union

import anthropic
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)

DEFAULT_MODEL = 'claude-opus-5'
# If the model declines, the API re-runs the request on Anthropic's
# recommended fallback model within the same call.
FALLBACK_BETA = 'server-side-fallback-2026-07-01'


class LLMError(RuntimeError):
  pass


class StructuredLLM(Protocol):

  def structured(self,
                 *,
                 system: str,
                 content: Union[str, List[dict]],
                 schema: Type[T],
                 effort: str = 'high') -> T:
    ...


class ClaudeLLM:

  def __init__(self,
               model: str = DEFAULT_MODEL,
               client: Optional[anthropic.Anthropic] = None,
               max_tokens: int = 16000):
    self.model = model
    self.client = client or anthropic.Anthropic()
    self.max_tokens = max_tokens

  def structured(self, *, system, content, schema, effort='high'):
    try:
      resp = self.client.beta.messages.parse(
          model=self.model,
          max_tokens=self.max_tokens,
          system=[{
              'type': 'text',
              'text': system,
              'cache_control': {
                  'type': 'ephemeral'
              }
          }],
          messages=[{
              'role': 'user',
              'content': content
          }],
          thinking={'type': 'adaptive'},
          output_config={'effort': effort},
          output_format=schema,
          betas=[FALLBACK_BETA],
          fallbacks='default',
      )
    except anthropic.RateLimitError as e:
      raise LLMError(f'rate limited: {e.message}') from e
    except anthropic.APIStatusError as e:
      raise LLMError(f'API error {e.status_code}: {e.message}') from e
    except anthropic.APIConnectionError as e:
      raise LLMError(f'connection error: {e}') from e
    if resp.stop_reason == 'refusal':
      raise LLMError('model declined the request')
    if resp.stop_reason == 'max_tokens':
      raise LLMError('response truncated at max_tokens')
    if resp.parsed_output is None:
      raise LLMError('no structured output returned')
    return resp.parsed_output
