import time
import torch
from transformers import AutoTokenizer
from inference.generate import *
from model.llama_tree_attn import LlamaForCausalLM
import datasets
from tqdm import tqdm
import gc
import csv
import random

target_model_names = ["lmsys/vicuna-7b-v1.5", "meta-llama/Llama-2-7b-chat-hf"]
draft_model_names = ['TinyLlama/TinyLlama-1.1B-Chat-v1.0', 'JackFram/llama-68m']
dataset_names = ["mt_bench", "alpaca", "trivia_qa"]
sampling_methods = ["probabilistic"]

max_new_tokens = 200
replacement = False
speculative_sampling = True
tree_attn = True

def mcsd_inference(target_model_name, draft_model_name, prompts, max_new_tokens=max_new_tokens, draft_model_temp = 0.7, target_model_temp = 0.7,replacement=replacement, speculative_sampling=speculative_sampling, tree_attn=tree_attn):
    total_token_length = 0
    acceptance_count = 0
    draft_token_count = 0
    k_config = (4,2,2,1)
    draft_model = LlamaForCausalLM.from_pretrained(
        draft_model_name,
        torch_dtype=torch.float32,
    ).to('cuda')
    target_model = LlamaForCausalLM.from_pretrained(
        target_model_name,
        torch_dtype=torch.bfloat16,
    ).to('cuda')
    draft_model.eval()
    target_model.eval()

    tokenizer = AutoTokenizer.from_pretrained(target_model_name)
    generator = SpeculativeGenerator(
        draft_model,
        target_model,
        eos_token_id=tokenizer.eos_token_id,
        k_config=k_config,
        max_new_tokens=max_new_tokens,
        replacement=replacement,
        speculative_sampling=speculative_sampling,
        draft_model_temp=draft_model_temp,
        target_model_temp=target_model_temp,
        tree_attn=tree_attn,
    )
    start_time = time.time()
    with torch.no_grad():
        for prompt_text in tqdm(prompts):
            inputs = tokenizer(prompt_text, return_tensors="pt").to("cuda")
            input_ids = inputs.input_ids
            output = generator.generate(input_ids)
            output_text = tokenizer.batch_decode(
                output.sequences, skip_special_tokens=True
            )
            print(output_text)
            total_token_length += output.sequences.shape[1] - input_ids.shape[1]
            acceptance_count += output.acceptance_count
            draft_token_count += output.draft_token_count
            torch.cuda.empty_cache()
            del inputs

    end_time = time.time()
    run_time = end_time - start_time
    token_per_second = total_token_length / run_time
    acceptance_rate = acceptance_count / draft_token_count
    print(f"speed is {token_per_second}")
    print(f"acceptance rate is {acceptance_rate}")
    # average_longest_acc_length = [sum(path)/len(path) for path in generator.stats]
    torch.cuda.empty_cache()
    del draft_model
    del target_model
    del tokenizer
    gc.collect()
    return token_per_second, acceptance_rate

for dataset_name in dataset_names:
    prompts = []
    if dataset_name == "mt_bench":
        dataset = datasets.load_dataset("HuggingFaceH4/mt_bench_prompts", split="train")
        prompts = [" ".join(example['prompt']) for example in dataset]
    elif dataset_name == "alpaca":
        dataset = datasets.load_dataset("yahma/alpaca-cleaned", split="train")
        prompts = []
        for example in dataset:
            if len(example['input']) != 0:
                prompt = "Question: " + " ".join([example['instruction'], example["input"]]).strip() + " Answer: "
                prompts.append(prompt)
            else:
                prompt = "Answer the following question: " + example['instruction']
                prompts.append(prompt)
        prompts = random.sample(prompts, 250)
    elif dataset_name == "trivia_qa":
        dataset = datasets.load_dataset("mandarjoshi/trivia_qa", "rc.nocontext", split="test")
        prompts = []
        for example in dataset:
            prompt = "Answer the following question: " + example['question'] + " Write your answer here: "
            prompts.append(prompt)
        prompts = random.sample(prompts, 250)
    
    for sampling_method in sampling_methods:
        csv_data = {}
        for target_model in target_model_names:
            for draft_model in draft_model_names:
                print(f"Dataset: {dataset_name}")
                print(f"Target model: {target_model}, Draft model: {draft_model}")
                print(f"Sampling method: {sampling_method}")
                token_per_second, acc_rate = mcsd_inference(target_model_name=target_model, 
                                                            draft_model_name=draft_model, 
                                                            prompts=prompts, 
                                                            draft_model_temp=0.7, 
                                                            target_model_temp=0.7)
                csv_data[(draft_model, target_model)] = [token_per_second, acc_rate]
                print(f"Tokens per second: {token_per_second}")
                print(f"Acceptance rate: {acc_rate}")
        with open(f'mcsd_inference_{dataset_name}_{sampling_method}_4221.csv', mode='w') as file:
            writer = csv.writer(file)
            for key, value in csv_data.items():
                writer.writerow([key[0], key[1], value[0], value[1]])