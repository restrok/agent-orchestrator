from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.main import send_telegram_notification, split_text_into_chunks

# ==========================================
# Unit Tests for split_text_into_chunks
# ==========================================


def test_split_text_empty():
    """Verify empty or whitespace-only text produces an empty list."""
    assert split_text_into_chunks("") == []
    assert split_text_into_chunks("   \n\t  ") == []


def test_split_text_short():
    """Verify text shorter than max_chunk_size produces exactly 1 chunk."""
    text = "Hello, this is a short notification message."
    chunks = split_text_into_chunks(text, max_chunk_size=4000)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_split_text_exact_boundary():
    """Verify text exactly at max_chunk_size produces 1 chunk without splitting."""
    text = "A" * 4000
    chunks = split_text_into_chunks(text, max_chunk_size=4000)
    assert len(chunks) == 1
    assert len(chunks[0]) == 4000


def test_split_text_long_with_newlines():
    """Verify text with single newlines splits cleanly at line breaks."""
    # Create 50 lines of 100 characters each (~5000 chars)
    lines = [f"Line {i:03d}: {'x' * 90}" for i in range(50)]
    text = "\n".join(lines)
    chunks = split_text_into_chunks(text, max_chunk_size=4000)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 4000
        # Each chunk should end cleanly without cutting inside a line
        assert not chunk.endswith("Line")

    # Reconstituted lines should match original lines
    reconstituted_lines = []
    for chunk in chunks:
        reconstituted_lines.extend(chunk.split("\n"))
    assert reconstituted_lines == lines


def test_split_text_long_with_paragraphs():
    """Verify text with paragraph breaks (\n\n) splits preferentially at paragraph boundaries."""
    para1 = "Paragraph 1: " + "a" * 2500
    para2 = "Paragraph 2: " + "b" * 2500
    text = f"{para1}\n\n{para2}"

    chunks = split_text_into_chunks(text, max_chunk_size=4000)
    assert len(chunks) == 2
    assert chunks[0] == para1
    assert chunks[1] == para2


def test_split_text_long_with_spaces():
    """Verify text without newlines splits at space breaks without splitting words."""
    words = [f"word{i}" for i in range(1000)]  # ~6000 chars
    text = " ".join(words)
    chunks = split_text_into_chunks(text, max_chunk_size=4000)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 4000

    # Ensure words were not cut in half
    reconstituted_words = []
    for chunk in chunks:
        reconstituted_words.extend(chunk.split())
    assert reconstituted_words == words


def test_split_text_unbreakable_long_string():
    """Verify continuous string with no spaces or newlines splits safely within max_chunk_size."""
    text = "Z" * 9500
    chunks = split_text_into_chunks(text, max_chunk_size=4000)

    assert len(chunks) == 3
    assert len(chunks[0]) == 4000
    assert len(chunks[1]) == 4000
    assert len(chunks[2]) == 1500
    for chunk in chunks:
        assert len(chunk) <= 4000


# ==========================================
# Unit Tests for send_telegram_notification
# ==========================================


@pytest.fixture(autouse=True)
def setup_telegram_token(monkeypatch):
    """Ensure TELEGRAM_BOT_TOKEN is set for test cases."""
    monkeypatch.setattr("app.main.TELEGRAM_BOT_TOKEN", "123456:TEST_BOT_TOKEN")


