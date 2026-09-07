import os


def get_chat_model(model_name: str, temperature: float = 0, **kwargs):
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
                **kwargs,
            )
        except ImportError:
            # Fallback to Google if proxy dependencies are missing to prevent crash loop
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=model_name, google_api_key=os.getenv("GOOGLE_API_KEY"), temperature=temperature, **kwargs
            )
    elif provider == "lmstudio":
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr

        lm_studio_url = os.getenv("LM_STUDIO_BASE_URL", "http://192.168.88.240:1234/v1")
        # Added defensive defaults for local models
        return ChatOpenAI(
            model=model_name,
            base_url=lm_studio_url,
            api_key=SecretStr("not-needed"),
            temperature=temperature,
            max_tokens=kwargs.get("max_tokens", 1024),
            timeout=kwargs.get("timeout", 60),
            **{k: v for k, v in kwargs.items() if k not in ["max_tokens", "timeout"]},
        )
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr

        return ChatOpenAI(
            model=model_name,
            api_key=SecretStr(os.getenv("OPENAI_API_KEY") or "none"),
            temperature=temperature,
            **kwargs,
        )
    elif provider == "ollama":
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr

        base_url = os.getenv("OLLAMA_BASE_URL", "https://ollama.com/v1")
        api_key = os.getenv("OLLAMA_API_KEY") or os.getenv("OPENAI_API_KEY") or "ollama"
        return ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=SecretStr(api_key),
            temperature=temperature,
            timeout=kwargs.get("timeout", 120),
            **{k: v for k, v in kwargs.items() if k not in ["timeout"]},
        )

    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=model_name, google_api_key=os.getenv("GOOGLE_API_KEY"), temperature=temperature, **kwargs
    )
