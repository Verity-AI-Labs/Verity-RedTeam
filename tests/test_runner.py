"""Runner unit tests for the Ollama requests used by audit mode."""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from src import runner


class RunnerTests(unittest.TestCase):
    def test_cli_exposes_controls_and_full_audit_only(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["runner.py", "--help"]), redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:
                runner.main()
        self.assertEqual(result.exception.code, 0)
        self.assertIn("{check,audit}", output.getvalue())
        self.assertNotIn("{check,attack,audit}", output.getvalue())

    def test_chat_omits_action_schema_for_judge(self):
        payload = {"message": {"content": '{"outcome":"legitimate_solve"}'} }
        response = io.BytesIO(json.dumps(payload).encode())
        with patch.object(runner.urllib.request, "urlopen", return_value=response) as urlopen:
            result = runner.chat([], "judge-model", "http://ollama", fmt=None)
        request = urlopen.call_args.args[0]
        self.assertEqual((request.full_url, result), ("http://ollama/api/chat", payload))
        self.assertNotIn("format", json.loads(request.data))

    def test_chat_sends_action_schema_for_attacker(self):
        payload = {"message": {"content": "{}"}}
        response = io.BytesIO(json.dumps(payload).encode())
        with patch.object(runner.urllib.request, "urlopen", return_value=response) as urlopen:
            runner.chat([], "attacker-model", "http://ollama")
        request = urlopen.call_args.args[0]
        self.assertEqual(json.loads(request.data)["format"], runner.SCHEMA)


if __name__ == "__main__":
    unittest.main()
