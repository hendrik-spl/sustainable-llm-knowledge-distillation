import re
import json
import random
from collections import Counter

from src.config.query_config import *

def get_query_params(dataset_name: str):
    if "sentiment" in dataset_name:
        return query_params_sentiment
    elif "gold" in dataset_name:
        return query_params_gold
    elif "summary" in dataset_name:
        return query_params_summary
    else:
        raise ValueError(f"Unknown dataset: {dataset_name}")

def find_majority(responses, dataset_name):
    if "sentiment" in dataset_name: # list of strings
        return find_majority_str(responses)
    elif "gold" in dataset_name: # list of dicts
        return find_majority_dict(responses)
    elif "summary" in dataset_name: # list of strings
        return find_majority_str(responses)

def find_majority_str(responses):
    """
    Find the majority value in a list of strings.

    Args:
        responses (list): List of strings to analyze.

    Returns:
        str: The majority string.
    """
    counter = Counter(responses)
    majority = counter.most_common(1)[0]
    if majority[1] > len(responses) / 2:
        return majority[0]
    else:
        return random.choice(responses)

def find_majority_dict(responses):
    """
    Find the majority value in each key of a list of dictionaries.

    Args:
        responses (list): List of dictionaries to analyze.

    Returns:
        dict: Dictionary with the values of the majority for each key.
    """
    # Initialize a dictionary to hold the majority values
    majority_dict = {}
    
    # Iterate through each dictionary in the list
    for response in responses:
        for key, value in response.items():
            if key not in majority_dict:
                majority_dict[key] = []
            majority_dict[key].append(value)
    
    # Find the majority for each key
    for key, values in majority_dict.items():
        counter = Counter(values)
        majority = counter.most_common(1)[0]
        if majority[1] > len(values) / 2:
            majority_dict[key] = majority[0]
        else:
            majority_dict[key] = random.choice(values)
    
    return majority_dict

def clean_llm_output(dataset_name, text: str):
    if "sentiment" in dataset_name:
        return clean_llm_output_sentiment(text)
    elif "gold" in dataset_name:
        return clean_llm_output_gold(text)
    elif "summary" in dataset_name:
        return clean_llm_output_summary(text)

def clean_llm_output_summary(text: str):
    """
    Clean LLM generated text by removing headlines and bullet point markers.
    
    Args:
        text (str): The text to clean
        
    Returns:
        str: The cleaned text with headlines and bullet point markers removed
    """

    # Remove lines with numeric bullet point references (e.g., "5 bullet points")
    cleaned_text = re.sub(r'(?im)^.*?\d+\s+bullet points?.*$', '', text)

    # Remove headline patterns that typically introduce bullet points
    # Handle headlines ending with either period or colon
    cleaned_text = re.sub(r'(?im)^.*?(bullet points?|summary|summariz|key points|important facts).*?(?:\.|\:|\n)', '', cleaned_text)
    
    # Remove any markdown headers (lines starting with #)
    cleaned_text = re.sub(r'(?m)^#+\s+.*$', '', cleaned_text)
    
    # Remove asterisks or hyphens at the start of lines
    cleaned_text = re.sub(r'(?m)^\s*[\*\-•]\s*', '', cleaned_text)

    # Remove enumerated list items like "1. ..." at the start of a line
    cleaned_text = re.sub(r'(?m)^\s*\d+\.\s+', '', cleaned_text)

    # Remove any backticks or code blocks
    cleaned_text = re.sub(r'`+', '', cleaned_text)

    # Remove anything in the response after these phrases
    phrases_remove_all_after = ["Here is the response in the correct format:"]
    for phrase in phrases_remove_all_after:
        cleaned_text = re.sub(rf'(?i){re.escape(phrase)}.*\Z', '', cleaned_text, flags=re.DOTALL)

    # Remove anything in a line after these phrases
    phrases_remove_line = [
        "(Note:"
    ]
    for phrase in phrases_remove_line:
        cleaned_text = re.sub(rf'(?i){re.escape(phrase)}.*$', '', cleaned_text, flags=re.DOTALL)

    # Remove specific phrases that are common in LLM outputs of summaries
    phrases_remove = [
        "i hope it is correct", 
        "please let me know if", 
        "(Note: I added the last point as it was not in the format you requested)", 
        "Here is the corrected response:", 
        "(Note: I added the last point as it was not in", 
        "I hope this is what you were looking for.", 
        "$0.00",
        "$$",
        "Here is the corrected response:",
        ]
    for phrase in phrases_remove:
        cleaned_text = re.sub(rf'(?i){re.escape(phrase)}', '', cleaned_text)
    
    # Clean up extra whitespace and normalize newlines
    cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)  # Replace 3+ newlines with 2
    cleaned_text = cleaned_text.strip()  # Remove leading/trailing whitespace
    
    return cleaned_text

