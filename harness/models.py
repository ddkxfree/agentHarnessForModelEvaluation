import json
import urllib.request


class Model:
    """Base adapter: implement generate(prompt) -> str."""

    name = "model"

    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class EchoModel(Model):
    """Returns the prompt unchanged; useful for smoke tests."""

    name = "echo"

    def generate(self, prompt: str) -> str:
        return prompt


class OpenAICompatibleModel(Model):
    """Adapter for any OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, model: str, api_key: str, base_url: str = "https://api.openai.com/v1", timeout: float = 60):
        self.name = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(self, prompt: str) -> str:
        body = json.dumps({"model": self.name, "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)["choices"][0]["message"]["content"]
