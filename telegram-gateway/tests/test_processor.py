from main import MessageProcessor


def test_message_processor_basic_clean():
    """Test basic whitespace cleaning."""
    dirty_text = "  Hello   \n\n\n  World  "
    cleaned = MessageProcessor.encode(dirty_text)
    assert cleaned == "Hello\n\nWorld"


def test_message_processor_html_escaping():
    """Test HTML escaping logic."""
    raw_text = "Hello *world* _italic_ <tag> & amp"
    # The processor should escape HTML reserved characters and restore formatting
    decoded = MessageProcessor.decode(raw_text)

    # Check if some HTML reserved chars are escaped
    assert "&lt;tag&gt;" in decoded
    assert "&amp;" in decoded

    # restoration logic should convert * to <b> and _ to <i>
    assert "<b>world</b>" in decoded
    assert "<i>italic</i>" in decoded


def test_message_processor_splitting():
    """Test smart splitting at newlines."""
    long_text = "Line 1\nLine 2\nLine 3"
    # Split with a very small limit
    chunks = MessageProcessor.split_message(long_text, max_length=7)
    assert len(chunks) == 3
    assert chunks[0] == "Line 1"
    assert chunks[1] == "Line 2"
    assert chunks[2] == "Line 3"
