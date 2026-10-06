from app.main import MessageProcessor


def test_message_processor_headers():
    """Test header levels #, ##, ### are converted to <b>."""
    assert MessageProcessor.decode("# Header 1") == "<b>Header 1</b>"
    assert MessageProcessor.decode("## Header 2") == "<b>Header 2</b>"
    assert MessageProcessor.decode("### Header 3") == "<b>Header 3</b>"
    assert MessageProcessor.decode("#### Header 4") == "<b>Header 4</b>"
    multiline_headers = "# Titulo 1\nTexto normal\n## Subtitulo 2\n### Subtitulo 3"
    decoded = MessageProcessor.decode(multiline_headers)
    assert "<b>Titulo 1</b>" in decoded
    assert "<b>Subtitulo 2</b>" in decoded
    assert "<b>Subtitulo 3</b>" in decoded


def test_message_processor_separators():
    """Test separators --- are converted to ──────────."""
    assert MessageProcessor.decode("---") == "──────────"
    assert MessageProcessor.decode("-----") == "──────────"
    assert MessageProcessor.decode("   ---   ") == "──────────"
    text_with_sep = "Seccion 1\n---\nSeccion 2"
    assert MessageProcessor.decode(text_with_sep) == "Seccion 1\n──────────\nSeccion 2"


def test_message_processor_markdown_table():
    """Test Markdown tables are converted to lists with <b>."""
    table_text = (
        "| Métrica | Valor |\n"
        "|---|---|\n"
        "| Ritmo | 5:00 min/km |\n"
        "| Distancia | 10 km |"
    )
    decoded = MessageProcessor.decode(table_text)
    assert "⏱️ <b>Ritmo:</b> 5:00 min/km" in decoded
    assert "📍 <b>Distancia:</b> 10 km" in decoded


def test_message_processor_inline_formatting():
    """Test inline formatting: **bold**, *bold*, _italic_, `code`."""
    assert MessageProcessor.decode("**texto en negrita**") == "<b>texto en negrita</b>"
    assert MessageProcessor.decode("*texto en negrita*") == "<b>texto en negrita</b>"
    assert MessageProcessor.decode("_texto en cursiva_") == "<i>texto en cursiva</i>"
    assert MessageProcessor.decode("`codigo inline`") == "<code>codigo inline</code>"

    combined = "**negrita 1** y *negrita 2* con _cursiva_ y `código`"
    expected = "<b>negrita 1</b> y <b>negrita 2</b> con <i>cursiva</i> y <code>código</code>"
    assert MessageProcessor.decode(combined) == expected


def test_message_processor_preserves_llm_html_tags():
    """Test valid HTML tags emitted by the LLM are NOT escaped."""
    llm_output = '<b>negrita</b> y <i>cursiva</i> y <code>código</code> y <a href="https://t.me">link</a>'
    decoded = MessageProcessor.decode(llm_output)
    assert decoded == llm_output
    assert "&lt;b&gt;" not in decoded
    assert "&lt;i&gt;" not in decoded
    assert "&lt;code&gt;" not in decoded


def test_message_processor_escapes_loose_special_chars():
    """Test loose <, >, & are escaped properly while preserving tags."""
    text = "Condición: 5 < 10 & 3 > 2"
    decoded = MessageProcessor.decode(text)
    assert decoded == "Condición: 5 &lt; 10 &amp; 3 &gt; 2"

    mixed = "<b>Resultado:</b> 5 < 10 & 3 > 2 con tag inválido <random>"
    decoded_mixed = MessageProcessor.decode(mixed)
    assert decoded_mixed == "<b>Resultado:</b> 5 &lt; 10 &amp; 3 &gt; 2 con tag inválido &lt;random&gt;"


def test_message_processor_idempotency():
    """Test reasonable idempotency: decoding already formatted text produces the same result."""
    original = (
        "# Reporte Semanal\n"
        "---\n"
        "Progreso **excelente** con ritmo *estable* y estado _óptimo_.\n"
        "Comando: `run_check()`\n"
        "<b>Anotación LLM</b>\n"
        "| Heart | 145 bpm |\n"
        "|---|---|\n"
        "| Distancia | 12 km |"
    )
    first_pass = MessageProcessor.decode(original)
    second_pass = MessageProcessor.decode(first_pass)
    assert first_pass == second_pass
