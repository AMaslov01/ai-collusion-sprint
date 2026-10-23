"""Offline fixtures. Run in non-root Docker on Caniculus, never on the laptop.

MEMORYARENA_ROOT must point to the verified pinned upstream checkout. Fixture
seeds, aliases and tool responses below are not experimental configuration.
"""

from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import httpx
import openai

from collusion_handoff.model_gateway import (
    ActorGatewayTransport, ActorRequestPolicy, WireJournal, install_native_actor_gateway,
)


FIXTURE_GENERATION = {
    "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0,
    "presence_penalty": 1.5, "repetition_penalty": 1.0,
    "chat_template_kwargs": {"enable_thinking": False}, "seed": 123,
}
FIXTURE_TOOLS = [{"type": "function", "function": {
    "name": "FixtureSearch", "description": "Offline fixture only",
    "parameters": {"type": "object", "properties": {}},
}}]


def fixture_response(*, tool_call: bool = False) -> dict:
    message = {"role": "assistant", "content": "fixture final plan"}
    if tool_call:
        message = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "fixture-tool-1", "type": "function",
            "function": {"name": "FixtureSearch", "arguments": "{}"},
        }]}
    return {
        "id": "fixture-response", "object": "chat.completion", "created": 0,
        "model": "qwen-fixture", "choices": [{
            "index": 0, "message": message,
            "finish_reason": "tool_calls" if tool_call else "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class ActorGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        upstream = Path(os.environ["MEMORYARENA_ROOT"])
        sys.path.insert(0, str(upstream))
        module = importlib.import_module(
            "env.env_systems.travel_planner_env.clients.openai_client"
        )
        cls.native_client_class = module.OpenAIClient

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.io = Path(self.temp.name) / "io"
        self.io.mkdir(mode=0o700)
        self.journal = WireJournal(self.io)
        self.policy = ActorRequestPolicy(
            generation=FIXTURE_GENERATION, served_alias="qwen-fixture", max_output=4096, n=1,
        )
        self.sent = []
        self.context = {"logical_call_id": "fixture/native/task/turn-0", "role": "A", "attempt": 1}

    def client(self, handler):
        native = self.native_client_class(
            model_name="qwen-fixture", api_key="fixture-secret-do-not-log",
            base_url="http://127.0.0.1:18000/v1",
        )

        def capture(request):
            self.sent.append(request.read())
            return handler(request)

        install_native_actor_gateway(
            native, policy=self.policy, journal=self.journal,
            call_context=lambda: self.context.copy(), before_call=lambda payload: None,
            timeout_seconds=1, inner=httpx.MockTransport(capture),
        )
        self.addCleanup(native.client.close)
        return native

    def rows(self, stream):
        return [json.loads(line) for line in (self.io / f"{stream}.jsonl").read_text().splitlines()]

    def test_native_default_is_overridden_at_the_wire(self):
        native = self.client(lambda request: httpx.Response(200, json=fixture_response()))
        messages = [{"role": "user", "content": "Fixture query — preserve exactly"}]
        original = deepcopy(messages)
        native.chat_with_tools(messages, FIXTURE_TOOLS)
        request = json.loads(self.sent[0])
        self.assertEqual({key: request[key] for key in FIXTURE_GENERATION}, FIXTURE_GENERATION)
        self.assertEqual(request["messages"], original)
        self.assertEqual(messages, original)
        self.assertEqual(request["tools"], FIXTURE_TOOLS)
        self.assertEqual(request["max_tokens"], 4096)
        self.assertEqual(request["n"], 1)
        row = self.rows("requests")[0]
        self.assertEqual(json.loads(row["incoming_body_utf8"])["temperature"], 0.0)
        self.assertEqual(row["body_utf8"].encode(), self.sent[0])
        self.assertNotIn("fixture-secret-do-not-log", (self.io / "requests.jsonl").read_text())
        self.assertEqual(native.client.max_retries, 0)

    def test_same_sampler_for_both_roles_in_all_arms(self):
        native = self.client(lambda request: httpx.Response(200, json=fixture_response()))
        for role in ("A", "A_prime", "B"):
            for arm in ("F", "S", "C", "CV", "CVP", "R"):
                self.context.update(role=role, logical_call_id=f"fixture/{arm}/{role}/turn-0")
                native.chat_with_tools([{"role": "user", "content": arm}], FIXTURE_TOOLS)
        self.assertEqual(len(self.sent), 18)
        for body in self.sent:
            request = json.loads(body)
            self.assertEqual({key: request[key] for key in FIXTURE_GENERATION}, FIXTURE_GENERATION)

    def test_native_tool_result_and_final_plan_cycle(self):
        native = self.client(lambda request: httpx.Response(
            200, json=fixture_response(tool_call=len(self.sent) == 1),
        ))
        messages = [{"role": "user", "content": "Fixture request"}]
        first = native.chat_with_tools(messages, FIXTURE_TOOLS)
        self.assertEqual(first.tool_calls[0].name, "FixtureSearch")
        messages.append(native.format_assistant_tool_calls(first.tool_calls))
        tool_result = "fixture catalog row\nprice=120\nUnicode: Москва"
        messages.append(native.format_tool_result("fixture-tool-1", tool_result))
        self.context["logical_call_id"] = "fixture/native/task/turn-1"
        final = native.chat_with_tools(messages, FIXTURE_TOOLS)
        sent = json.loads(self.sent[1])
        self.assertEqual(sent["messages"][-1], {
            "role": "tool", "tool_call_id": "fixture-tool-1", "content": tool_result,
        })
        self.assertEqual(final.content, "fixture final plan")
        self.assertEqual(len(self.rows("responses")), 2)

    def test_sdk_does_not_retry_server_errors(self):
        native = self.client(lambda request: httpx.Response(500, json={"error": {"message": "fixture"}}))
        with self.assertRaises(openai.InternalServerError):
            native.chat_with_tools([{"role": "user", "content": "fixture"}], FIXTURE_TOOLS)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.rows("responses")[0]["status_code"], 500)

    def test_transport_failure_is_logged_once_without_retry(self):
        def fail(request):
            raise httpx.ReadTimeout("fixture timeout", request=request)

        native = self.client(fail)
        with self.assertRaises(openai.APITimeoutError):
            native.chat_with_tools([{"role": "user", "content": "fixture"}], FIXTURE_TOOLS)
        self.assertEqual(len(self.sent), 1)
        row = self.rows("responses")[0]
        self.assertEqual(row["transport_error_type"], "ReadTimeout")
        self.assertTrue(row["unknown_spend"])

    def test_builders_do_not_inherit_actor_sampler(self):
        native = self.client(lambda request: httpx.Response(200, json=fixture_response()))
        self.context["role"] = "V"
        with self.assertRaises(openai.APIConnectionError):
            native.client.chat.completions.create(
                model="qwen-fixture", messages=[{"role": "user", "content": "fixture"}],
            )
        self.assertEqual(self.sent, [])

    def test_unresolved_seed_prevents_policy_creation(self):
        generation = {**FIXTURE_GENERATION, "seed": "UNSET"}
        with self.assertRaises(ValueError):
            ActorRequestPolicy(generation=generation, served_alias="qwen-fixture", max_output=4096, n=1)

    def test_external_endpoint_is_rejected_before_dispatch(self):
        transport = ActorGatewayTransport(
            inner=httpx.MockTransport(lambda request: self.fail("Must not dispatch")),
            policy=self.policy, journal=self.journal,
            call_context=lambda: self.context.copy(), before_call=lambda payload: None,
        )
        with self.assertRaises(ValueError):
            transport.handle_request(httpx.Request("POST", "https://example.com/v1/chat/completions", json={}))


if __name__ == "__main__":
    unittest.main()
