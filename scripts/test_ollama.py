import os

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, SecretStr


class TestIntent(BaseModel):
    intent: str = Field(description="The intent")
    rationale: str = Field(description="The reason")


llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-v4-flash:0731"),
    base_url=os.getenv("OLLAMA_BASE_URL", "https://ollama.com/v1"),
    api_key=SecretStr(os.getenv("OLLAMA_API_KEY", "")),
    temperature=0,
)
print("1. Standard invoke:")
res = llm.invoke("Hola, responde 'Conexion OK'")
print("Response:", res.content)

print("2. Structured output:")
structured = llm.with_structured_output(TestIntent, method="function_calling")
res_struct = structured.invoke("Cual es mi ritmo cardiaco?")
print("Structured:", res_struct)
