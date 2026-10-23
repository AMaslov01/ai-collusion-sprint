"""Offline checks of smoke admission; run only in the server's CPU container."""

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from collusion_handoff.smoke import InputBudget, admission


class SmokeBoundsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stop = Path(self.directory.name) / "STOP"
        self.deadline = time.time() + 2000
        self.payload = {
            "model": "fixture", "messages": [{"role": "user", "content": "fixture"}],
            "tools": [], "chat_template_kwargs": {"enable_thinking": False}, "max_tokens": 4096,
        }

    def test_stop_and_deadline_prevent_tokenization(self):
        with self.assertRaises(RuntimeError):
            admission(time.time() - 1, self.stop)
        self.stop.touch()
        def unexpected(request):
            self.fail("STOP must prevent even the tokenizer request")
        with httpx.Client(transport=httpx.MockTransport(unexpected)) as http:
            with self.assertRaises(RuntimeError):
                InputBudget(http, self.deadline, stop=self.stop)(self.payload)

    def test_input_limit_is_inclusive_and_preserves_full_template_inputs(self):
        sent = []
        def tokenize(request):
            sent.append(json.loads(request.read()))
            return httpx.Response(200, json={"max_model_len": 32768, "count": 28672})
        with httpx.Client(transport=httpx.MockTransport(tokenize)) as http, patch("collusion_handoff.smoke.save"):
            guard = InputBudget(http, self.deadline, stop=self.stop)
            for _ in range(3):
                guard(self.payload)
            with self.assertRaises(RuntimeError):
                guard(self.payload)
        self.assertEqual(len(sent), 3)
        self.assertEqual(sent[0]["messages"], self.payload["messages"])
        self.assertEqual(sent[0]["tools"], self.payload["tools"])
        self.assertEqual(sent[0]["chat_template_kwargs"], {"enable_thinking": False})

    def test_overflow_is_rejected_without_admitting_generation(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"max_model_len": 32768, "count": 28673}
        ))) as http:
            guard = InputBudget(http, self.deadline, stop=self.stop)
            with self.assertRaises(RuntimeError):
                guard(self.payload)
            self.assertEqual(guard.counts, [])
