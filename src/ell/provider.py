from abc import ABC, abstractmethod
from collections import defaultdict
from functools import lru_cache
import inspect
from types import MappingProxyType
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple, Type, TypedDict, Union

from pydantic import BaseModel, ConfigDict, Field
from ell.types import Message, ContentBlock, ToolCall
from ell.types._lstr import _lstr
import json
from dataclasses import dataclass
from ell.types.message import LMP


# XXX: Might leave this internal to providers so that the complex code is simpler & 
# we can literally jsut call provider.call like any openai fn.
class EllCallParams(BaseModel):
    model: str = Field(..., description="Model identifier")
    messages: List[Message] = Field(..., description="Conversation context")
    client: Any = Field(..., description="API client")
    tools: Optional[List[LMP]] = Field(None, description="Available tools")
    api_params: Dict[str, Any] = Field(default_factory=dict, description="API parameters")


    model_config = ConfigDict(arbitrary_types_allowed=True)


class Metadata(TypedDict):
    """First class metadata so that ell studio can work, you can add more stuff here if you want"""
    
#XXX: Needs a better name.
class Provider(ABC):
    """
    Abstract base class for all providers. Providers are API interfaces to language models, not necessarily API providers.
    For example, the OpenAI provider is an API interface to OpenAI's API but also to Ollama and Azure OpenAI.
    In Ell. We hate abstractions. The only reason this exists is to force implementers to implement their own provider correctly -_-.
    """

    ################################
    ### API PARAMETERS #############
    ################################
    @abstractmethod
    def provider_call_function(self, api_call_params : Optional[Dict[str, Any]] = None) -> Callable[..., Any]:
        """
        Implement this method to return the function that makes the API call to the language model.
        For example, if you're implementing the OpenAI provider, you would return the function that makes the API call to OpenAI's API.
        """
        return NotImplemented
        

    def disallowed_api_params(self) -> FrozenSet[str]:
        """
        Returns a list of disallowed call params that ell will override.
        """
        return frozenset({"messages", "tools", "model"})

    def available_api_params(self, api_params : Optional[Dict[str, Any]] = None):
        params = _call_params(self.provider_call_function(api_params))
        return frozenset(params.keys()) - self.disallowed_api_params()


    ################################
    ### TRANSLATION ###############
    ################################
    @abstractmethod
    def translate_to_provider(self, ell_call : EllCallParams) -> Dict[str, Any]:
        """Converts an ell call to provider call params!"""
        return NotImplemented
    
    @abstractmethod
    def translate_from_provider(self, provider_response : Any, ell_call : EllCallParams, origin_id : Optional[str] = None, logger : Optional[Callable[[str], None]] = None) -> Tuple[List[Message], Metadata]:
        """Converts provider responses to universal format."""
        return NotImplemented

    ################################
    ### CALL MODEL ################
    ################################
    # Be careful to override this method in your provider.
    def call(self, ell_call : EllCallParams, origin_id : Optional[str] = None, logger : Optional[Any] = None) -> Tuple[List[Message], Dict[str, Any], Metadata]:
        # Automatic validation of params
        assert ell_call.api_params.keys() not in self.disallowed_api_params(), f"Disallowed parameters: {ell_call.api_params}"

        # Call
       
        
        final_api_call_params = self.translate_to_provider(ell_call)
        call = self.provider_call_function(final_api_call_params)
        _validate_provider_call_params(final_api_call_params, call)

        provider_resp = call(final_api_call_params)(**final_api_call_params)

        messages, metadata = self.translate_from_provider(provider_resp, ell_call, origin_id, logger)
        _validate_messages_are_tracked(messages, origin_id)
        
        # TODO: Validate messages are tracked.
        return messages, final_api_call_params, metadata


        


# handhold the the implementer, in production mode we can turn these off for speed.
@lru_cache(maxsize=None)
def _call_params(call : Callable[..., Any]) -> MappingProxyType[str, inspect.Parameter]:
    return inspect.signature(call).parameters

def _validate_provider_call_params(api_call_params: Dict[str, Any], call : Callable[..., Any]):
    provider_call_params = _call_params(call)
    
    required_params = {
        name: param for name, param in provider_call_params.items()
        if param.default == param.empty and param.kind != param.VAR_KEYWORD
    }
    
    for param_name in required_params:
        assert param_name in api_call_params, f"Provider implementation error: Required parameter '{param_name}' is missing in the converted call parameters converted from ell call."
    
    for param_name, param_value in api_call_params.items():
        assert param_name in provider_call_params, f"Provider implementation error: Unexpected parameter '{param_name}' in the converted call parameters."
        
        param_type = provider_call_params[param_name].annotation
        if param_type != inspect.Parameter.empty:
            assert isinstance(param_value, param_type), f"Provider implementation error: Parameter '{param_name}' should be of type {param_type}."
    

def _validate_messages_are_tracked(messages: List[Message], origin_id: Optional[str] = None):
    if origin_id is None: return
    
    for message in messages:
        assert isinstance(message.text, _lstr), f"Provider implementation error: Message text should be an instance of _lstr, got {type(message.text)}"
        assert message.text._or == origin_id, f"Provider implementation error: Message origin_id {message.text.origin_id} does not match the provided origin_id {origin_id}"


