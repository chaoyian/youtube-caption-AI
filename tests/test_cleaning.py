from yt_finance_kb.cleaning import clean_segments, transcript_hash
from yt_finance_kb.models import TranscriptSegment


def test_cleaning_removes_duplicates_stage_directions_and_short_ads():
    segments = [
        TranscriptSegment(start=0, text="【笑聲】"),
        TranscriptSegment(start=1, text="今天看金融市場"),
        TranscriptSegment(start=2, text="今天看金融市場"),
        TranscriptSegment(start=3, text="記得訂閱按讚"),
        TranscriptSegment(start=4, text="聯準會可能維持利率"),
    ]
    cleaned = clean_segments(segments)
    assert [item.text for item in cleaned] == ["今天看金融市場", "聯準會可能維持利率"]


def test_hash_changes_only_with_normalized_content():
    left = [TranscriptSegment(start=1.1, text="利率  上升")]
    right = [TranscriptSegment(start=1.9, text="利率 上升")]
    changed = [TranscriptSegment(start=2.1, text="利率 上升")]
    assert transcript_hash(left) == transcript_hash(right)
    assert transcript_hash(left) != transcript_hash(changed)