def clean_llm_output_sentiment(text: str):
    """
    Cleans the output of a language model and extracts sentiment.

    Args:
        text (str): The text to clean.

    Returns:
        int: The sentiment label as integer (0=negative, 1=neutral, 2=positive) or -1 for invalid
    """
    # Define mapping for consistent return types
    mapping = {
        "negative": 0,
        "neutral": 1,
        "positive": 2
    }

    if text is None or text == "":
        print("Received None text. Marking as invalid (-1).")
        return -1
    
    text = text.strip().lower()
    
    # Look for exact matches
    if text in mapping.keys():
        return mapping[text]
    
    words_found = []
    
    for word in mapping.keys():
        words_found.extend([word] * len(re.findall(word, text)))
    
    if not words_found:
        print("No valid sentiment found. Marking as invalid (-1).")
        return -1
    
    # If multiple sentiment words, find the majority
    majority_word = find_majority(words_found, dataset_name="sentiment")
    return mapping[majority_word]


def clean_llm_output_gold(input_data):
    """
    Process various input formats containing financial sentiment data and return a standardized dictionary.
    
    Args:
        input_data: Dictionary, string, or other format containing sentiment analysis results
        
    Returns:
        dict: Dictionary with all expected keys and validated values (0, 1, or -1 for errors)
    """
    # Define the expected keys
    expected_keys = [
        "price_or_not", 
        "price_up", 
        "price_const_stable", 
        "price_down", 
        "past_price_info", 
        "future_price_info", 
        "past_gen_info", 
        "future_gen_info", 
        "asset_comparison"
    ]
    
    # Initialize result dictionary with all keys set to -1
    result = {key: -1 for key in expected_keys}
    
    # Parse input to dictionary if it's not already
    parsed_data = {}
    
    if isinstance(input_data, dict):
        parsed_data = input_data
    elif isinstance(input_data, str):
        # Remove markdown code blocks if present
        clean_input = re.sub(r'```(?:json|python)?\s*|\s*```', '', input_data)
        
        # Try to find and extract JSON-like structure from text
        json_pattern = r'(?:\{|\[).*?(?:\}|\])'
        json_matches = re.findall(json_pattern, clean_input, re.DOTALL)
        
        if json_matches:
            # Try each potential JSON match
            for json_str in json_matches:
                try:
                    candidate = json.loads(json_str)
                    if isinstance(candidate, dict) and any(key in candidate for key in expected_keys):
                        parsed_data = candidate
                        break
                except json.JSONDecodeError:
                    continue
        
        # If no valid JSON was found, try direct parsing
        if not parsed_data:
            try:
                parsed_data = json.loads(clean_input)
            except json.JSONDecodeError:
                # Try to parse Python dict syntax
                try:
                    # Replace single quotes with double quotes for JSON parsing
                    clean_input = clean_input.replace("'", '"')
                    parsed_data = json.loads(clean_input)
                except json.JSONDecodeError:
                    # Try to extract key-value pairs using regex
                    pairs = re.findall(r'"?(\w+)"?\s*:\s*(-?\d+)', clean_input)
                    parsed_data = {key: int(value) for key, value in pairs}
    
    # Update result with valid values from parsed data
    for key in expected_keys:
        if key in parsed_data:
            # Ensure value is 0 or 1, otherwise set to -1
            if parsed_data[key] in [0, 1]:
                result[key] = parsed_data[key]
    
    return result