import sys

sys.path.append("/home/fsirio/telegram-agent-orchestrator/telegram-gateway")
from main import MessageProcessor

sample_text = """
### Plan de corrección
Este es un texto largo para probar cómo se divide y decodifica.
`user_id` de telegram `fedeale_s` y `fsirio`.

""" + (
    "Aca va una linea larga con **negrita** y _italica_ y `codigo` y palabras_con_guiones_bajos.\n"
    * 100
)

print(f"Total length: {len(sample_text)}")
chunks = MessageProcessor.split_message(sample_text, 3800)
print(f"Number of chunks: {len(chunks)}")
for i, chunk in enumerate(chunks):
    decoded = MessageProcessor.decode(chunk)
    print(f"Chunk {i} raw len: {len(chunk)}, decoded len: {len(decoded)}")
    # Check for unclosed HTML tags
    b_open = decoded.count("<b>")
    b_close = decoded.count("</b>")
    i_open = decoded.count("<i>")
    i_close = decoded.count("</i>")
    print(f"  <b> open/close: {b_open}/{b_close}, <i> open/close: {i_open}/{i_close}")
