import os
import torch
import weave
from tqdm import tqdm
from transformers import pipeline
from peft import LoraConfig, TaskType, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer
from transformers.generation.stopping_criteria import StoppingCriteriaList

from src.models.hf_stopping import KeywordStoppingCriteria
from src.prompts.sentiment import get_sentiment_prompt
from src.prompts.gold import get_gold_classification_prompt
from src.prompts.summary import get_summmary_prompt
from src.models.model_mapping import model_mapping
from src.models.query_utils import find_majority, clean_llm_output, get_query_params
from src.data.data_manager import get_samples

class HF_Manager:
    
    @weave.op()
    @staticmethod
    def predict(model_path, dataset, dataset_name, wandb_run=None, limit=5):
        params = get_query_params(dataset_name)
        # remove ['custom_max_retries', 'custom_retry_delay'] from params because they are not needed downstream and cause ValueErrors
        if "custom_max_retries" in params:
            params.pop("custom_max_retries", None)
        if "custom_retry_delay" in params:
            params.pop("custom_retry_delay", None)
        if "max_context_length" in params:
            params.pop("max_context_length", None)

        pipe = pipeline(
            model=model_path,
            task="text-generation",
            )

        print(f"Running test inference with model {model_path} on dataset {dataset.shape}. Limit: {limit}.")
        for i, example in tqdm(enumerate(dataset), total=min(limit, len(dataset))):
            if i >= limit:
                break
            if "sentiment" in dataset_name:
                sentence = example["sentence"]
                prompt = get_sentiment_prompt(sentence)
                prompt_separator = "Final Label: "
            elif "gold" in dataset_name:
                headline = example["News"]
                prompt = get_gold_classification_prompt(headline)
                prompt_separator = "FINAL CLASSIFICATION: "
            elif "summary" in dataset_name:
                text = example["text"]
                prompt = get_summmary_prompt(text)
                prompt_separator = "FINAL SUMMARY OF YOUR TEXT: "
            completion = pipe(prompt, **params)
            completion = completion[0]["generated_text"]
            label_pos = completion.find(prompt_separator)
            if label_pos == -1:
                print("Label not found in completion.")
                continue
            completion = completion[label_pos + len(prompt_separator):].strip()
            print(f"Example {i}:")
            print(f"Prompt: {prompt}")
            print(f"Completion by student: {completion}")
            print(f"-----------")
            if wandb_run:
                wandb_run.log({
                    "dataset_size": dataset.shape,
                    "sample": i,
                    "prompt": prompt,
                    "student_completion": completion
                    })
            
    @staticmethod 
    def query_hf_sc(model, tokenizer, dataset_name, prompt, verbose=False):
        query_params = get_query_params(dataset_name)
        
        response = HF_Manager.query_model(model, tokenizer, dataset_name, prompt, query_params)
        cleaned_response = clean_llm_output(dataset_name, response)
        if verbose:
            print(f"Response: {response}")
            print(f"Cleaned Response: {cleaned_response}")
            print(f"-----------")
        
        return cleaned_response

    @staticmethod
    def query_model(model, tokenizer, dataset_name, prompt, params):
        device = "cuda:0" if torch.cuda.is_available() else "cpu"

        # Tokenize the input prompt and move to the appropriate device
        inputs = tokenizer(prompt, return_tensors="pt")
        # Move inputs to the same device as the models first parameter
        if hasattr(model, "device"):
            device = model.device
        else:
            # For models distributed across multiple devices, get device of first parameter
            param_device = next(model.parameters()).device
            device = param_device
            
        inputs = {k: v.to(device) for k, v in inputs.items()}

        prompt_length = inputs["input_ids"].shape[1]

        # Create stopping criteria - stop on seeing these keywords or patterns
        if "sentiment" in dataset_name:
            stop_words = ["text:"]
            stopping_criteria = StoppingCriteriaList([
                KeywordStoppingCriteria(tokenizer, stop_words, prompt_length, max_tokens=3)
            ])
        elif "gold" in dataset_name:
            stop_words = ["end of classification", "}"]
            stopping_criteria = StoppingCriteriaList([
                KeywordStoppingCriteria(tokenizer, stop_words, prompt_length, max_tokens=None)
            ])
        elif "summary" in dataset_name:
            stop_words = ["please let me know if", "i hope it is correct"]
            stopping_criteria = StoppingCriteriaList([
                KeywordStoppingCriteria(tokenizer, stop_words, prompt_length, max_tokens=None)
            ])
        else:
            stopping_criteria = None

        # Generate a response
        with torch.no_grad():
            outputs = model.generate(**inputs,
                                    do_sample=params.get("do_sample"),
                                    temperature=params.get("temperature"),
                                    top_p=params.get("top_p"),
                                    top_k=params.get("top_k"),
                                    max_new_tokens=params.get("max_new_tokens"),
                                    # pad_token_id=tokenizer.eos_token_id, # previous implementation which caused issues. It doesn't match the setup in load_model
                                    pad_token_id=tokenizer.pad_token_id,
                                    eos_token_id=tokenizer.eos_token_id,
                                    stopping_criteria=stopping_criteria,
                                    )

        # Decode ONLY the generated tokens (exclude the input prompt tokens)
        response = tokenizer.decode(outputs[0][prompt_length:], skip_special_tokens=True)
        
        return response

    @staticmethod
    def load_model(model_name: str, peft: bool):
        # if model name is not a valid path
        if not os.path.exists(model_name):
            print(f"Model name {model_name} is not a valid path. Checking model mapping.")
            if model_name in model_mapping:
                model_name = model_mapping[model_name]["HF"]
                print(f"Model name {model_name} found in model mapping. Using Hugging Face model name.")
        else:
            print(f"Model name {model_name} is a valid path. Loading model from local path.")

        model = AutoModelForCausalLM.from_pretrained(model_name, 
                                                     device_map="auto" # very important for large models!
                                                     )
        tokenizer = AutoTokenizer.from_pretrained(model_name)

        if "opt" in model_name:
            print(f"Loading OPT model {model_name}. Note: OPT adds EOS token at prompt beginning.")
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token
                model.config.pad_token_id = model.config.eos_token_id

        elif tokenizer.pad_token is None:
            print(f"Tokenizer {tokenizer.name_or_path} does not have a pad token. Setting a unique pad token.")

            # Add a new special token as pad_token
            special_tokens_dict = {'pad_token': '[PAD]'}
            num_added_toks = tokenizer.add_special_tokens(special_tokens_dict)
            print(f"Added {num_added_toks} special tokens to the tokenizer")
            
            # Resize the model's token embeddings to account for the new pad token
            model.resize_token_embeddings(len(tokenizer))

            # Set the pad_token_id to the ID of the new pad token
            model.config.pad_token_id = tokenizer.pad_token_id

            print(f"tokenizer.pad_token: {tokenizer.pad_token}")
            print(f"model.config.pad_token_id: {model.config.pad_token_id}")
            print(f"model.config.eos_token_id: {model.config.eos_token_id}")

        if peft:
            peft_config = LoraConfig(
                task_type=TaskType.CAUSAL_LM,
                r=8,
                lora_alpha=16,
                lora_dropout=0.1,
                modules_to_save=["lm_head", "embed_tokens"],
            )
            print(f"Loaded model {model_name} with PEFT configuration.")
            model = get_peft_model(model, peft_config)
            model.print_trainable_parameters()
        
        return model, tokenizer

    @staticmethod
    def load_finetuned_adapter(model_path):
        """
        Load a fine-tuned PEFT/LoRA adapter model from a local path using AutoPeftModelForCausalLM.
        """
        from peft import AutoPeftModelForCausalLM
        from transformers import AutoTokenizer

        # Step 1: Load the tokenizer from the fine-tuned model
        tokenizer = AutoTokenizer.from_pretrained(model_path)

        # Step 2: Load the fine-tuned adapter model directly
        print(f"Loading fine-tuned adapter model from {model_path}")
        model = AutoPeftModelForCausalLM.from_pretrained(model_path, device_map="auto")

        if tokenizer.pad_token is None:
            print(f"Tokenizer {tokenizer.name_or_path} does not have a pad token. Setting a unique pad token.")

            # Add a new special token as pad_token
            special_tokens_dict = {'pad_token': '[PAD]'}
            num_added_toks = tokenizer.add_special_tokens(special_tokens_dict)
            print(f"Added {num_added_toks} special tokens to the tokenizer")
            
            # Resize the model's token embeddings to account for the new pad token
            model.resize_token_embeddings(len(tokenizer))

            # Set the pad_token_id to the ID of the new pad token
            model.config.pad_token_id = tokenizer.pad_token_id

            print(f"tokenizer.pad_token: {tokenizer.pad_token}")
            print(f"model.config.pad_token_id: {model.config.pad_token_id}")
            print(f"model.config.eos_token_id: {model.config.eos_token_id}")


        return model, tokenizer
    
    @staticmethod
    def track_samples_hf(model, tokenizer, dataset_name):
        weave.init("model-inference-v2")
        sample_prompts = get_samples(dataset_name)
        query_params = get_query_params(dataset_name)
        model_name = model.config._name_or_path # needed to see natural model name in wandb weave

        responses = []
        for sample_prompt in sample_prompts:
            responses.append(HF_Manager.track_sample_hf(model=model, tokenizer=tokenizer, dataset_name=dataset_name, model_name=model_name, prompt=sample_prompt, query_params=query_params))

        return responses
    
    @weave.op()
    @staticmethod
    def track_sample_hf(model, tokenizer, dataset_name, model_name, prompt, query_params):
        response = HF_Manager.query_model(model=model, tokenizer=tokenizer, dataset_name=dataset_name, prompt=prompt, params=query_params)
        return response