"""Capa del LLM implementada con el patrón Adapter.

El resto del programa (agente.py y tools.py) conoce solo la interfaz
`AdaptadorLLM`; nunca importa el SDK de un proveedor. Cada proveedor tiene su
adaptador, que traduce esa interfaz a su cliente concreto. Para cambiar de
modelo o de proveedor alcanza con editar el .env (MODEL_PROVIDER / MODEL_NAME)
o registrar un adaptador nuevo en ADAPTADORES: el agente y las tools no cambian.

Lo mismo para los embeddings del RAG (`AdaptadorEmbeddings`).
"""
import os
from abc import ABC, abstractmethod

from dotenv import load_dotenv
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

load_dotenv()

# Los tiers gratuitos devuelven 429 con frecuencia. Los clientes de LangChain ya
# reintentan con espera exponencial (max_retries): no hace falta middleware.
REINTENTOS = int(os.getenv("LLM_MAX_RETRIES", "6"))
TIMEOUT = int(os.getenv("LLM_TIMEOUT", "180"))
# Los modelos de razonamiento entran en bucles repetitivos con temperatura 0;
# los proveedores recomiendan ~0.6. El tope de salida corta un bucle a tiempo.
TEMPERATURA_AGENTE = float(os.getenv("LLM_TEMPERATURE", "0.6"))

# Esfuerzo de razonamiento: (tokens para pensar en OpenRouter y Gemini, tope de salida total). El tope incluye
# el razonamiento, así que deja ~7-8k libres para la respuesta (un banco en JSON entra).
# bajo: elegir tools y armar bancos simples. medio: varios temas o leer archivos largos.
# alto: pedidos grandes que cruzan bibliografía, campus e internet.
ESFUERZOS = {"bajo": (100, 8000), "medio": (200, 12000), "alto": (500, 16000)}


NIVELES_GROQ = {"bajo": "low", "medio": "medium", "alto": "high"}


def esfuerzo() -> tuple[int, int]:
    return ESFUERZOS.get(os.getenv("LLM_EFFORT", "bajo"), ESFUERZOS["bajo"])


def _requerir(variable: str) -> str:
    valor = os.getenv(variable)
    if not valor or valor.startswith("tu_"):
        raise RuntimeError(f"Falta {variable}. Cargala en la sección 🔑 Claves o en el .env.")
    return valor


# --- Interfaz objetivo (Target) ----------------------------------------------

class AdaptadorLLM(ABC):
    """Interfaz común que usan el agente y las tools, independiente del proveedor."""

    proveedor: str = ""

    def __init__(self, modelo: str):
        self.modelo = modelo

    @abstractmethod
    def modelo_chat(self, temperatura: float = TEMPERATURA_AGENTE) -> BaseChatModel:
        """Modelo de chat con soporte de tool calling, para `create_agent`."""

    def completar(self, sistema: str, usuario: str, temperatura: float = 0.6) -> str:
        """Una única llamada sistema + usuario -> texto. La usa la tool generadora."""
        respuesta = self.modelo_chat(temperatura).invoke(
            [SystemMessage(sistema), HumanMessage(usuario)]
        )
        return respuesta.text

    def __repr__(self) -> str:
        return f"{self.proveedor}:{self.modelo}"


# --- Adaptadores concretos -----------------------------------------------------

def _chat_openai_con_razonamiento():
    """ChatOpenAI que conserva el razonamiento del modelo.

    Groq (y LM Studio) devuelven el razonamiento en `message.reasoning` /
    `message.reasoning_content`, campos que no son de la API de OpenAI y que
    ChatOpenAI descarta. Esta subclase los copia a `additional_kwargs`, de donde
    los lee agente.py para mostrarlos en la UI.
    """
    from langchain_openai import ChatOpenAI

    class ChatOpenAIConRazonamiento(ChatOpenAI):
        def _create_chat_result(self, response, generation_info=None):
            result = super()._create_chat_result(response, generation_info)
            datos = response if isinstance(response, dict) else response.model_dump()
            for generacion, choice in zip(result.generations, datos.get("choices", [])):
                mensaje = choice.get("message") or {}
                razonamiento = mensaje.get("reasoning") or mensaje.get("reasoning_content")
                if razonamiento:
                    generacion.message.additional_kwargs["reasoning_content"] = razonamiento
                if mensaje.get("reasoning_details"):
                    generacion.message.additional_kwargs["reasoning_details"] = mensaje["reasoning_details"]
            return result

        def _get_request_payload(self, input_, *, stop=None, **kwargs):
            """Reenvía el razonamiento de cada respuesta anterior (`reasoning_details`).

            Sin esto, algunos modelos razonan solo en la primera llamada del turno y
            después eligen las tools "a ciegas". Si el proveedor no lo manda, no hace nada.
            """
            payload = super()._get_request_payload(input_, stop=stop, **kwargs)
            mensajes = self._convert_input(input_).to_messages()
            for original, enviado in zip(mensajes, payload.get("messages", [])):
                detalles = original.additional_kwargs.get("reasoning_details")
                if original.type == "ai" and detalles:
                    enviado["reasoning_details"] = detalles
            return payload

    return ChatOpenAIConRazonamiento


