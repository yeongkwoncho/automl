"""Thin wrapper around the Claude API for structured (Pydantic) outputs."""
import base64
import logging
from typing import List, Optional, Protocol, Type, TypeVar, Union

import anthropic
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseModel)

DEFAULT_MODEL = 'claude-opus-5'
# Server-side refusal fallback: if the model declines, the API re-runs the
# request on Anthropic's recommended fallback model within the same call.
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
  """Calls Claude and returns a validated instance of `schema`."""

  def __init__(self,
               model: str = DEFAULT_MODEL,
               client: Optional[anthropic.Anthropic] = None,
               max_tokens: int = 16000):
    self.model = model
    self.client = client or anthropic.Anthropic()
    self.max_tokens = max_tokens

  def structured(self,
                 *,
                 system: str,
                 content: Union[str, List[dict]],
                 schema: Type[T],
                 effort: str = 'high') -> T:
    try:
      resp = self.client.beta.messages.parse(
          model=self.model,
          max_tokens=self.max_tokens,
          # The system prompt holds the (large, stable) candidate profile, so
          # caching it makes scoring many jobs much cheaper.
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


def document_block(path: str) -> dict:
  """Builds a content block for a resume file (PDF or plain text)."""
  with open(path, 'rb') as f:
    data = f.read()
  if path.lower().endswith('.pdf'):
    return {
        'type': 'document',
        'source': {
            'type': 'base64',
            'media_type': 'application/pdf',
            'data': base64.b64encode(data).decode()
        }
    }
  return {
      'type': 'document',
      'source': {
          'type': 'text',
          'media_type': 'text/plain',
          'data': data.decode('utf-8', errors='replace')
      }
  }
