from abc import ABC, abstractmethod
from autosub.subtitles.models import LLMResponse

class ProviderError(Exception):
    pass

class RateLimitError(ProviderError):
    pass

class ContextOverflowError(ProviderError):
    pass

class ProviderUnavailableError(ProviderError):
    pass

class BaseLLMProvider(ABC):
    @abstractmethod
    def generate_completion(
        self, prompt: str, system_instruction: str = "", timeout: float = 180.0, temperature: float = 0.3
    ) -> LLMResponse:
        pass

    @abstractmethod
    def is_available(self) -> bool:
        pass
