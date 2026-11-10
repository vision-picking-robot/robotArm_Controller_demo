import asyncio
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.packing import Packing
from app.store import Store
from app.voice import Voice
from app.voice_commands import INTENTS, normalized, packing_answer


class VoiceCommandsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(directory.name)
        self.addCleanup(self.store.close)
        self.packing = Packing(self.store)

    def test_queries_mutations_and_exact_phrases(self):
        self.assertIn("생수 4개", packing_answer(self.packing, "query-stock").text)
        self.assertIn("123번", packing_answer(self.packing, "query-order").text)
        for phrase, paused in [("멈춰!", True), ("피킹 재개해 줘.", False)]:
            packing_answer(self.packing, INTENTS[normalized(phrase)])
            self.assertEqual(self.packing.order(123)["paused"], paused)
        self.assertNotIn(normalized("피킹 재개하지 마"), INTENTS)
        self.assertEqual(INTENTS[normalized("응, 주문 내역 알려줘.")], "query-order")
        self.assertEqual(INTENTS[normalized("네, 재고 조회해 줘.")], "query-stock")
        self.assertNotIn(normalized("응, 주문 확정"), INTENTS)
        self.assertNotIn(normalized("최고 알려줘"), INTENTS)
        self.assertNotIn(normalized("이건 주문 확정 아니야"), INTENTS)
        self.assertTrue(packing_answer(self.packing, "cmd-order").confirm)
        self.assertIn("취소했습니다", packing_answer(self.packing, "discard-order", confirming=True).text)
        self.assertIn("없습니다", packing_answer(self.packing, "confirm-order").text)
        for _ in range(2):
            packing_answer(self.packing, "confirm-order", confirming=True)
        self.assertFalse(packing_answer(self.packing, "cmd-order").confirm)
        self.assertEqual(len([e for e in self.store.list("packing_event") if "#124" in e["message"]]), 1)


