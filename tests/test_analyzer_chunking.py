from yt_finance_kb.analyzer import CHUNK_SIZE, PoeAnalyzer


def test_chunking_preserves_timestamped_lines():
    lines = [f"[{index}] 金融观点 {index} " + ("内容" * 100) for index in range(400)]
    transcript = "\n".join(lines)
    chunks = PoeAnalyzer._chunks(transcript)
    assert len(chunks) > 1
    assert all(len(chunk) <= CHUNK_SIZE + len(lines[0]) for chunk in chunks)
    assert "\n".join(chunks) == transcript

