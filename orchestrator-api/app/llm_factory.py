import os


def get_chat_model(model_name: str, temperature: float = 0):
    """
    Modular factory to switch between Google AI Studio and the local Gemini CLI Proxy.
    """
    provider = os.getenv("LLM_PROVIDER", "google").lower()

    if provider == "proxy":
        try:
            from langchain_openai import ChatOpenAI
            from pydantic import SecretStr

            proxy_url = os.getenv("LLM_PROXY_URL", "http://host.docker.internal:8000/v1")
            return ChatOpenAI(
                model=model_name,
                base_url=proxy_url,
                api_key=SecretStr(os.getenv("OPENAI_API_KEY") or "none"),
                temperature=temperature,
            )
        except ImportError:
            # Fallback to Google if proxy dependencies are missing to prevent crash loop
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(
                model=model_name, google_api_key=os.getenv("GOOGLE_API_KEY"), temperature=temperature
            )
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model_name, google_api_key=os.getenv("GOOGLE_API_KEY"), temperature=temperature)