@pytest.mark.asyncio
async def test_send_telegram_notification_short_message():
    """Verify short message sends a single request with parse_mode HTML."""
    message = "✅ Worker completed successfully!"
    user_id = "12345678"  # Numeric chat_id

    mock_resp = httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        success = await send_telegram_notification(user_id, message)

        assert success is True
        assert mock_post.call_count == 1
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"]["chat_id"] == "12345678"
        assert call_kwargs["json"]["text"] == message
        assert call_kwargs["json"]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_send_telegram_notification_long_message_chunking():
    """Verify long message exceeding 4000 chars is split and sent sequentially with delay."""
    # Create ~9000 chars text
    paragraphs = [f"Section {i}: " + ("x" * 1500) for i in range(6)]
    message = "\n\n".join(paragraphs)
    user_id = "12345678"

    mock_resp = httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    with (
        patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        mock_post.return_value = mock_resp

        success = await send_telegram_notification(user_id, message, delay_between_chunks=0.1)

        assert success is True
        assert mock_post.call_count > 1

        # All chunks sent must be <= 4000 chars
        for call in mock_post.call_args_list:
            sent_text = call.kwargs["json"]["text"]
            assert len(sent_text) <= 4000

        # Delay should be called for each subsequent chunk (call_count - 1 times)
        assert mock_sleep.call_count == mock_post.call_count - 1
        mock_sleep.assert_called_with(0.1)


@pytest.mark.asyncio
async def test_send_telegram_notification_with_newlines():
    """Verify long message with line breaks preserves lines across chunks."""
    lines = [f"Log record #{i}: status=OK details={'data' * 20}" for i in range(60)]
    message = "\n".join(lines)
    user_id = "12345678"

    mock_resp = httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        success = await send_telegram_notification(user_id, message, delay_between_chunks=0)

        assert success is True
        assert mock_post.call_count > 1
        for call in mock_post.call_args_list:
            sent_text = call.kwargs["json"]["text"]
            assert len(sent_text) <= 4000
            # Ensure lines aren't cut mid-word
            assert not sent_text.endswith("Log record #")


@pytest.mark.asyncio
async def test_send_telegram_notification_inline_keyboard_on_last_chunk():
    """Verify inline_keyboard is only attached to the last chunk when chunking occurs."""
    message = "A" * 3500 + "\n\n" + "B" * 3500
    user_id = "12345678"
    keyboard = [[{"text": "Aprobar", "callback_data": "approve"}]]

    mock_resp = httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        success = await send_telegram_notification(user_id, message, inline_keyboard=keyboard, delay_between_chunks=0)

        assert success is True
        assert mock_post.call_count == 2

        first_call_json = mock_post.call_args_list[0].kwargs["json"]
        second_call_json = mock_post.call_args_list[1].kwargs["json"]

        # First chunk should NOT have reply_markup
        assert "reply_markup" not in first_call_json
        # Second (last) chunk MUST have reply_markup
        assert second_call_json.get("reply_markup") == {"inline_keyboard": keyboard}


@pytest.mark.asyncio
async def test_send_telegram_notification_html_fallback():
    """Verify that if Telegram returns HTML entity parse error, it falls back to plain text."""
    message = "Broken <b>HTML tag without closing"
    user_id = "12345678"

    # First call fails with entity parse error, second call succeeds
    error_resp = httpx.Response(
        400,
        json={"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"},
    )
    success_resp = httpx.Response(200, json={"ok": True, "result": {"message_id": 2}})

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [error_resp, success_resp]

        success = await send_telegram_notification(user_id, message)

        assert success is True
        assert mock_post.call_count == 2
        first_call_payload = mock_post.call_args_list[0].kwargs["json"]
        second_call_payload = mock_post.call_args_list[1].kwargs["json"]

        assert first_call_payload.get("parse_mode") == "HTML"
        assert "parse_mode" not in second_call_payload  # Fallback to plain text


@pytest.mark.asyncio
async def test_send_telegram_notification_failure_stops_sequence():
    """Verify that if an HTTP error occurs on a chunk, it halts and returns False."""
    message = "Chunk 1 content " * 300 + "\n\n" + "Chunk 2 content " * 300
    user_id = "12345678"

    error_resp = httpx.Response(
        500,
        json={"ok": False, "error_code": 500, "description": "Internal Server Error"},
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = error_resp

        success = await send_telegram_notification(user_id, message, delay_between_chunks=0)

        assert success is False
        # Only the first chunk was attempted before failing
        assert mock_post.call_count == 1
