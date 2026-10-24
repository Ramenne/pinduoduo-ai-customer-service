"""Command Code SDK 协议与错误处理回归。"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from common.services import ai_provider_service as provider

BASE = "https://api.commandcode.ai/provider/v1"

class CommandCodeTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.factory = MagicMock()
        self.factory.return_value.__enter__.return_value = self.client
        self.module = SimpleNamespace(OpenAI=self.factory)

    def test_connection_uses_sdk_and_requires_visible_text(self):
        response = self.client.chat.completions.create.return_value
        response.model_dump.return_value = {"choices": [{"message": {"content": "测试成功"}}]}
        with patch.dict(sys.modules, {"openai": self.module}):
            result = provider.test_ai_connection("openai_compatible", BASE, "test-key", "deepseek/deepseek-v4.1-flash")
            self.assertEqual(result, "测试成功")
            response.model_dump.return_value = {"choices": [{"message": {"content": ""}}]}
            with self.assertRaises(provider.AiProviderError):
                provider.test_ai_connection("openai_compatible", BASE, "test-key", "deepseek/deepseek-v4.1-flash")
        self.factory.return_value.__exit__.assert_called()

    def test_model_list_uses_sdk(self):
        model = MagicMock()
        model.model_dump.return_value = {"id": "deepseek/deepseek-v4.1-flash", "name": "Flash"}
        self.client.models.list.return_value = [model]
        with patch.dict(sys.modules, {"openai": self.module}):
            result = provider.fetch_model_list("openai_compatible", BASE, "test-key")
        self.assertEqual(result, [{"id": "deepseek/deepseek-v4.1-flash", "name": "Flash"}])

    def test_error_does_not_expose_secret(self):
        self.client.chat.completions.create.side_effect = RuntimeError("sensitive-test-key")
        with patch.dict(sys.modules, {"openai": self.module}):
            with self.assertRaises(provider.AiProviderError) as caught:
                provider.test_ai_connection("openai_compatible", BASE, "sensitive-test-key", "model")
        self.assertNotIn("sensitive-test-key", str(caught.exception))

    def test_other_provider_retains_existing_transport(self):
        with patch.object(provider, "_http_post_json", return_value={"choices": [{"message": {"content": "正常"}}]}) as request:
            self.assertEqual(provider.test_ai_connection("openai_compatible", "https://example.com/v1", "test-key", "model"), "正常")
            request.assert_called_once()

if __name__ == "__main__":
    unittest.main()
