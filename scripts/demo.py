"""Run a disposable demonstration; no configuration, network, or runtime discovery."""

from __future__ import annotations

import hashlib
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit("此展示需要 Python 3.11 或更新版本；请显式选择合适的 Python 解释器。")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mqelite.archive import ArchiveDay, build_archive, verify_archive
from mqelite.domain.events import ConversationExcerpt
from mqelite.domain.l2 import L2Document
from mqelite.domain.time_window import MemoryDayWindow
from mqelite.index import IndexedDocument, RecallIndex
from mqelite.recall import RecallService
from mqelite.storage.layout import DataLayout
from mqelite.storage.reconcile import DailyReconciler


def main():
    day = date(2026, 3, 28)
    document = L2Document(day, "synthetic-persona", (
        ConversationExcerpt("user", "Research decision: preserve the calibration baseline.", 0),
        ConversationExcerpt("assistant", "研究记录：保留校准基线，重建检索索引。", 1),
    ))
    with tempfile.TemporaryDirectory(prefix="mqelite-showcase-") as directory:
        layout = DataLayout(Path(directory) / "data").persona("synthetic-persona")
        reconciler = DailyReconciler(layout)
        source_hash = hashlib.sha256(b"synthetic-source").hexdigest()
        first = reconciler.reconcile(document, source_hash=source_hash)
        repeat = reconciler.reconcile(document, source_hash=source_hash)
        assert first.changed and not repeat.changed
        index = RecallIndex(layout)
        index.rebuild([IndexedDocument.from_native(document)], source_root_hash=source_hash)
        hits = RecallService(index).search("校准", max_chars=300)
        assert len(hits) == 1
        before = layout.recall_db.read_bytes()
        assert len(RecallService(index).exact(day)) == 2
        assert before == layout.recall_db.read_bytes()
        bundle = build_archive(persona_id="synthetic-persona", year=2026, month=3,
                               generation=1, created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                               days=[ArchiveDay(day, document.canonical_bytes(), "native")],
                               tool_version="showcase")
        verified = verify_archive(bundle.tar_bytes, bundle.external_manifest,
                                  expected_persona_id="synthetic-persona")
        assert len(verified["members"]) == 1
        print("每日提交：首次写入；相同输入重试保持 JSON 不变")
        print("召回：中文查询命中合成记录；按日查询保持索引字节不变")
        print("归档：一个合成日期成员验证通过（未运行完整月度生命周期）")
        print(f"英国春季时钟切换的记忆日： {MemoryDayWindow.for_date(day).elapsed_seconds / 3600:g} 小时")
    print("演示临时数据已清理")


if __name__ == "__main__":
    main()
