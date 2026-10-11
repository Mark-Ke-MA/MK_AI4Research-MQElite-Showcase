"""Showcase-authored contract checks against a synthetic, in-memory turn API."""

from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from mqelite.codex.source import DurablePollingSource, SourceSnapshotChanged
from mqelite.codex.transport import WebSocketMessageTooLarge
from mqelite.errors import ValidationError


BASE = 1_800_000_000
MAIN = SimpleNamespace(persona_id="synthetic-persona", generation=1, thread_id="synthetic-thread")


def turn(identity, start, end, status="completed"):
    return dict(id=identity, startedAt=start, completedAt=end, status=status, items=[
        dict(id=identity + "-user", type="userMessage", content=[dict(type="text", text="synthetic question")]),
        dict(id=identity + "-tool", type="commandExecution", aggregatedOutput="excluded tool output"),
        dict(id=identity + "-agent", type="agentMessage", text="synthetic response"),
    ])


class FakeRpc:
    def __init__(self, turns, *, overflow=False, mutate=False):
        self.turns = turns
        self.calls = []
        self.positions = {}
        self.resets = 0
        self.overflow = overflow
        self.mutate = mutate

    def reset(self):
        self.resets += 1

    def request(self, method, params, **kwargs):
        assert method == "thread/turns/list"
        assert params["threadId"] == MAIN.thread_id
        self.calls.append(dict(params))
        start = self.positions[params["cursor"]] if "cursor" in params else 0
        stop = min(len(self.turns), start + params["limit"])
        data = copy.deepcopy(self.turns[start:stop])
        if params["itemsView"] == "notLoaded":
            data = [{key: value for key, value in item.items() if key != "items"} for item in data]
        else:
            if self.overflow and params["limit"] > 1:
                raise WebSocketMessageTooLarge("synthetic bound")
            if self.mutate and data:
                data[0]["completedAt"] += 1
        cursor = None
        if stop < len(self.turns):
            cursor = "opaque-" + str(stop * 7919)
            self.positions[cursor] = stop
        return dict(data=data, nextCursor=cursor)


def reader(rpc):
    return DurablePollingSource(rpc, persona_id=MAIN.persona_id,
                                digest_key=b"synthetic-public-key-not-a-secret", page_size=10)


def read(source):
    return source.fetch_canonical_events(main=MAIN, start_unix_ns=BASE * 10**9,
                                         end_unix_ns=(BASE + 100) * 10**9)


class DateFirstShowcaseTests(unittest.TestCase):
    def test_fetches_only_eligible_bodies_and_reuses_metadata(self):
        rpc = FakeRpc([turn("old", BASE - 20, BASE - 10),
                       turn("relevant", BASE + 1, BASE + 2),
                       turn("future", BASE + 101, BASE + 102)])
        source = reader(rpc)
        first = read(source)
        self.assertEqual(["user", "assistant"], [event.role for event in first.events])
        self.assertEqual(1, source.stats["full_turns"])
        inventory_calls = len([call for call in rpc.calls
                               if call["itemsView"] == "notLoaded" and call["limit"] == 10])
        second = read(source)
        self.assertEqual(first, second)
        self.assertEqual(inventory_calls, len([call for call in rpc.calls
                         if call["itemsView"] == "notLoaded" and call["limit"] == 10]))

    def test_overflow_resets_and_halves_body_batch(self):
        rpc = FakeRpc([turn(str(i), BASE + i, BASE + i + 1) for i in range(4)], overflow=True)
        source = reader(rpc)
        self.assertEqual(8, len(read(source).events))
        self.assertEqual(2, rpc.resets)
        self.assertEqual(2, source.stats["oversized_retries"])

    def test_repeated_metadata_change_has_bounded_retry(self):
        rpc = FakeRpc([turn("changing", BASE + 1, BASE + 2)], mutate=True)
        source = reader(rpc)
        with self.assertRaises(SourceSnapshotChanged):
            read(source)
        self.assertEqual(1, source.stats["snapshot_refreshes"])
        self.assertEqual(2, source.stats["full_requests"])

    def test_active_and_interrupted_turns_preserve_time_uncertainty(self):
        rpc = FakeRpc([turn("active", BASE + 1, None, "inProgress"),
                       turn("stopped", BASE + 3, None, "interrupted")])
        events = read(reader(rpc)).events
        self.assertEqual(["user", "user", "assistant"], [event.role for event in events])
        self.assertIsNone(events[-1].timestamp)
        self.assertEqual("text_untimed_interrupted", events[-1].message_type)
        self.assertTrue(all("tool output" not in event.content for event in events))

    def test_cross_persona_request_rejected_before_rpc(self):
        rpc = FakeRpc([])
        with self.assertRaises(ValidationError):
            reader(rpc).fetch_canonical_events(
                main=SimpleNamespace(persona_id="other-persona", generation=1, thread_id=MAIN.thread_id),
                start_unix_ns=BASE * 10**9, end_unix_ns=(BASE + 100) * 10**9)
        self.assertEqual([], rpc.calls)
