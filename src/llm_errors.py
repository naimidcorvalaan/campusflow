"""Provider-neutral, content-free failures at the model boundary."""
class LLMClientError(RuntimeError):
    pass


class LLMConfigurationError(LLMClientError):
    pass


class VisionUnavailable(LLMClientError):
    """No image evidence was obtained; callers must not invent recognition."""
