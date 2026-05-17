import pytest
from main import MessageProcessor

def test_message_processor_basic_clean():
    """Test basic whitespace cleaning."""
    dirty_text = "  Hello   \n\n\n  World  "
    cleaned = MessageProcessor.encode(dirty_text)
    assert cleaned == "Hello\n\nWorld"

def test_message_processor_markdown_escaping():
    """Test MarkdownV2 escaping logic."""
    raw_text = "Hello *world* _underscore_ [link](http://test.com) - dot. ! bang"
    # The processor should escape reserved characters that are NOT part of the restoration logic
    # In this project, it seems to escape most things then restore some.
    escaped = MessageProcessor.decode(raw_text)
    
    # Check if some reserved chars are escaped
    assert r"\." in escaped
    assert r"\-" in escaped
    assert r"\!" in escaped
    # restoration logic should keep *, _, [ intact if they are balanced
    assert "*" in escaped
    assert "_" in escaped
    assert "[" in escaped