class AdaptadorOpenRouter(AdaptadorLLM):
    """OpenRouter expone una API compatible con OpenAI: se adapta con ChatOpenAI."""

    proveedor = "openrouter"
    URL = "https://openrouter.ai/api/v1"

    def __init__(self, modelo: str):
        super().__init__(modelo)
        self._api_key = _requerir("OPENROUTER_API_KEY")
        # Respaldo opcional (MODEL_FALLBACKS, separados por coma): si el modelo da 429,
        # OpenRouter prueba los siguientes en el mismo request. Vacío = solo el elegido.
        respaldo = os.getenv("MODEL_FALLBACKS", "")
        self._respaldo = [m.strip() for m in respaldo.split(",") if m.strip() and m.strip() != modelo]

    def modelo_chat(self, temperatura: float = TEMPERATURA_AGENTE) -> BaseChatModel:
        return _chat_openai_con_razonamiento()(
            model=self.modelo,
            base_url=self.URL,
            api_key=self._api_key,
            temperature=temperatura,
            max_retries=REINTENTOS,
            timeout=TIMEOUT,
            max_tokens=esfuerzo()[1],
            extra_body={
                # Presupuesto de razonamiento según LLM_EFFORT. A los modelos que solo
                # aceptan effort, OpenRouter se lo traduce desde estos tokens.
                "reasoning": {"enabled": True, "max_tokens": esfuerzo()[0]},
                "models": [self.modelo, *self._respaldo],
            },
            # Encabezado opcional que OpenRouter usa para identificar la app.
            default_headers={"X-Title": "Generador de practica estud.ia - UTN FRM"},
        )


class AdaptadorGroq(AdaptadorLLM):
    """Groq expone una API compatible con OpenAI: se adapta con ChatOpenAI."""

    proveedor = "groq"
    URL = "https://api.groq.com/openai/v1"

    def __init__(self, modelo: str):
        super().__init__(modelo)
        self._api_key = _requerir("GROQ_API_KEY")

    def modelo_chat(self, temperatura: float = TEMPERATURA_AGENTE) -> BaseChatModel:
        # A diferencia de OpenRouter, Groq no acepta un presupuesto en tokens: el nivel va como reasoning_effort.
        razonamiento = {"reasoning_effort": NIVELES_GROQ.get(os.getenv("LLM_EFFORT", "bajo"), "low")}
        if not self.modelo.startswith("openai/gpt-oss"):
            # Los GPT-OSS devuelven `reasoning` solos; el resto lo necesita en "parsed"
            # (con tools, "raw" da 400 y "hidden" no lo devuelve).
            razonamiento["reasoning_format"] = "parsed"
        return _chat_openai_con_razonamiento()(
            model=self.modelo,
            base_url=self.URL,
            api_key=self._api_key,
            temperature=temperatura,
            max_retries=REINTENTOS,
            timeout=TIMEOUT,
            max_tokens=esfuerzo()[1],
            extra_body=razonamiento,
        )


class AdaptadorGemini(AdaptadorLLM):
    """Google Gemini (tier gratuito de AI Studio). Devuelve su razonamiento."""

    proveedor = "google_genai"

    def __init__(self, modelo: str):
        super().__init__(modelo)
        _requerir("GOOGLE_API_KEY")

    def modelo_chat(self, temperatura: float = TEMPERATURA_AGENTE) -> BaseChatModel:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=self.modelo,
            temperature=temperatura,
            max_retries=REINTENTOS,
            include_thoughts=True,
            # Gemma razona siempre y rechaza el presupuesto: ahí el nivel solo cambia el tope.
            thinking_budget=None if self.modelo.startswith("gemma") else esfuerzo()[0],
            max_output_tokens=esfuerzo()[1],
        )


class AdaptadorLMStudio(AdaptadorLLM):
    """Modelo local servido por LM Studio (API compatible con OpenAI)."""

    proveedor = "lmstudio"

    def modelo_chat(self, temperatura: float = TEMPERATURA_AGENTE) -> BaseChatModel:
        return _chat_openai_con_razonamiento()(
            model=self.modelo,
            base_url=os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
            api_key="lm-studio",   # el cliente la exige, LM Studio la ignora
            temperature=temperatura,
            max_tokens=esfuerzo()[1],   # ponytail: sin presupuesto de razonamiento, el modelo local no lo expone igual
            timeout=TIMEOUT,
        )


ADAPTADORES: dict[str, type[AdaptadorLLM]] = {
    AdaptadorOpenRouter.proveedor: AdaptadorOpenRouter,
    AdaptadorGroq.proveedor: AdaptadorGroq,
    AdaptadorGemini.proveedor: AdaptadorGemini,
    AdaptadorLMStudio.proveedor: AdaptadorLMStudio,
}

MODELO_POR_DEFECTO = "nvidia/nemotron-3.5-super-vl:free"


def obtener_adaptador() -> AdaptadorLLM:
    """Fábrica: elige el adaptador según el .env."""
    proveedor = os.getenv("MODEL_PROVIDER", "openrouter")
    modelo = os.getenv("MODEL_NAME", MODELO_POR_DEFECTO)
    if proveedor not in ADAPTADORES:
        raise RuntimeError(
            f"MODEL_PROVIDER «{proveedor}» no soportado. Opciones: {', '.join(ADAPTADORES)}."
        )
    return ADAPTADORES[proveedor](modelo)


# --- Embeddings ------------------------------------------------------------------

class AdaptadorEmbeddings(ABC):
    """Interfaz común para el modelo de embeddings del RAG."""

    @abstractmethod
    def embeddings(self) -> Embeddings:
        """Objeto con embed_documents / embed_query."""


class AdaptadorEmbeddingsGemini(AdaptadorEmbeddings):
    """gemini-embedding-001, gratis con el tier de Google AI Studio."""

    def __init__(self, modelo: str = "gemini-embedding-001"):
        _requerir("GOOGLE_API_KEY")
        self.modelo = modelo

    def embeddings(self) -> Embeddings:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(model=self.modelo)


def obtener_embeddings() -> Embeddings:
    modelo = os.getenv("EMBEDDINGS_MODEL", "gemini-embedding-001")
    return AdaptadorEmbeddingsGemini(modelo).embeddings()
