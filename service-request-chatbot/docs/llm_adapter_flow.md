# LLM Adapter Flow Analysis

This document outlines the architecture and request flow of the multi-provider LLM adapter system within the Cenomi Service Request Chatbot. The system is designed to dynamically adapt and instantiate the appropriate LLM provider based on environment variables.

## Overview

The application utilizes a registry and gateway pattern to abstract away the specific details of individual LLM providers (e.g., OpenAI, Azure OpenAI, Google Vertex AI). This allows the rest of the application to interact with a unified interface (`LLMProviderProtocol`), while the underlying implementation can be swapped effortlessly by changing a single environment variable (`LLM_PROVIDER`).

## Core Components

### 1. `base.py` (The Protocol)
- **`LLMResult`**: A standardized dataclass that normalizes the response from any LLM provider, ensuring the rest of the app always receives the same response structure (content, parsed JSON, token usage, latency, etc.).
- **`LLMProviderProtocol`**: The abstract protocol that all concrete provider adapters must satisfy. It defines the core `generate()` method and properties like `model` and `provider_name`.
- **`BaseLLMGateway`**: An abstract base class that implements standard convenience wrappers (like `complete_json()`) around the core `generate()` method, reducing boilerplate across different providers.

### 2. `registry.py` (The Factory)
- **`LLMProviderRegistry`**: The central registry that maps provider names (e.g., `'openai'`, `'azure_openai'`) to their respective factory functions.
- It acts as a singleton manager, caching instantiated provider adapters in `_instances` so that only one instance of a provider adapter is created during the application's lifecycle.
- **Built-in Registration**: It automatically registers factories for OpenAI, Azure OpenAI, and Vertex AI upon module import. Crucially, the actual provider modules (e.g., `openai_provider.py`) are only imported *lazily* when the factory is invoked, keeping the startup fast and dependency footprint minimal if a provider isn't used.

### 3. `gateway.py` (The Dispatcher)
- **`LLMGateway.from_settings()`**: This is the crucial bridge to the environment variables. It reads the application settings (`settings.llm_provider`, which typically comes from the `LLM_PROVIDER` env variable) and asks the `LLMProviderRegistry` for the corresponding provider instance.
- **`get_default_gateway()`**: A singleton helper function used throughout the application to fetch the active, fully-configured LLM adapter without needing to know which specific provider is active.

## Flow: How it Adapts Based on Env Variables

When the application needs to interact with an LLM, the following sequence occurs:

1. **Environment Configuration**: The application starts up, and the configuration system (`app.core.config.settings`) loads the `LLM_PROVIDER` environment variable (e.g., set to `"azure_openai"`).
2. **Gateway Request**: A component calls `get_default_gateway()` in `gateway.py`.
3. **Settings Resolution**: `LLMGateway.from_settings()` is triggered, reading `settings.llm_provider`.
4. **Registry Lookup**: `LLMProviderRegistry.get_provider("azure_openai")` is invoked.
5. **Lazy Instantiation**: 
    - The registry looks up the factory function for `"azure_openai"`.
    - The factory is executed, which *now* imports `AzureOpenAIGateway` from `azure_openai_provider.py`.
    - `AzureOpenAIGateway.from_settings()` is called, which reads further provider-specific environment variables (like `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`).
6. **Singleton Caching**: The newly created `AzureOpenAIGateway` instance is cached in the registry.
7. **Unified Execution**: The caller receives an object satisfying `LLMProviderProtocol` and can call `.generate()` without knowing it's specifically Azure OpenAI under the hood.

> [!TIP]
> This pattern heavily adheres to the Open-Closed Principle (OCP) and Dependency Inversion Principle (DIP). To add a new provider (e.g., Anthropic), you simply write a new adapter class, register it in `registry.py`, and update the `.env` file—without modifying any core application logic.
