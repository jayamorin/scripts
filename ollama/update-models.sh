#!/bin/bash

for x in qwen3.6:35b-a3b-q4_K_M qwen3-coder:30b-a3b-q4_K_M 
do
    echo "Updating base model ${x}"
    ollama pull ${x}
done

for x in qwen3-coder:custom qwen3.6:custom
do
    echo "Rebuilding custom model ${x}"
    ollama create ${x} -f ${x}.modelfile
done

brew services restart ollama
