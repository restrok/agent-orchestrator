from main import MessageProcessor

def test_user_sample_formatting():
    sample_text = """Hola, *fsirio*. Como tu Head Coach, he realizado una *extracción y síntesis total de tu base de datos biométricos*. No es un resumen superficial, sino una auditoría técnica de tu estado fisiológico, mecánico y metabólico actual.

Este es tu *Reporte Maestro de Rendimiento*.

---

*1. 🫀 Dominio Cardiovascular y Autonómico (El "Motor")*
Tu sistema cardiovascular es potente y muestra una capacidad de recuperación muy eficiente.

*   *Capacidad Aeróbica (VO2 Max):* Te sitúas en un rango de *46-48*, lo cual es una base sólida y competitiva para tu objetivo de los 10k.
*   *Estabilidad Aeróbica (Z2):* Tienes un marcador de estabilidad excepcional. Hemos registrado un *desacoplamiento aeróbico < 1%* a frecuencias cardíacas de hasta 158 bpm. Esto significa que tu corazón es capaz de mantener un esfuerzo constante sin derivar hacia el estrés anaeróbico prematuramente.
*   *Resiliencia del SNA (Sistema Nervioso Autónomo):* Posees un *Índice de Resiliencia ANS de 1.0*. Este es un dato clave: tu HRV se mantiene "Balanceada" (37ms) incluso cuando tu *Body Battery* cae a niveles críticos (21-34). Tienes una capacidad innata para rendir a pesar de una recuperación energética incompleta.
*   *RHR (Frecuencia Cardíaca en Reposo):* Estable en *58 bpm*, lo que confirma que no hay signos de sobreentrenamiento ni estrés sistémico agudo.

*2. 🛡️ Análisis de Carga y Biomecánica (El "Chasis")*
Tu estructura mecánica está absorbiendo la carga actual con una eficiencia notable.

*   *Gestión de Carga (Ratio A:C):* Tu ratio Agudo:Crónico actual es de *0.72*, mientras que tu "Línea Roja" personal (límite de riesgo) está en *1.45*. Estás operando al *50% de tu límite de riesgo*, lo que nos deja un margen amplio para incrementar la intensidad en el próximo bloque sin peligro de lesión.
*   *Escaneo Biomecánico (Timeseries):*
    *   *GCT (Tiempo de Contacto con el Suelo):* Estable ($\approx$ 301-312ms). No hay "drift" o deriva, lo que indica que tu técnica no se degrada por fatiga durante la sesión.
    *   *Ratio Vertical:* Consistente ($\approx$ 9.4% - 10.3%), lo que confirma que no hay pérdida de eficiencia mecánica (no hay "rebote" excesivo).

*3. 💤 Arquitectura del Sueño y Recuperación (La "Reparación")*
Aquí es donde identificamos la principal oportunidad de optimización.

*   *Calidad vs. Cantidad:* Tu calidad de sueño es alta (77/100) y el sueño profundo es excelente (*1.43h*), asegurando la reparación física de los tejidos.
*   *Déficit de Volumen:* Tu duración total es corta ($\approx$ 6.5h) and el sueño REM está ligeramente por debajo del umbral óptimo (*18.5% vs 20%*).
*   *Impacto:* Aunque tu corazón está recuperado, la falta de volumen de sueño y REM puede afectar tu agudeza mental y la recuperación del sistema nervioso central a largo plazo.

*4. ⚖️ Perfil Metabólico y Nutricional (El "Combustible")*
Tu fisiología dicta reglas estrictas para evitar la pérdida de rendimiento.

*   *Fenotipo:* *Ectomorfo con corazón de "altas revoluciones"*.
*   *Dinámica de Esfuerzo (Late Steady State):* Hemos observado que alcanzas tu máxima eficiencia mecánica *después del kilómetro 8*. Esto significa que los primeros 8km de cualquier carrera son metabólicamente más "costosos" para ti que para otros corredores.
*   *Riesgo de Catabolismo:* Debido a tu complexión, tienes una tendencia alta a utilizar masa muscular como combustible si no hay glucógeno disponible.
*   *Protocolo Crítico:* Ventana de 30-60 min post-entreno $\rightarrow$ *Carbohidratos rápidos + Proteína*. Sin este pico de insulina, el riesgo de degradación muscular aumenta significativamente.

---

*🎯 Proyección hacia el Objetivo: 10k en 50:00 (15 de Julio)*

*Análisis de Viabilidad:* 
Con tu estabilidad en Z2 y tu VO2 Max actual, el objetivo de 50:00 es *totalmente viable*. Tienes la base aeróbica; ahora necesitamos trabajar la potencia específica del ritmo de carrera.

*Plan de Acción Maestro:*
1.  *Entrenamiento:* Introducir bloques de *Intervalos de Umbral (Threshold)*. Tu cuerpo está listo para el estrés de alta intensidad.
2.  *Estrategia de Carrera:* Dado tu *Late Steady State*, debemos planificar un inicio controlado en los primeros 8km para no agotar el glucógeno prematuramente y poder "volar" en los últimos 2km.
3.  *Optimización Biológica:* 
    *   *Sueño:* Intentar subir a 7.5h para cerrar la brecha de REM.
    *   *Nutrición:* Superávit calórico agresivo los días de intensidad.

*Veredicto Final:* 
Estás en un estado físico *SÓLIDO y SEGURO*. Tienes un motor eficiente y una estructura resistente. Si ajustamos el volumen de sueño y somos estrictos con la nutrición post-entreno, llegarás al 15 de julio en tu pico de forma.

**Estado:

✅ OPTIMIZADO PARA EL SIGUIENTE NIVEL.**"""

    decoded = MessageProcessor.decode(sample_text)
    
    # Check that backslashes followed by reserved chars are present (for Telegram's parser)
    # but that our intended formatting (bold/italic) is clean.
    
    # Telegram MarkdownV2 expects dots, hyphens, etc to be escaped: \. \-
    assert r"\." in decoded
    assert r"\-" in decoded
    assert r"\(" in decoded
    
    # But our restoration logic should have converted **text** to *text* (Telegram bold)
    # The sample uses *text* which is Telegram italic.
    # Actually, the user sample uses *fsirio* which is italic in V2.
    
    # Let's see what the processor does with *text*
    # L103: text = re.sub(r'\\\_(.*?)\\\_', r'_\1_', text)
    # L101: text = re.sub(r'\\\*\\\*(.*?)\\\*\\\*', r'*\1*', text)
    
    # If the input has *text*, it gets escaped to \*text\* then it stays \*text\* unless we restore it.
    # Current restoration only handles ** (bold) and _ (italic).
    
    print("\n--- DECODED OUTPUT START ---")
    print(decoded)
    print("--- DECODED OUTPUT END ---\n")

if __name__ == "__main__":
    test_user_sample_formatting()
