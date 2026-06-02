import os


def get_chat_model(model_name: str, temperature: float = 0):
    """
    Modular factory to switch between Google AI Studio and the local Gemini CLI Proxy.
    """
    provider = os.getenv("LLM_PROVIDER", "google").lower()

    if provider == "proxy":
        from langchain_openai import ChatOpenAI

        proxy_url = os.getenv("LLM_PROXY_URL", "http://host.docker.internal:8000/v1")
        return ChatOpenAI(
            model=model_name,
            openai_api_base=proxy_url,
            openai_api_key=os.getenv("OPENAI_API_KEY", "none"),
            temperature=temperature,
        )
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model_name, google_api_key=os.getenv("GOOGLE_API_KEY"), temperature=temperature)
