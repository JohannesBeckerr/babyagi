#!/usr/bin/env python3
import os
import time
import argparse
import importlib
import openai
import pinecone
from collections import deque
from typing import Dict, List
from dotenv import load_dotenv

#Set Variables
load_dotenv()

# Engine configuration

# API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
assert OPENAI_API_KEY, "OPENAI_API_KEY environment variable is missing from .env"

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
assert PINECONE_API_KEY, "PINECONE_API_KEY environment variable is missing from .env"

PINECONE_ENVIRONMENT = os.getenv("PINECONE_ENVIRONMENT", "us-east1-gcp")
assert PINECONE_ENVIRONMENT, "PINECONE_ENVIRONMENT environment variable is missing from .env"

# Table config
YOUR_TABLE_NAME = os.getenv("TABLE_NAME", "")
assert YOUR_TABLE_NAME, "TABLE_NAME environment variable is missing from .env"

# Run configuration

parser = argparse.ArgumentParser(
    add_help=False,
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
examples:
 * start solving world hunger by creating initial list of tasks using GPT-4:
     %(prog)s -t "Create initial list of tasks" -4 Solve world hunger
 * join the work on solving world hunger using GPT-3:
     %(prog)s -j Solve world hunger
"""
)
parser.add_argument('objective', nargs='*', metavar='<objective>', help='''
main objective description. Doesn\'t need to be quoted.
if not specified, get OBJECTIVE from environment.
''', default=[os.getenv("OBJECTIVE", "")])
parser.add_argument('-n', '--name', required=False, help='''
babyagi instance name.
if not specified, get BABY_NAME from environment.
''', default=os.getenv("BABY_NAME", "BabyAGI"))
group = parser.add_mutually_exclusive_group()
group.add_argument('-t', '--task', metavar='<initial task>', help='''
initial task description. must be quoted.
if not specified, get INITIAL_TASK from environment.
''', default=os.getenv("INITIAL_TASK", os.getenv("FIRST_TASK", "")))
group.add_argument('-j', '--join', action='store_true', help='''
join an existing objective.
install cooperative requirements.
''')
parser.add_argument('-4', '--gpt-4', dest='use_gpt4', action='store_true', help='''
use GPT-4 instead of GPT-3
''')
parser.add_argument('-h', '-?', '--help', action='help', help='''
show this help message and exit
''')

args = parser.parse_args()

BABY_NAME = args.name
if not BABY_NAME:
    print("\033[91m\033[1m"+"BabyAGI instance name missing\n"+"\033[0m\033[0m")
    parser.print_help()
    parser.exit()

def can_import(module_name):
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False

module_name = "ray"
JOIN_EXISTING_OBJECTIVE = args.join
if JOIN_EXISTING_OBJECTIVE and not can_import(module_name):
    print("\033[91m\033[1m"+f"Package {module_name} not installed\nInstall:  pip install -r requirements-cooperative.txt\n"+"\033[0m\033[0m")
    parser.print_help()
    parser.exit()

USE_GPT4 = args.use_gpt4

OBJECTIVE = ' '.join(args.objective).strip()
if not OBJECTIVE:
    print("\033[91m\033[1m"+"No objective specified or found in environment.\n"+"\033[0m\033[0m")
    parser.print_help()
    parser.exit()

INITIAL_TASK = args.task
if not INITIAL_TASK and not JOIN_EXISTING_OBJECTIVE:
    print("\033[91m\033[1m"+"No initial task specified or found in environment.\n"+"\033[0m\033[0m")
    parser.print_help()
    parser.exit()

print("\033[95m\033[1m"+"\n*****CONFIGURATION*****\n"+"\033[0m\033[0m")
print(f"Name: {BABY_NAME}")
print(f"LLM: {'GPT-4' if USE_GPT4 else 'GPT-3'}")

if USE_GPT4:
    print("\033[91m\033[1m"+"\n*****USING GPT-4. POTENTIALLY EXPENSIVE. MONITOR YOUR COSTS*****"+"\033[0m\033[0m")

print("\033[94m\033[1m"+"\n*****OBJECTIVE*****\n"+"\033[0m\033[0m")
print(f"{OBJECTIVE}")

if not JOIN_EXISTING_OBJECTIVE: print("\033[93m\033[1m"+"\nInitial task:"+"\033[0m\033[0m"+f" {INITIAL_TASK}")
else: print("\033[93m\033[1m"+f"\nJoining to help the objective"+"\033[0m\033[0m")

# Configure OpenAI and Pinecone
openai.api_key = OPENAI_API_KEY
pinecone.init(api_key=PINECONE_API_KEY, environment=PINECONE_ENVIRONMENT)

# Create Pinecone index
table_name = YOUR_TABLE_NAME
dimension = 1536
metric = "cosine"
pod_type = "p1"
if table_name not in pinecone.list_indexes():
    pinecone.create_index(table_name, dimension=dimension, metric=metric, pod_type=pod_type)

# Connect to the index
index = pinecone.Index(table_name)

# Task list
task_list = deque([])

def add_task(task: Dict):
    task_list.append(task)

def get_ada_embedding(text):
    text = text.replace("\n", " ")
    return openai.Embedding.create(input=[text], model="text-embedding-ada-002")["data"][0]["embedding"]

def openai_call(prompt: str, use_gpt4: bool = False, temperature: float = 0.5, max_tokens: int = 100):
    if not use_gpt4:
        #Call GPT-3 DaVinci model
        response = openai.Completion.create(
            engine='text-davinci-003',
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0
        )
        return response.choices[0].text.strip()
    else:
        #Call GPT-4 chat model
        messages=[{"role": "user", "content": prompt}]
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages = messages,
            temperature=temperature,
            max_tokens=max_tokens,
            n=1,
            stop=None,
        )
        return response.choices[0].message.content.strip()

def task_creation_agent(objective: str, result: Dict, task_description: str, task_list: List[str], gpt_version: str = 'gpt-3'):
    prompt = f"You are an task creation AI that uses the result of an execution agent to create new tasks with the following objective: {objective}, The last completed task has the result: {result}. This result was based on this task description: {task_description}. These are incomplete tasks: {', '.join(task_list)}. Based on the result, create new tasks to be completed by the AI system that do not overlap with incomplete tasks. Return the tasks as an array."
    response = openai_call(prompt, USE_GPT4)
    new_tasks = response.split('\n')
    return [{"task_name": task_name} for task_name in new_tasks]

def prioritization_agent(this_task_id:int, gpt_version: str = 'gpt-3'):
    global task_list
    task_names = [t["task_name"] for t in task_list]
    next_task_id = int(this_task_id)+1
    prompt = f"""You are an task prioritization AI tasked with cleaning the formatting of and reprioritizing the following tasks: {task_names}. Consider the ultimate objective of your team:{OBJECTIVE}. Do not remove any tasks. Return the result as a numbered list, like:
    #. First task
    #. Second task
    Start the task list with number {next_task_id}."""
    response = openai_call(prompt, USE_GPT4)
    new_tasks = response.split('\n')
    task_list = deque()
    for task_string in new_tasks:
        task_parts = task_string.strip().split(".", 1)
        if len(task_parts) == 2:
            task_id = task_parts[0].strip()
            task_name = task_parts[1].strip()
            task_list.append({"task_id": task_id, "task_name": task_name})

def execution_agent(objective:str,task: str, gpt_version: str = 'gpt-3') -> str:
    #context = context_agent(index="quickstart", query="my_search_query", n=5)
    context=context_agent(index=YOUR_TABLE_NAME, query=objective, n=5)
    #print("\n*******RELEVANT CONTEXT******\n")
    #print(context)
    prompt =f"You are an AI who performs one task based on the following objective: {objective}.\nTake into account these previously completed tasks: {context}\nYour task: {task}\nResponse:"
    return openai_call(prompt, USE_GPT4, 0.7, 2000)

def context_agent(query: str, index: str, n: int):
    query_embedding = get_ada_embedding(query)
    index = pinecone.Index(index_name=index)
    results = index.query(query_embedding, top_k=n,
    include_metadata=True)
    #print("***** RESULTS *****")
    #print(results)
    sorted_results = sorted(results.matches, key=lambda x: x.score, reverse=True)    
    return [(str(item.metadata['task'])) for item in sorted_results]

# Add the first task
first_task = {
    "task_id": 1,
    "task_name": INITIAL_TASK
}

add_task(first_task)
# Main loop
task_id_counter = 1
while True:
    if task_list:
        # Print the task list
        print("\033[96m\033[1m"+"\n*****TASK LIST*****\n"+"\033[0m\033[0m")
        for t in task_list:
            print(str(t['task_id'])+": "+t['task_name'])

        # Step 1: Pull the first task
        task = task_list.popleft()
        print("\033[92m\033[1m"+"\n*****NEXT TASK*****\n"+"\033[0m\033[0m")
        print(str(task['task_id'])+": "+task['task_name'])

        # Send to execution function to complete the task based on the context
        result = execution_agent(OBJECTIVE,task["task_name"])
        this_task_id = int(task["task_id"])
        print("\033[93m\033[1m"+"\n*****TASK RESULT*****\n"+"\033[0m\033[0m")
        print(result)

        # Step 2: Enrich result and store in Pinecone
        enriched_result = {'data': result}  # This is where you should enrich the result if needed
        result_id = f"result_{task['task_id']}"
        vector = enriched_result['data']  # extract the actual result from the dictionary
        index.upsert([(result_id, get_ada_embedding(vector),{"task":task['task_name'],"result":result})])

        # Step 3: Create new tasks and reprioritize task list
        new_tasks = task_creation_agent(OBJECTIVE,enriched_result, task["task_name"], [t["task_name"] for t in task_list])

        for new_task in new_tasks:
            task_id_counter += 1
            new_task.update({"task_id": task_id_counter})
            add_task(new_task)
        prioritization_agent(this_task_id)

    time.sleep(1)  # Sleep before checking the task list again
