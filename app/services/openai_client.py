# вызов ChatGPT/OpenAI

from openai import OpenAI

class OpenAIClient:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def generate(self, user_text: str) -> str:
        # Responses API — рекомендованный интерфейс :contentReference[oaicite:1]{index=1}
        resp = self.client.responses.create(
            model=self.model,
            input=user_text,
        )
        return resp.output_text