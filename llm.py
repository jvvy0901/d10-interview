import json
import os

import openai
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DEFAULT_MODEL = "gpt-6-luna"


class LLMError(Exception):
    """화면에 그대로 보여 줄 수 있는 한국어 한 줄 에러."""


def ask_json(instructions, user_input, schema, name):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise LLMError(".env 에 OPENAI_API_KEY 가 없어요. 키를 넣고 다시 실행해 주세요.")

    model = os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=user_input,
            text={
                "format": {
                    "type": "json_schema",
                    "name": name,
                    "schema": schema,
                    "strict": True,
                }
            },
        )
        return json.loads(response.output_text)
    except openai.AuthenticationError:
        raise LLMError("API 키가 올바르지 않아요. .env 의 OPENAI_API_KEY 를 확인해 주세요.") from None
    except openai.RateLimitError as e:
        if getattr(e, "code", None) == "insufficient_quota":
            raise LLMError("OpenAI 크레딧이 부족해요. 결제 · 잔액을 확인해 주세요.") from None
        raise LLMError("요청이 너무 많아요. 잠시 뒤에 다시 시도해 주세요.") from None
    except (openai.APIConnectionError, openai.APITimeoutError):
        raise LLMError("OpenAI 에 연결하지 못했어요. 인터넷 연결을 확인해 주세요.") from None
    except openai.NotFoundError:
        raise LLMError("모델을 찾을 수 없어요. .env 의 OPENAI_MODEL 을 확인해 주세요.") from None
    except (openai.APIError, json.JSONDecodeError):
        raise LLMError("AI 응답을 받지 못했어요. 잠시 뒤에 다시 시도해 주세요.") from None
