import re

class MessageProcessor:
    @staticmethod
    def decode(text: str) -> str:
        """
        Robustly formats text for Telegram's MarkdownV2.
        """
        if not text:
            return ""

        # 1. Initial cleanup and Handle literal \n
        text = text.replace("\\n", "\n")

        # 2. Table-to-List Transformation
        lines = text.split('\n')
        processed_lines = []
        in_table = False
        
        emojis = {
            "distance": "📍", "distancia": "📍",
            "hr": "❤️", "bpm": "❤️", "frecuencia": "❤️",
            "pace": "⏱️", "ritmo": "⏱️",
            "power": "⚡", "potencia": "⚡",
            "time": "🕒", "tiempo": "🕒", "duración": "🕒",
            "calories": "🔥", "calorías": "🔥",
            "vo2": "📈", "sleep": "😴", "sueño": "😴",
            "hrv": "⚖️"
        }

        for line in lines:
            stripped = line.strip()
            # Detect table line
            if stripped.startswith('|') and stripped.endswith('|'):
                parts = [p.strip() for p in stripped.split('|') if p.strip()]
                # Skip separators
                if not parts or all(re.match(r'[:\-]+', p) for p in parts):
                    continue
                
                if not in_table:
                    in_table = True
                    processed_lines.append("") # Spacing before table
                    # We skip the header row or treat it as normal row? 
                    # Let's treat it as header and skip it to follow previous implementation
                    continue
                
                if len(parts) >= 2:
                    metric_name = parts[0]
                    value = " | ".join(parts[1:])
                    icon = ""
                    for e_key, emoji in emojis.items():
                        if e_key in metric_name.lower():
                            icon = emoji + " "
                            break
                    processed_lines.append(f"{icon}*{metric_name}:* {value}")
                else:
                    processed_lines.append(f"• {parts[0]}")
            else:
                if in_table:
                    in_table = False
                    processed_lines.append("") # Spacing after table
                processed_lines.append(line)
        
        text = '\n'.join(processed_lines)

        # 3. Structural Headers & Spacing
        # Convert ### to Bold and ensure spacing
        text = re.sub(r'^###\s+(.*)$', r'\n\n*\1*\n', text, flags=re.MULTILINE)
        
        # Spacing for common structural emojis
        structural_markers = [r'🔹', r'⚠️', r'✅', r'📅', r'🔔', r'🏃', r'🔋', r'💪', r'🧘‍♂️', r'🎯']
        for marker in structural_markers:
            # Ensure it starts on a new line if preceded by text on the same line
            text = re.sub(rf'([^\n])\s*({marker})', r'\1\n\n\2', text)
            # Ensure space after the emoji
            text = re.sub(rf'({marker})([^\s])', r'\1 \2', text)

        # 4. MarkdownV2 Escaping
        reserved_chars = r"_*[]()~`>#+-=|{}.!"
        
        def escape(t):
            # Escape backslashes first
            t = t.replace('\\', '\\\\')
            # Escape all reserved characters
            return re.sub(f'([{re.escape(reserved_chars)}])', r'\\\1', t)

        text = escape(text)

        # 5. Restoration of formatting entities
        # Bold: restore **text** and *text* to *text*
        # Use DOTALL to allow bolding across lines, though Telegram is strict about it.
        # We also need to be careful not to match the escaped backslashes incorrectly.
        
        # First, handle double star bold
        text = re.sub(r'\\(\*\*|__)(?!\s)(.+?)(?<!\s)\\\1', r'*\2*', text, flags=re.DOTALL)
        
        # Then, handle single star/underscore bold/italic
        # For Telegram MarkdownV2, * is bold, _ is italic.
        text = re.sub(r'\\(\*)(?!\s)(.+?)(?<!\s)\\\1', r'*\2*', text, flags=re.DOTALL)
        text = re.sub(r'\\(_)(?!\s)(.+?)(?<!\s)\\_', r'_\2_', text, flags=re.DOTALL)

        # 6. Final cleanup: Max 2 newlines
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()

sample = """Hola, *fsirio*. 

| Metric | Value |
| :--- | :--- |
| Heart Rate | 70 bpm |
| VO2 Max | 48 |

Como tu Head Coach..."""

print(MessageProcessor.decode(sample))
