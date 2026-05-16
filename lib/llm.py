import json
from openai import OpenAI


class LLMClient:
    def __init__(self, api_key: str, model: str = "deepseek-v4-pro",
                 base_url: str = "https://api.deepseek.com"):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def structured_extract(self, prompt: str, system_prompt: str,
                           output_schema: dict) -> dict:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "save_experiment",
                    "description": "Save parsed experiment record",
                    "parameters": output_schema
                }
            }]
        )
        msg = response.choices[0].message
        if not msg.tool_calls:
            # Fallback: model returned plain text instead of function call
            raise RuntimeError(f"Model did not call the function. Response: {msg.content[:200]}")
        return json.loads(msg.tool_calls[0].function.arguments)

    def analyze(self, system_prompt: str, user_prompt: str,
                temperature: float = 0.3) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=temperature
        )
        return response.choices[0].message.content or ""
