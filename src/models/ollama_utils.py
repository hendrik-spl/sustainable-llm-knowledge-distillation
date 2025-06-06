import os
import time
import ollama
import weave
from ollama import chat, ChatResponse
from src.models.model_mapping import model_mapping
from src.models.query_utils import get_query_params, clean_llm_output, find_majority
from src.data.data_manager import get_samples

weave.init("model-inference-v2")

def track_samples_ollama(model, dataset_name):
    sample_prompts = get_samples(dataset_name)
    query_params = get_query_params(dataset_name)
    
    responses = []
    for sample_prompt in sample_prompts:
        responses.append(track_sample_ollama(model, sample_prompt, query_params))
    
    return responses

@weave.op()
def track_sample_ollama(model, prompt, query_params):
    response = query_ollama_model(model, prompt, query_params)
    return response

def query_ollama_sc(model, prompt, dataset_name, verbose=False):
    query_params = get_query_params(dataset_name)
    
    response = query_ollama_model(model, prompt, query_params)
    cleaned_response = clean_llm_output(dataset_name, response)
    if verbose:
        print(f"Response: {response}")
        print(f"Cleaned Response: {cleaned_response}")
        print(f"-----------")
    
    return cleaned_response

def query_ollama_model(model, prompt, params):
    """
    Sends a chat request to the Ollama API with the given model and prompt using the Ollama SDK.

    Args:
        model (str): The name of the model to use. For example, "llama3.2:1b".
        prompt (str): The prompt to send to the model.
        temperature (float, optional): The temperature to use for sampling. Defaults to 0.1.
        seed (int, optional): The seed for reproducibility. Defaults to 42.
        max_retries (int, optional): Maximum number of retries on failure. Defaults to 3.
        retry_delay (int, optional): Delay between retries in seconds. Defaults to 5.

    Returns:
        str or None: The response content if the request was successful, None otherwise.
    """
    model_name = model_mapping.get(model, {}).get("ollama", model)

    messages = [{"role": "user", "content": prompt}]
    options = {
        "temperature": params.get("temperature"),
        "seed": params.get("seed"),
        "top_p": params.get("top_p"),
        "top_k": params.get("top_k"),
        "num_predict": params.get("max_new_tokens"),
        }
    
    if params.get("max_context_length") is not None:
        options["num_ctx"] = params.get("max_context_length")

    max_retries = params.get("custom_max_retries")
    retry_delay = params.get("custom_retry_delay")
    
    for attempt in range(max_retries):
        try:
            response: ChatResponse = chat(
                model=model_name,
                messages=messages,
                options=options
            )
            return response.message.content
        except Exception as e:
            print(f"Error in Ollama request (attempt {attempt+1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                print("Failed to get response from Ollama after multiple attempts.")
                return None
            time.sleep(retry_delay)

def use_ollama(model_name: str) -> bool:
    """
    Check if the model is an Ollama model.

    Args:
        model_name: The name of the model to check.

    Returns:
        bool: True if the model is an Ollama model, False otherwise.
    """
    if os.path.exists(model_name):
        return False
    else:
        return True

def check_if_ollama_model_exists(model_name):
    """
    Checks if a model exists in the Ollama API.

    Args:
        model_name (str): The name of the model to check.

    Returns:
        bool: True if the model exists, False otherwise.
    """
    # Try to get the value from the mapping, otherwise continue with original model_name
    model_name = model_mapping.get(model_name, {}).get("ollama", model_name)
    try:
        list = ollama.list()
        if len(list.models) == 0:
            print("No ollama models available yet. Pulling...")
            pull_model_from_ollama(model_name)
            return False
        for model in list:
            if model[1][0].model == model_name:
                return True
            else:
                print(f"Model {model_name} not found in Ollama. Attempting to pull model.")
                pull_model_from_ollama(model_name)
                return False
    except Exception as e:
        print(f"Failed to check if model {model_name} exists: {e}")
        return False

def pull_model_from_ollama(model_name):
    """
    Pulls a model from the Ollama API.

    Args:
        model_name (str): The name of the model to pull.
    """
    try:
        ollama.pull(model_name)
        print(f"Model {model_name} pulled successfully.")
    except Exception as e:
        print(f"Failed to pull model {model_name}: {e}")